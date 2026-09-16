"""
MySQL 数据库只读查询工具模块

提供煤矿顶板灾变领域的只读查询能力：
- SQL 白名单模式：只允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH
- 表名白名单参数校验（防止 SQL 注入）
- 返回行数上限 500 行
- 返回值统一为结构化字典 {status, data, error, audit}

运行时能力：
- 跨平台查询超时：Windows 无 signal.alarm，改用 ThreadPoolExecutor + Future.result(timeout)
- 连接池：mysql.connector.pooling.MySQLConnectionPool，复用连接降低建连开销
- 断线重连：连接失败后按 _MAX_RETRIES 次重试
"""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Optional

from mysql.connector import pooling

from langchain_core.tools import tool

# 允许的安全 SQL 语句类型（只读白名单）
_ALLOWED_SQL_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH")
# 被禁止的 SQL 关键字（防止误调用或提示词绕过）
_FORBIDDEN_SQL_KEYWORDS = (
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
    "TRUNCATE", "RENAME", "REPLACE", "GRANT", "REVOKE",
)
# 单次查询返回行数上限
_MAX_ROWS = 500

# ── 跨平台超时 + 连接池 + 重连配置 ──
_QUERY_TIMEOUT_SECONDS = 30   # 单次查询超时阈值
_POOL_SIZE = 5                # 连接池大小
_MAX_RETRIES = 1              # 断线重连次数
# 查询统一在线程池中执行：Future.result(timeout) 在所有平台（含 Windows）均生效，
# 替代在 Windows 上无实际作用的 signal.alarm 超时方案
_QUERY_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db_query")

_pool: Optional[pooling.MySQLConnectionPool] = None
_pool_lock = threading.Lock()


def _validate_sql_readonly(sql: str) -> tuple[bool, str]:
    """校验 SQL 语句是否为只读查询。

    返回 (is_valid, reason)。
    规则覆盖常见的 SQL 注入和写操作模式。
    """
    cleaned = sql.strip().rstrip(";").strip()
    # 移除多余空白用于关键字检查
    upper = cleaned.upper()

    # 多语句检测（分号分隔，WITH 除外）
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        return False, "禁止执行多条 SQL 语句"

    # 检查是否以允许的前缀开头
    if not any(upper.startswith(prefix) for prefix in _ALLOWED_SQL_PREFIXES):
        return False, (
            f"仅允许只读查询（SELECT/SHOW/DESCRIBE/EXPLAIN/WITH），"
            f"当前语句前缀不在白名单中"
        )

    # 检查是否包含禁止的关键字
    for keyword in _FORBIDDEN_SQL_KEYWORDS:
        # 使用单词边界匹配，避免误判（如 SELECT 中包含 ELE 不算）
        if re.search(rf"\b{keyword}\b", upper, re.IGNORECASE):
            return False, f"禁止执行 {keyword} 操作"

    return True, "ok"


