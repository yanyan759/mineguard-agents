"""MySQL 业务桥接模块（补齐：主执行链消费 MySQL 业务数据）。

统一封装六 Agent 工作流确定性模式对 MySQL 业务数据的**读写**通道，全部
函数同步（节点/工具均为同步调用），并复用 db_tools 的连接池与跨平台超时
（ThreadPoolExecutor + Future.result(timeout)）。

对外接口：
- load_available_resources()  资源评估节点读取当前可用人员/物资/设备
- create_work_orders(entries) 协同管控节点将处置动作写入 work_orders 工单
- load_role_permissions(role) 权限校验工具从 role_permissions 表读角色权限

离线降级策略（不中断工作流）：
- 连接池不可用（MYSQL_HOST/database 未配置）→ 返回 None / 空列表，调用方回退 fixture
- 查询/写入异常 → 结构化返回错误，调用方记录但继续执行
- work_orders 写入为专用 INSERT 通道，不经过 db_tools 的只读白名单（保持只读工具语义）
"""

from __future__ import annotations

from typing import Any, Optional


def _get_pool():
    """复用 db_tools 懒加载单例连接池；未配置/不可用返回 None（离线）。"""
    from app.tools.db_tools import _get_pool as _db_get_pool
    return _db_get_pool()


def _execute(sql: str, params: Optional[tuple] = None):
    """在连接池执行一条语句（复用 db_tools 的超时/重连/跨平台封装）。

    INSERT 无结果集时返回 (None, [])；连接池 autocommit=True 自动提交。
    """
    from app.tools.db_tools import _execute_query
    return _execute_query(sql, params)


def mysql_available() -> bool:
    """连接池是否可用（仅作标记：MySQL 数据源是否在线）。"""
    return _get_pool() is not None


def load_available_resources() -> Optional[dict[str, dict[str, float]]]:
    """读取当前可用资源（资源评估 Agent 确定性模式数据源）。

    从 personnel/materials_inventory/equipment 表读取，按资源类型分组，
    返回 {personnel: {角色: 在岗人数}, support_materials: {物资名: 可用量},
    equipment: {设备名: 台数}}。MySQL 不可达/异常 → 返回 None，
    调用方回退 fixture 资源快照（不中断工作流）。
    """
    pool = _get_pool()
    if pool is None:
        return None
    try:
        _, p_rows = _execute("SELECT role FROM personnel WHERE status = 'on_duty'")
        _, m_rows = _execute(
            "SELECT name, quantity FROM materials_inventory "
            "WHERE status = 'available'"
        )
        _, e_rows = _execute("SELECT name FROM equipment WHERE status = 'normal'")
    except Exception:
        # 查询失败（连接中断/表缺失）→ 离线降级，由调用方回退 fixture
        return None

    personnel: dict[str, float] = {}
    for (role,) in p_rows:
        if role:
            personnel[role] = personnel.get(role, 0.0) + 1.0

    # 同名设备和材料可能分散在多行，不能用字典推导覆盖前面的数量。
    support_materials: dict[str, float] = {}
    for name, quantity in m_rows:
        if name: support_materials[name] = support_materials.get(name, 0.0) + float(quantity)
    equipment: dict[str, float] = {}
    for (name,) in e_rows:
        if name: equipment[name] = equipment.get(name, 0.0) + 1.0

    return {
        "personnel": personnel,
        "support_materials": support_materials,
        "equipment": equipment,
    }


