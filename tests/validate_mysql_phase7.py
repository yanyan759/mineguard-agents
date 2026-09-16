"""阶段 7 前置条件：MySQL 数据环境验收脚本。

依赖：deploy/mysql 的 Docker MySQL 已启动且 agent1/.env 配置 MYSQL_*（host=localhost,
port=3307, password=agent1_dev, database=coal_mine_db）。

验证项：
1. MySQL 真实连接成功
2. 6 张业务表存在（accident_cases/personnel/materials_inventory/equipment/work_orders/role_permissions）
3. 种子数据行数符合预期
4. 只读白名单：DELETE/UPDATE/INSERT/DROP 语句被工具层拒绝
5. 真实 SELECT 查询返回数据
6. 角色权限表 + mysql_bridge 数据源（load_available_resources / load_role_permissions）

用法：
    cd agent1
    python tests/validate_mysql_phase7.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# 统一结果收集
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项检查结果。"""
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# 预期表与最小行数（来自 deploy/mysql/sql 种子数据）
EXPECTED_TABLES = {
    "accident_cases": 5,           # 从 fixture YAML 派生
    "personnel": 7,                # 手工构造排班
    "materials_inventory": 8,      # 手工构造库存
    "equipment": 6,                # 手工构造设备
    "work_orders": 0,              # 空表，工单随工作流写入
    "role_permissions": 7,         # 权限矩阵 V2：7 角色（config/permission_matrix.py 权威源）
}


def test_connection() -> bool:
    """MySQL 真实连接。"""
    import mysql.connector

    from app.tools.db_tools import _get_db_config

    config = _get_db_config()
    try:
        conn = mysql.connector.connect(**config)
    except Exception as e:
        check("连接成功", False, f"{e}")
        return False
    try:
        check("连接成功", True, f"{config.get('host')}:{config.get('port')}/{config.get('database')}")
        return True
    finally:
        conn.close()


def test_tables_and_rows(conn) -> None:
    """5 张表存在 + 种子数据行数。"""
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = {row[0] for row in cursor.fetchall()}
    for table, min_rows in EXPECTED_TABLES.items():
        check(f"表存在: {table}", table in tables)
        if table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            count = cursor.fetchone()[0]
            check(f"表 {table} 行数 ≥ {min_rows}", count >= min_rows, f"实际 {count}")
    cursor.close()


def test_readonly_whitelist() -> None:
    """只读白名单：DML 语句被工具层拒绝。"""
    from app.tools.db_tools import execute_sql_query, list_sql_tables

    dml_examples = {
        "DELETE FROM accident_cases WHERE case_id='case-2023-001'": "DELETE",
        "UPDATE personnel SET status='rest'": "UPDATE",
        "INSERT INTO work_orders (action) VALUES ('test')": "INSERT",
        "DROP TABLE accident_cases": "DROP",
        "ALTER TABLE accident_cases ADD COLUMN x INT": "ALTER",
    }
    for sql, kw in dml_examples.items():
        result = execute_sql_query.invoke({"query": sql})
        check(f"只读拒绝 {kw}", result["status"] == "rejected", result.get("error", ""))

    # 合法只读查询应放行（不要求数据库有数据，离线也应走到查询或降级）
    result = list_sql_tables.invoke({})
    check("SHOW TABLES 放行", result["status"] in ("success", "error"),
          f"status={result['status']}")