def _build_result(
    status: str,
    data: Any = None,
    error: str | None = None,
    query: str | None = None,
) -> dict:
    """构建统一的结构化工具返回格式。"""
    return {
        "status": status,          # "success" | "rejected" | "error"
        "data": data,              # 查询结果数据
        "error": error,            # 错误信息（仅 status != success 时填充）
        "audit": {                 # 审计信息
            "tool": "db_query",
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def _get_db_config() -> dict:
    """从环境变量读取 MySQL 连接配置（内部函数）。"""
    from dotenv import load_dotenv
    load_dotenv()

    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "autocommit": True,
    }
    config = {k: v for k, v in config.items() if v is not None}
    return config


# ── 连接池 + 跨平台超时 ──


def _get_pool() -> Optional[pooling.MySQLConnectionPool]:
    """懒加载单例 MySQL 连接池。

    未配置必需连接项（host/database）时返回 None，工具层降级为离线提示。
    连接池采用惰性连接：创建时不会立即建连，首次 get_connection 才真正连接，
    因此池初始化失败只发生在配置非法时。
    """
    global _pool
    if _pool is not None:
        return _pool

    config = _get_db_config()
    if not config.get("host") or not config.get("database"):
        return None

    with _pool_lock:
        if _pool is None:
            try:
                _pool = pooling.MySQLConnectionPool(
                    pool_name="agent1_mysql",
                    pool_size=_POOL_SIZE,
                    **config,
                )
            except Exception:
                # 配置非法导致池创建失败 → 保持 None，后续调用继续走离线降级
                _pool = None
    return _pool


def _query_once(
    pool: pooling.MySQLConnectionPool,
    sql: str,
    params: Optional[tuple] = None,
) -> tuple[Optional[list], list]:
    """在连接池中执行一次只读查询，返回 (columns, rows)。

    单独封装以便在独立线程中执行（配合 Future.result(timeout) 实现跨平台超时）。
    查询失败时连接在 finally 中关闭，保证连接不会泄漏。
    """
    conn = pool.get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            desc = cursor.description
            if desc is None:
                return None, []
            columns = [d[0] for d in desc]
            rows = cursor.fetchall()
            return columns, [list(r) for r in rows]
    finally:
        conn.close()


def _execute_query(
    sql: str, params: Optional[tuple] = None
) -> tuple[Optional[list], list]:
    """跨平台超时 + 断线重连 的查询执行。

    - 超时：Windows 无 signal.alarm，改为线程池 Future.result(timeout)；
      超时抛出 TimeoutError 由调用方降级处理。
    - 重连：连接失败（如 MySQL 重启）后按 _MAX_RETRIES 次重试。

    :return: (columns, rows)；columns 为 None 表示无结果集
    """
    pool = _get_pool()
    if pool is None:
        raise ConnectionError("MySQL 未配置或连接池不可用（离线模式）")

    last_err: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            future = _QUERY_EXECUTOR.submit(_query_once, pool, sql, params)
            columns, rows = future.result(timeout=_QUERY_TIMEOUT_SECONDS)
            return columns, rows
        except FutureTimeoutError:
            raise TimeoutError(f"数据库查询超时（>{_QUERY_TIMEOUT_SECONDS}s）")
        except Exception as e:  # 连接中断/驱动异常等 → 重连重试
            last_err = e
            if attempt < _MAX_RETRIES:
                time.sleep(0.5)  # 短暂等待后重连
    raise last_err if last_err is not None else ConnectionError("查询失败")


def _run_query(sql: str, params: Optional[tuple] = None) -> dict:
    """执行只读查询并统一降级为结构化返回。

    内部函数：把 _execute_query 的异常路径收敛为 {status: "error"|"success"}。
    """
    try:
        columns, rows = _execute_query(sql, params)
        if columns is None:
            return _build_result("success", "查询执行成功，无结果集返回", query=sql)
        data = {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }
        return _build_result("success", data, query=sql)
    except Exception as e:
        # 离线 / 超时 / 连接失败 → 结构化降级返回，不中断 Agent 执行链路
        return _build_result("error", data=f"数据库查询失败", error=str(e), query=sql)


@tool
def list_sql_tables() -> dict:
    """查询当前数据库中所有可用表。

    只读操作，用于让 Agent 先识别真实可用的表名，
    方便后续预览表结构和编写自定义 SQL。

    返回值结构：
      {status: "success"|"error", data: "表名列表", error: None|"错误信息", audit: {...}}
    """
    sql = "SHOW TABLES"
    valid, reason = _validate_sql_readonly(sql)
    if not valid:
        return _build_result("rejected", error=reason, query=sql)

    try:
        columns, rows = _execute_query(sql)
        if not rows:
            return _build_result("success", "数据库当前无可用表", query=sql)
        table_names = [r[0] for r in rows]
        data = f"可用的表有：{', '.join(table_names)}"
        return _build_result("success", data, query=sql)
    except Exception as e:
        return _build_result(
            "error",
            data=f"{e} 请配置 .env 中的 MYSQL_* 环境变量并确保 MySQL 运行。",
            error=str(e),
            query=sql,
        )


@tool
def get_table_data(table_name: str, limit: int = 100) -> dict:
    """查询指定表的前 N 行数据。

    调用前应先通过 list_sql_tables 确认表名存在。
    表名仅允许字母/数字/下划线，防止 SQL 注入。

    :param table_name: 表名（仅允许 [a-zA-Z0-9_]）
    :param limit: 返回行数上限（默认 100，最大 500）
    """
    # 表名白名单校验：仅允许安全字符
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
        return _build_result(
            "rejected",
            error=f"非法表名：'{table_name}'，仅允许字母/数字/下划线",
            query=f"SELECT * FROM {table_name}",
        )

    limit = min(limit, _MAX_ROWS)
    sql = f"SELECT * FROM {table_name} LIMIT {limit}"
    try:
        columns, rows = _execute_query(sql)
        if columns is None or not rows:
            return _build_result(
                "success", f"数据表 {table_name} 暂无数据", query=sql
            )
        data = {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }
        return _build_result("success", data, query=sql)
    except Exception as e:
        return _build_result(
            "error",
            data=f"表 {table_name} 查询失败",
            error=str(e),
            query=sql,
        )


@tool
def execute_sql_query(query: str) -> dict:
    """执行只读 SQL 查询。

    调用前需通过 list_sql_tables 确认表名，通过 get_table_data 确认字段。
    仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 语句。
    返回行数上限 500 行。

    :param query: 只读 SQL 查询语句
    """
    if not query or not query.strip():
        return _build_result("rejected", error="SQL 查询语句不能为空")

    valid, reason = _validate_sql_readonly(query)
    if not valid:
        return _build_result("rejected", error=reason, query=query)

    # 注入行数限制（如未包含 LIMIT）
    upper = query.strip().upper()
    if upper.startswith("SELECT") and "LIMIT" not in upper:
        query = query.strip().rstrip(";") + f" LIMIT {_MAX_ROWS}"

    return _run_query(query)
