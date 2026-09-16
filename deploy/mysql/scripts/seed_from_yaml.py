"""生成 MySQL 初始化数据 SQL（02_seed_data.sql）。

从 tests/fixtures/accident_cases.yaml 派生事故案例 INSERT；
人员/物资/设备为按字段结构手工构造的模拟种子（与本机真实排班无关）。

用法：
    cd agent1
    python deploy/mysql/scripts/seed_from_yaml.py    # 重新生成 deploy/mysql/sql/02_seed_data.sql

生成规则：
- 事故案例列表字段（causes/precursors/actions/lessons）以 JSON 列存储
- 日期字符串 'T' 替换为空格以符合 MySQL DATETIME 字面量
- INSERT 语句幂等（INSERT IGNORE 按 case_id 去重），可重复执行
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

# 路径约定：scripts/ → mysql/ → deploy/ → agent1/
AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(AGENT1_ROOT))  # 允许 from app.config.* 读取权威源
FIXTURES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "accident_cases.yaml"
OUT_SQL = AGENT1_ROOT / "deploy" / "mysql" / "sql" / "02_seed_data.sql"

# 权限矩阵 V2 权威源：落库矩阵 = 内置矩阵 = 条款出处矩阵
from app.config.permission_matrix import ROLE_PERMISSIONS as _PM_ROLE_PERMISSIONS

# ── 人员排班种子（按 personnel 表字段结构构造 6 行）──
_PERSONNEL_SEED = [
    # name, role, mine_id, shift, status
    ("王工", "支护工程师", "MINE-001", "早班", "on_duty"),
    ("李师傅", "支护工程师", "MINE-001", "中班", "on_duty"),
    ("张值班", "监测值班员", "MINE-001", "早班", "on_duty"),
    ("赵调度", "调度室主任", "MINE-001", "常白班", "on_duty"),
    ("钱安全", "安全副矿长", "MINE-001", "常白班", "on_duty"),
    ("孙矿总", "矿总工程师", "MINE-001", "常白班", "on_duty"),
    ("周操作", "操作工", "MINE-001", "晚班", "on_duty"),
]

# ── 物资库存种子（按 materials_inventory 表字段结构构造 8 行）──
_MATERIALS_SEED = [
    # name, quantity, unit, location, status
    ("锚索", 120, "根", "西翼库房", "available"),
    ("钢带", 80, "卷", "西翼库房", "available"),
    ("锚杆", 200, "根", "西翼库房", "available"),
    ("注浆材料", 300, "袋", "东翼库房", "available"),
    ("卸压钻机", 2, "台", "机电车间", "available"),
    ("备用支护材料", 50, "套", "材料棚", "available"),
    ("单体液压支柱", 40, "根", "东翼库房", "available"),
    ("木垛料", 100, "根", "材料棚", "shortage"),
]

# ── 设备状态种子（按 equipment 表字段结构构造 6 行）──
_EQUIPMENT_SEED = [
    # name, equipment_type, location, status
    ("微震监测仪", "监测", "1203工作面", "normal"),
    ("顶板离层仪", "监测", "1203工作面", "normal"),
    ("钻孔应力计", "监测", "1203运输巷", "normal"),
    ("液压支架监测系统", "监测", "1203工作面", "warning"),
    ("锚索张拉机", "支护", "机电车间", "normal"),
    ("井下应急广播", "救援", "西一采区", "normal"),
]

# ── 角色权限种子（7 行，取自 config/permission_matrix.py 权威源）──
# 与 tools/permission_check_tool.py 内置矩阵同源（「内置矩阵 = 种子矩阵 = 条款出处矩阵」，
# 见 任务成果/03_权限矩阵依据/PM1-PM6来源说明.md）；用户权限落库后，权限校验工具优先
# 从本表读取，MySQL 不可达时回退内置矩阵。
_ROLE_PERMISSIONS_SEED = list(_PM_ROLE_PERMISSIONS.items())


def _json_literal(values: list | None) -> str:
    """把 Python 列表序列化为 MySQL JSON 列字面量。"""
    if not values:
        return "NULL"
    # ensure_ascii=False 保留中文；单引号转义由 json.dumps 自动处理为 \\"
    text = json.dumps(values, ensure_ascii=False)
    return f"'{text.replace(chr(39), chr(39)*2)}'"


def _dt(value: str) -> str:
    """把 ISO 时间字符串转成 MySQL DATETIME 字面量。"""
    return value.replace("T", " ")


def _build_accident_inserts() -> list[str]:
    """从 accident_cases.yaml 生成事故案例 INSERT 语句列表。"""
    data = yaml.safe_load(FIXTURES_YAML.read_text(encoding="utf-8"))
    inserts: list[str] = []
    for c in data.get("cases", []):
        row = (
            f"('{c['case_id']}', '{c['location']}', '{_dt(c['occurred_at'])}', "
            f"'{c['risk_level']}', "
            f"{_json_literal(c.get('causes'))}, "
            f"{_json_literal(c.get('precursors'))}, "
            f"{_json_literal(c.get('actions'))}, "
            f"{_sql_string(c.get('outcome'))}, "
            f"{_json_literal(c.get('lessons'))}, "
            f"{_sql_string(c.get('source'))})"
        )
        inserts.append(
            f"INSERT IGNORE INTO accident_cases\n"
            f"  (case_id, location, occurred_at, risk_level, causes, precursors,"
            f"   actions, outcome, lessons, source)\n"
            f"VALUES {row};"
        )
    return inserts


def _sql_string(value: str | None) -> str:
    """把字符串转成 SQL 单引号字面量（转义单引号），None → NULL。"""
    if value is None:
        return "NULL"
    return f"'{value.replace(chr(39), chr(39)*2)}'"


def _build_row_inserts(
    table: str,
    columns: list[str],
    rows: list[tuple],
) -> list[str]:
    """按列生成多行 INSERT 语句。"""
    col_sql = ", ".join(columns)
    values_sql = ",\n".join(
        f"  ({', '.join(_sql_string(str(v)) for v in row)})" for row in rows
    )
    return [f"INSERT IGNORE INTO {table} ({col_sql})\nVALUES\n{values_sql};"]


def main() -> None:
    """组装完整 02_seed_data.sql 并写盘。"""
    parts: list[str] = []
    parts.append(
        "-- MineGuard Agents MySQL 初始化数据（由 deploy/mysql/scripts/seed_from_yaml.py 生成）\n"
        "-- 事故案例、人员、物资和设备数据用于本地验收；生产环境应替换为已核验台账\n"
        "-- 关键：初始化时 mysql 客户端默认会话字符集是 latin1，必须 SET NAMES utf8mb4\n"
        "-- 否则 UTF-8 中文会按 latin1 误解码入库（双重编码乱码）。\n"
        "SET NAMES utf8mb4;\n"
        "USE coal_mine_db;\n"
    )
    parts.append("-- 1. 事故案例（从本地案例资料生成）")
    parts.extend(_build_accident_inserts())
    parts.append("-- 2. 人员排班（7 行）")
    parts.extend(_build_row_inserts(
        "personnel", ["name", "role", "mine_id", "shift", "status"], _PERSONNEL_SEED))
    parts.append("-- 3. 物资库存（8 行）")
    parts.extend(_build_row_inserts(
        "materials_inventory", ["name", "quantity", "unit", "location", "status"],
        [(n, q, u, l, s) for n, q, u, l, s in _MATERIALS_SEED]))
    parts.append("-- 4. 设备状态（6 行）")
    parts.extend(_build_row_inserts(
        "equipment", ["name", "equipment_type", "location", "status"], _EQUIPMENT_SEED))
    parts.append("-- 5. 处置工单（空表，由协同管控 Agent 运行产生）")
    parts.append("-- （无种子数据，工单随工作流执行写入）")
    parts.append("-- 6. 角色权限（7 行，config/permission_matrix.py 权威源，逐条带条款出处）")
    parts.append(
        "INSERT IGNORE INTO role_permissions (role, permissions)\nVALUES\n"
        + ",\n".join(
            f"  ('{role}', {_json_literal(perms)})" for role, perms in _ROLE_PERMISSIONS_SEED
        )
        + ";"
    )

    OUT_SQL.parent.mkdir(parents=True, exist_ok=True)
    OUT_SQL.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"[OK] 已生成 {OUT_SQL}")


if __name__ == "__main__":
    main()
