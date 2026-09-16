"""生成《锚点溯源表》（每个种子值 → 公开数据锚点来源）。

需求口径（docs/真实资料爬取与整理要求清单.md §4.4/§7）：
真实排班/库存/台账属矿方内部数据（商业秘密+数据安全法）不可爬，仿真种子以
公开锚点（SA1-SA6）升级为「高仿真锚定」，如实标注 synthetic_but_anchored=true，
并产出溯源表（每个种子值 → 锚点来源 URL）。

种子权威源与 MySQL seed 同源：
- 人员/物资/设备种子 = deploy/mysql/scripts/seed_from_yaml.py 的 _PERSONNEL_SEED
  / _MATERIALS_SEED / _EQUIPMENT_SEED（与 02_seed_data.sql 同一处定义）
- 权限矩阵种子 = config/permission_matrix.py ROLE_PERMISSIONS
锚点权威源 = config/anchor_refs.py（加载 任务成果/04_锚定素材/SA*.csv）

输出：任务成果/04_锚定素材/锚点溯源表.csv
- 每行一个「种子值 → 锚点」映射（同名资源多条锚点逐一列出）
- 无匹配锚点的种子值也如实列出（锚点列留空，标注"未匹配公开锚点"）
- 附使用边界说明列，防止把锚点量级误当件价/库存/强制标准

用法：
    cd agent1
    python deploy/mysql/scripts/generate_anchor_trace.py          # 生成溯源表
    python deploy/mysql/scripts/generate_anchor_trace.py --dry    # 只打印不写盘
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# 种子权威源（与 MySQL seed 同源，避免两处漂移）
from app.config.anchor_refs import ANCHOR_USAGE_GUARD
from app.config.anchor_refs import trace_seed as _trace_seed
from app.config.permission_matrix import ROLE_PERMISSIONS as _PM_ROLE_PERMISSIONS
from deploy.mysql.scripts.seed_from_yaml import (
    _EQUIPMENT_SEED,
    _MATERIALS_SEED,
    _PERSONNEL_SEED,
)

OUT_CSV = AGENT1_ROOT.parent / "任务成果" / "04_锚定素材" / "锚点溯源表.csv"

# 种子类别 → (种子表, 名称列下标)
_SEED_SOURCES = [
    ("personnel", _PERSONNEL_SEED, 1),   # (name, role, mine_id, shift, status) → role 列
    ("materials", _MATERIALS_SEED, 0),   # (name, quantity, unit, location, status) → name 列
    ("equipment", _EQUIPMENT_SEED, 0),   # (name, equipment_type, location, status) → name 列
]


def _collect_seed_values() -> list[dict]:
    """收集全部仿真种子值（与 02_seed_data.sql 同源），供逐条溯源。"""
    values = []
    for category, table, idx in _SEED_SOURCES:
        for row in table:
            values.append({
                "category": category,
                "seed_value": str(row[idx]),
                "seed_detail": " | ".join(str(x) for x in row if str(x) and str(x) != row[idx]),
            })
    # 权限矩阵角色（role_permissions 种子）也纳入溯源（SA4 班制定员/岗位框架锚点）
    for role, perms in _PM_ROLE_PERMISSIONS.items():
        values.append({
            "category": "role_permissions",
            "seed_value": role,
            "seed_detail": f"权限: {','.join(perms)}",
        })
    return values


def build_trace_rows() -> list[dict]:
    """构造溯源表行：每个种子值 → 匹配锚点列表（无匹配如实留空）。"""
    rows: list[dict] = []
    for v in _collect_seed_values():
        anchors = _trace_seed(v["seed_value"])
        if not anchors:
            rows.append({
                "category": v["category"],
                "seed_value": v["seed_value"],
                "seed_detail": v["seed_detail"],
                "anchor_id": "",
                "anchor_item": "",
                "anchor_spec": "",
                "anchor_magnitude": "",
                "source": "",
                "url": "",
                "synthetic_but_anchored": "true",
                "note": "未匹配公开锚点（来源未披露，如实标注）",
            })
            continue
        for a in anchors:
            rows.append({
                "category": v["category"],
                "seed_value": v["seed_value"],
                "seed_detail": v["seed_detail"],
                "anchor_id": a["anchor_id"],
                "anchor_item": a["item"],
                "anchor_spec": a["spec"],
                "anchor_magnitude": a["magnitude"],
                "source": a["source"],
                "url": a["url"],
                "synthetic_but_anchored": "true",
                "note": ANCHOR_USAGE_GUARD["magnitude_not_unit_price"]
                    if a["category"] in ("SA1", "SA3", "SA5") else "",
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="生成《锚点溯源表》")
    parser.add_argument("--dry", action="store_true", help="只打印不写盘")
    args = parser.parse_args()

    rows = build_trace_rows()
    matched = sum(1 for r in rows if r["url"])
    seed_values = _collect_seed_values()
    print(f"[info] 种子值总量: {len(seed_values)}，溯源映射行: {len(rows)}，命中锚点行: {matched}")
    print(f"[info] 未命中种子值: "
          f"{sum(1 for v in seed_values if not _trace_seed(v['seed_value']))} 个")

    if args.dry:
        for r in rows[:12]:
            print(f"  [{r['category']}] {r['seed_value']} <- {r['anchor_id'] or '无'}"
                  f" | {r['source'] or r['note']}")
        print(f"[dry] 未写盘（--dry）")
        return 0

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[ok] 已生成 {OUT_CSV}（{len(rows)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