def test_real_select(conn) -> None:
    """真实 SELECT 查询（验证中文/JSON 列读取）。"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT case_id, location, risk_level, causes FROM accident_cases "
        "WHERE risk_level='red' LIMIT 2"
    )
    rows = cursor.fetchall()
    check("SELECT 红警案例", len(rows) >= 1, f"返回 {len(rows)} 行")
    if rows:
        # 乱码真实性校验：UTF-8 中文被 latin1 误解码会出现 ç/¤/é/å 等扩展字符。
        # 正确数据应同时满足：①location 含矿名（WW/YY/XX 等缩写前缀 + 中文）；
        # ②字段中含汉字（U+4E00-U+9FFF）；③不含 Latin-1 乱码特征字符。
        loc = rows[0][1]
        causes = rows[0][3]
        has_han = bool(re.search(r"[一-鿿]", loc))
        has_latin1_garbled = bool(re.search(r"[-ÿ]", loc))
        check("中文内容读取（含真实性校验）", has_han and not has_latin1_garbled,
              f"{rows[0][0]} / {loc[:14]}…")
        # JSON 列 causes 也应含汉字且无乱码特征
        causes_ok = bool(re.search(r"[一-鿿]", causes)) and not bool(
            re.search(r"[-ÿ]", causes))
        check("JSON causes 中文无乱码", causes_ok, f"{causes[:20]}…")
    cursor.close()


def test_role_permissions_and_bridge() -> None:
    """角色权限表内容 + mysql_bridge 数据源（主链消费通道）。

    role_permissions 是权限校验工具的数据源；load_available_resources /
    load_role_permissions 是资源评估/权限校验的 MySQL 主链消费入口。
    """
    from app.services import mysql_bridge

    # 1. role_permissions 表内容：6 角色权限可经桥接层解析
    perms = mysql_bridge.load_role_permissions("矿总工程师")
    check("load_role_permissions 返回权限列表",
          perms is not None and "approve_plan" in perms,
          f"{perms}")

    # 2. load_available_resources 三类资源分组（personnel/materials/equipment）
    avail = mysql_bridge.load_available_resources()
    ok_group = isinstance(avail, dict) and {"personnel", "support_materials", "equipment"} <= set(avail)
    check("load_available_resources 三类资源",
          ok_group and sum(avail.get("personnel", {}).values()) >= 1,
          f"personnel={len(avail.get('personnel', {}))} 角色组" if ok_group else f"{avail}")

    # 3. 权限校验工具优先读 MySQL（permission_source=mysql）
    from app.tools.permission_check_tool import check_permission

    r = check_permission.invoke({"user_role": "支护工程师", "requested_action": "execute_plan"})
    check("权限工具 permission_source=mysql",
          r["data"].get("permission_source") == "mysql",
          f"source={r['data'].get('permission_source')}, passed={r['data'].get('passed')}")

    # 4. 工单写入通道存在（结构：create_work_orders 可调用，离线返回 error 不抛异常）
    wo = mysql_bridge.create_work_orders(
        [{"action": "加密支护", "owner_role": "支护工程师", "deadline_minutes": 30}],
        workflow_run_id="test-run", coordination_id="test-coord",
    )
    check("create_work_orders 可调用", wo.get("status") in ("success", "error"),
          f"status={wo.get('status')}")


def test_anchor_traceability() -> None:
    """锚点溯源（SA1-SA6 高仿真锚定，HALLUCINATION 清零）。

    需求口径（docs/真实资料爬取与整理要求清单.md §4.2/§4.4/§7）：真实排班/库存/
    台账不可爬，仿真种子以公开锚点升级为「高仿真锚定」，产出《锚点溯源表》
    （每个种子值 → 锚点来源），全部标注 synthetic_but_anchored=true。
    """
    from app.config.anchor_refs import ANCHOR_REFS, ANCHOR_BY_CATEGORY, find_anchor

    # 1. 锚点权威源：6 类齐全，每类 ≥5 条，全部 synthetic_but_anchored=true
    check("锚点权威源 6 类齐全",
          set(ANCHOR_BY_CATEGORY) == {"SA1", "SA2", "SA3", "SA4", "SA5", "SA6"},
          f"{sorted(ANCHOR_BY_CATEGORY)}")
    per_cat = {k: len(v) for k, v in ANCHOR_BY_CATEGORY.items()}
    check("每类锚点 ≥5 条", all(n >= 5 for n in per_cat.values()), f"{per_cat}")
    check("全部 synthetic_but_anchored=true",
          all(a["synthetic_but_anchored"] for a in ANCHOR_REFS),
          f"{len(ANCHOR_REFS)} 条")

    # 2. 关键角色/物资溯源命中（role 词表对齐，防命名漂移静默破坏资源核算）
    hits = find_anchor("矿总工程师")
    check("SA4 组合锚点命中 矿总工程师", any(a["anchor_id"] == "SA4-005" for a in hits),
          f"{[a['anchor_id'] for a in hits]}")
    hits = find_anchor("锚索")
    check("物资锚索可溯源", len(hits) >= 2, f"{len(hits)} 个锚点")
    hits = find_anchor("单体液压支柱")
    check("设备单体液压支柱可溯源",
          any(a["anchor_id"] == "SA2-001" for a in hits),
          f"{[a['anchor_id'] for a in hits]}")

    # 3. 溯源表已产出（任务成果/04_锚定素材/锚点溯源表.csv），每行标真
    from pathlib import Path
    import csv as _csv

    trace_csv = AGENT1_ROOT.parent / "任务成果" / "04_锚定素材" / "锚点溯源表.csv"
    check("《锚点溯源表》已产出", trace_csv.exists(), str(trace_csv))
    if trace_csv.exists():
        with trace_csv.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(_csv.DictReader(fh))
        check("溯源表行数 ≥ 种子值数", len(rows) >= 20, f"{len(rows)} 行")
        check("溯源表全部 synthetic_but_anchored=true",
              rows and all(r["synthetic_but_anchored"] == "true" for r in rows))
        # 至少部分种子值命中真实锚点 URL（高仿真锚定而非全空）
        with_url = sum(1 for r in rows if r["url"])
        check("溯源表命中真实锚点 URL", with_url >= 10, f"{with_url} 行带 URL")

    # 4. 资源核算工具输出带锚点溯源（消费链闭环）
    from app.tools.resource_accounting_tool import calculate_gaps

    r = calculate_gaps.invoke({
        "requirements": {"materials": [{"name": "锚索", "count": 100, "unit": "根"}]},
        "available_resources": {"materials": [{"name": "锚索", "count": 80, "unit": "根", "status": "available"}]},
    })
    d = r["data"]
    check("资源核算 data_provenance=synthetic_but_anchored",
          d.get("data_provenance") == "synthetic_but_anchored",
          f"{d.get('data_provenance')}")
    check("资源核算 anchored_references 非空",
          len(d.get("anchored_references", [])) >= 1,
          f"{len(d.get('anchored_references', []))} 条溯源")


def main() -> int:
    """执行阶段 7 MySQL 验收。"""
    print("=" * 50)
    print("  阶段 7：MySQL 数据环境验收")
    print("=" * 50)

    if not test_connection():
        print("\n[说明] 连接失败，请先：")
        print("  1. cd agent1/deploy/mysql && docker compose up -d")
        print("  2. 确认 agent1/.env 中 MYSQL_* 与 deploy/mysql/.env 一致")
        print("\n验证结果: 1 FAIL / 0 PASS")
        return 1

    import mysql.connector

    from app.tools.db_tools import _get_db_config

    conn = mysql.connector.connect(**_get_db_config())
    try:
        test_tables_and_rows(conn)
        test_readonly_whitelist()
        test_real_select(conn)
        test_role_permissions_and_bridge()
        test_anchor_traceability()
    finally:
        conn.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n验证结果: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
