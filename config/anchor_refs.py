"""锚点参考（SA1-SA6）：真实公开数据高仿真锚定（HALLUCINATION 清零）。

权威源：`任务成果/04_锚定素材/SA*.csv`（6 类 × 5 条，列契约：
品名/设备名, 型号/规格, 数量或量级, 价格或单位, 供应商/来源, URL, 采集日期），
与同目录 `锚点使用边界.md` 的使用边界约定一致。

设计原则（对齐 docs/真实资料爬取与整理要求清单.md §4 与 锚点使用边界.md）：
- 真实排班/库存/台账属矿方内部数据（商业秘密+数据安全法）不可爬 → 仿真种子
  用公开锚点升级为「高仿真锚定」，统一标 synthetic_but_anchored=true；
- 数量或量级 ≠ 件价（年度交易额是金额量级不是单价）；事故/执法现场数量 ≠ 库存；
  征求意见稿 ≠ 现行强制标准；CSV 空字段表示来源未披露（不推断不编造）；
- 锚点仅作量级/实例参考，不直接替换业务值；品名/角色名与种子值对齐以便溯源。

对外接口：
- ANCHOR_REFS          全部锚点记录（含 synthetic_but_anchored=true）
- ANCHOR_CATEGORIES    SA1-SA6 类别元信息
- ANCHOR_USAGE_GUARD   使用边界约定（防误用）
- find_anchor(keyword) 按品名/规格子串匹配锚点（资源溯源用）
- trace_seed(name)     种子值 → 命中的锚点列表（溯源表用；未命中返回 []）
"""

from __future__ import annotations

import csv
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent

# 锚点素材目录（任务成果/04_锚定素材，与摄取清单同源；缺失时降级为空锚点集）
ANCHOR_DIR = AGENT1_ROOT.parent / "任务成果" / "04_锚定素材"

# ── SA1-SA6 类别元信息：编号 → (主题, 锚定对象)（来源 要求清单 §4.1）──
ANCHOR_CATEGORIES: dict[str, tuple[str, str]] = {
    "SA1": ("支护/监测设备采购中标与采购锚点", "equipment 品名/型号/数量/厂商"),
    "SA2": ("设备技术参数", "equipment 参数"),
    "SA3": ("支护材料市场报价与分类", "materials 品名/单位/参考价"),
    "SA4": ("行业班制定员锚点", "personnel 岗位/班制/定员"),
    "SA5": ("煤矿企业年报/财报量级", "设备/物资量级"),
    "SA6": ("执法处罚/监察公示锚点", "设备命名/布局真实性"),
}

# ── 使用边界约定（来源 锚点使用边界.md，防把锚点误当真实台账/件价）──
ANCHOR_USAGE_GUARD: dict[str, str] = {
    "magnitude_not_unit_price": "年度交易额是金额量级，不是件价（单件单价）。",
    "scene_qty_not_inventory": "事故现场/执法现场的台数、件数不是当前库存。",
    "draft_not_standard": "征求意见稿等非现行强制标准，不能作为强制合规依据。",
    "empty_is_undisclosed": "CSV 空字段表示来源未披露，不推断、不编造。",
    "synthetic_but_anchored": "排班/库存/台账为仿真种子，锚点为量级/实例参考，如实标注。",
}

# CSV 文件名 → SA 编号（保持 4.2 列契约：品名/设备名, 型号/规格, 数量或量级,
# 价格或单位, 供应商/来源, URL, 采集日期）
_CSV_FILES: dict[str, str] = {
    "SA1": "SA1_采购中标与采购锚点.csv",
    "SA2": "SA2_设备技术参数.csv",
    "SA3": "SA3_支护材料报价与分类.csv",
    "SA4": "SA4_班制定员锚点.csv",
    "SA5": "SA5_企业年报量级.csv",
    "SA6": "SA6_执法与布局锚点.csv",
}

# 列名契约（与 CSV 表头严格一致）
_COL_ITEM = "品名/设备名"
_COL_SPEC = "型号/规格"
_COL_QTY = "数量或量级"
_COL_UNIT = "价格或单位"
_COL_SRC = "供应商/来源"
_COL_URL = "URL"
_COL_DATE = "采集日期"