def scoped_inventory(mine_id: str):
    """旧表没有矿井字段：必须声明单矿部署范围，不能把全库数据当任意矿库存。"""
    import os
    from datetime import datetime, timezone
    if os.getenv("MYSQL_SCOPE_MINE_ID") != mine_id:
        raise ValueError("MySQL旧表未配置与当前矿井一致的单矿范围")
    result = load_available_resources()
    if result is None: raise ValueError("MySQL库存不可查询；未使用演示数据替代")
    import math
    if any(not math.isfinite(v) or v < 0 for group in result.values() for v in group.values()):
        raise ValueError("MySQL库存包含负数或非有限数量，不参与核算")
    observed = os.getenv("MYSQL_INVENTORY_OBSERVED_AT")
    if observed:
        parsed=datetime.fromisoformat(observed)
        if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
            raise ValueError("MySQL 台账观测时间必须带时区且不得在未来")
        observed=parsed.isoformat()
    return {"mine_id":mine_id, "source":"mysql_business_tables", "queried_at":datetime.now(timezone.utc).isoformat(),
            "observed_at":observed, "available_resources":result, "note":"观测时间由单矿台账维护者配置，查询时间独立记录"}


_WRITE_TIMEOUT_SECONDS = 30  # 单条 INSERT 超时（跨平台：线程池 Future.result）


def _insert_one(sql: str, params: tuple) -> int:
    """执行单条 INSERT 并返回自增主键（线程池内执行，跨平台超时）。

    复用 db_tools 的连接池与线程池；connection autocommit=True 自动提交。
    """
    from app.tools.db_tools import _QUERY_EXECUTOR

    pool = _get_pool()
    if pool is None:
        raise ConnectionError("MySQL 未配置或连接池不可用（离线）")

    def _run(p=pool, s=sql, ps=params) -> int:
        conn = p.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(s, ps)
                return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    future = _QUERY_EXECUTOR.submit(_run)
    return future.result(timeout=_WRITE_TIMEOUT_SECONDS)


def create_work_orders(
    entries: list[dict],
    workflow_run_id: str = "",
    coordination_id: str = "",
) -> dict:
    """批量写入处置工单（协同管控 Agent 下发后调用）。

    :param entries: 工单条目 [{action, owner_role, deadline_minutes}]
    :param workflow_run_id: 工作流运行 ID
    :param coordination_id: 协同管控 ID
    :return: {status, data: {inserted, work_order_ids}, error}
      写失败返回 status=error（调用方记录但不中断工作流）。
    """
    if not entries:
        return {"status": "success", "data": {"inserted": 0, "work_order_ids": []}, "error": None}
    if _get_pool() is None:
        return {
            "status": "error",
            "data": {"inserted": 0, "work_order_ids": []},
            "error": "MySQL 未配置或连接池不可用（离线），工单未落库",
        }

    sql = (
        "INSERT INTO work_orders "
        "(workflow_run_id, coordination_id, action, owner_role, deadline_minutes, status) "
        "VALUES (%s, %s, %s, %s, %s, 'pending')"
    )
    params = [
        (
            workflow_run_id or "",
            coordination_id or "",
            e.get("action", ""),
            e.get("owner_role", "") or None,
            int(e.get("deadline_minutes", 0) or 0) or None,
        )
        for e in entries
    ]
    try:
        work_order_ids = [_insert_one(sql, p) for p in params]
        return {
            "status": "success",
            "data": {"inserted": len(work_order_ids), "work_order_ids": work_order_ids},
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "data": {"inserted": 0, "work_order_ids": []},
            "error": str(exc),
        }


def load_role_permissions(role: str) -> Optional[list[str]]:
    """从 role_permissions 表读取角色权限列表（用户权限落库）。

    MySQL 可达且角色存在 → 返回权限列表；否则返回 None（调用方回退内置矩阵）。
    """
    pool = _get_pool()
    if pool is None:
        return None
    try:
        cols, rows = _execute(
            "SELECT permissions FROM role_permissions WHERE role = %s", (role,)
        )
    except Exception:
        return None
    if cols is None or not rows:
        return None
    perms = rows[0][0]
    if isinstance(perms, list):   # 驱动可能已解析 JSON
        return [str(p) for p in perms]
    if isinstance(perms, str):    # 未解析 → 手动解析
        import json
        try:
            parsed = json.loads(perms)
        except Exception:
            return None
        return [str(p) for p in parsed] if isinstance(parsed, list) else None
    return None