def _load_anchor_csv(sa_id: str) -> list[dict]:
    """读取单个 SA CSV 为规范化锚点记录列表（utf-8-sig 兼容 BOM，字段留空不推断）。"""
    path = ANCHOR_DIR / _CSV_FILES[sa_id]
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=1):
            rows.append({
                "anchor_id": f"{sa_id}-{i:03d}",
                "category": sa_id,
                "category_label": ANCHOR_CATEGORIES[sa_id][0],
                "item": (row.get(_COL_ITEM) or "").strip(),
                "spec": (row.get(_COL_SPEC) or "").strip(),
                "magnitude": (row.get(_COL_QTY) or "").strip(),
                "unit": (row.get(_COL_UNIT) or "").strip(),
                "source": (row.get(_COL_SRC) or "").strip(),
                "url": (row.get(_COL_URL) or "").strip(),
                "collected_at": (row.get(_COL_DATE) or "").strip(),
                "synthetic_but_anchored": True,
            })
    return rows


def _load_all_anchors() -> list[dict]:
    """加载全部 SA1-SA6 锚点记录；素材目录缺失时返回空集并告警（不中断）。"""
    all_rows: list[dict] = []
    missing = [f for f in _CSV_FILES.values() if not (ANCHOR_DIR / f).exists()]
    if missing:
        print(f"[anchor_refs] 锚点素材缺失 {len(missing)} 份（{', '.join(missing)}），"
              f"锚点集为空：{ANCHOR_DIR}")
        return []
    for sa_id in _CSV_FILES:
        all_rows.extend(_load_anchor_csv(sa_id))
    return all_rows


# ── 模块级权威源（import 即加载，与 permission_matrix 同模式）──
ANCHOR_REFS: list[dict] = _load_all_anchors()

# 分类索引：SA 编号 → 记录列表
ANCHOR_BY_CATEGORY: dict[str, list[dict]] = {}
for _a in ANCHOR_REFS:
    ANCHOR_BY_CATEGORY.setdefault(_a["category"], []).append(_a)


def _split_terms(field: str) -> list[str]:
    """把锚点字段按组合分隔符拆成多个独立词项。

    组合锚点字段（如"矿长/总工程师/安全副矿长""掘锚一体机/连采机"）以 / 、 、及
    等连接多个岗位/品名，须拆分后逐项匹配，避免"矿总工程师"漏匹配"总工程师"。
    """
    import re
    parts = re.split(r"[/、及，,\s]+", field)
    return [p for p in parts if p]


def find_anchor(keyword: str) -> list[dict]:
    """按品名/规格词项匹配锚点（资源溯源用）。

    :param keyword: 种子值关键字（如"单体液压支柱""锚索""矿总工程师"）
    :return: 命中锚点记录列表（含 synthetic_but_anchored=true）；未命中返回 []
    """
    if not keyword:
        return []
    kw = keyword.strip().lower()
    hits = []
    for a in ANCHOR_REFS:
        # 组合锚点字段拆分后逐项匹配：任一词项与关键字互为子串即命中；
        # 空字段不参与匹配（避免 '' in kw 恒真误命中）
        terms = _split_terms(a["item"].lower()) + _split_terms(a["spec"].lower())
        if any(term and (kw in term or term in kw) for term in terms):
            hits.append(a)
    return hits


def trace_seed(name: str) -> list[dict]:
    """种子值 → 命中的锚点列表（溯源表用）。

    与 find_anchor 同匹配逻辑，语义上强调"为某个种子值溯源"；
    未命中返回 []（溯源表如实记录"无直接公开锚点"，不编造）。
    """
    return find_anchor(name)


if __name__ == "__main__":
    print(f"锚点总数: {len(ANCHOR_REFS)}")
    for sa, label in ANCHOR_CATEGORIES.items():
        n = len(ANCHOR_BY_CATEGORY.get(sa, []))
        print(f"  {sa} {label[0]}: {n} 条")
    print(f"全部 synthetic_but_anchored=True: "
          f"{all(a['synthetic_but_anchored'] for a in ANCHOR_REFS)}")
    print("示例溯源: '单体液压支柱' →",
          [a["anchor_id"] for a in find_anchor("单体液压支柱")])
    print("示例溯源: '锚索' →",
          [a["anchor_id"] for a in find_anchor("锚索")])
