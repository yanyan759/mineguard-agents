"""
资源需求核算工具

对比处置方案的资源需求与当前可用资源，计算缺口并给出调整建议。
锚点集成：核算涉及的资源项附公开数据锚点溯源（synthetic_but_anchored=true），
来源 任务成果/04_锚定素材/SA*.csv（真实排班/库存/台账不可爬，仿真值以公开锚点高仿真锚定）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.tools import tool

# 锚点溯源（SA1-SA6 权威源）：资源项名称 → 公开数据锚点（量级/实例参考）
from app.config.anchor_refs import ANCHOR_USAGE_GUARD as _ANCHOR_USAGE_GUARD
from app.config.anchor_refs import find_anchor as _find_anchor


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "resource_accounting",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def calculate_gaps(
    requirements: dict,
    available_resources: dict,
) -> dict:
    """核算处置方案的资源缺口。

    将方案的 resource_requirements 与当前 available_resources 逐项对比，
    计算每个资源项的缺口量和替代建议。

    :param requirements: 方案资源需求 {personnel: [{name, count}], materials: [{name, count, unit}], equipment: [{name, count}]}
    :param available_resources: 可用资源 {personnel: [{name, count, status}], materials: [{name, count, unit, status}], equipment: [{name, count, status}]}
    """
    resource_types = ["personnel", "materials", "equipment"]
    gaps = []
    blocking_issues = []
    total_items = 0
    satisfied_items = 0

    for rtype in resource_types:
        required_list = requirements.get(rtype, [])
        available_list = available_resources.get(rtype, [])

        # 构建可用资源查找表
        available_map = {}
        for item in available_list:
            key = item.get("name", "").lower()
            available_map[key] = item

        for req in required_list:
            total_items += 1
            req_name = req.get("name", "")
            req_count = float(req.get("count", req.get("required", 0)))
            unit = req.get("unit", "")

            avail = available_map.get(req_name.lower())
            if avail is None:
                gaps.append({
                    "resource_type": rtype,
                    "resource_name": req_name,
                    "required": req_count, "available": 0.0,
                    "gap": req_count, "unit": unit, "reason": "资源未找到",
                })
                blocking_issues.append(f"{rtype}/{req_name}: 需求 {req_count}{unit}，可用 0——资源未在库存中")
                continue

            avail_count = float(avail.get("count", avail.get("available", 0)))
            avail_status = avail.get("status", "available")

            if avail_status not in ("available", "in_use"):
                gaps.append({
                    "resource_type": rtype,
                    "resource_name": req_name,
                    "required": req_count, "available": avail_count,
                    "gap": req_count, "unit": unit,
                    "reason": f"资源状态为 {avail_status}",
                })
                blocking_issues.append(f"{rtype}/{req_name}: 状态 {avail_status}，不可用")
                continue

            gap = max(0.0, req_count - avail_count)
            if gap > 0:
                gaps.append({
                    "resource_type": rtype,
                    "resource_name": req_name,
                    "required": req_count, "available": avail_count,
                    "gap": gap, "unit": unit, "reason": "数量不足",
                })
                blocking_issues.append(
                    f"{rtype}/{req_name}: 需求 {req_count}{unit}，可用 {avail_count}{unit}，缺口 {gap}{unit}"
                )
                # 数量不足：按满足比例计分（非完全失败）
                satisfied_items += min(1.0, avail_count / req_count)
            else:
                satisfied_items += 1

    feasibility_score = satisfied_items / max(total_items, 1)
    # 可行性分级阈值：0.95=完全满足、0.50=可调整（工程判定参数，可在
    # config/engine_params.py 类配置处统一调整；实际阈值由矿方应急决策口径确认）。
    # 资源数值本身已带公开数据锚点（synthetic_but_anchored），此处为分级边界而非数据。
    feasibility: Literal["feasible", "feasible_with_adjustment", "infeasible"] = (
        "feasible" if feasibility_score >= 0.95
        else "feasible_with_adjustment" if feasibility_score >= 0.5
        else "infeasible"
    )
    suggestions = []
    for gap in gaps:
        if gap["resource_type"] == "materials" and "锚索" in gap["resource_name"]:
            suggestions.append(f"可用锚杆替代部分锚索需求（缺口 {gap['gap']}{gap['unit']}）")
        elif gap["resource_type"] == "materials" and "工字钢" in gap["resource_name"]:
            suggestions.append(f"考虑使用 U 型钢替代工字钢，或调用相邻工作面库存")
        elif gap["resource_type"] == "personnel":
            suggestions.append(f"可调用下一班次 {gap['resource_name']}（缺口 {gap['gap']} 人）")
        elif gap["resource_type"] == "equipment":
            suggestions.append(f"检查相邻工作面是否有可用 {gap['resource_name']}")

    # ── 锚点溯源：资源项 → 公开数据锚点 ──
    # 对资源名称附加公开规格来源；生产库存数量仍以业务台账快照为准。
    _traced_names = sorted({
        (req.get("name") or "").strip()
        for rtype in resource_types
        for req in requirements.get(rtype, [])
        if (req.get("name") or "").strip()
    })
    anchored_references = []
    for name in _traced_names:
        hits = _find_anchor(name)
        for h in hits:
            anchored_references.append({
                "resource_name": name,
                "anchor_id": h["anchor_id"],
                "category": h["category"],
                "item": h["item"],
                "spec": h["spec"] or None,
                "magnitude": h["magnitude"] or None,
                "unit": h["unit"] or None,
                "source": h["source"],
                "url": h["url"],
                "synthetic_but_anchored": True,
            })
    # 去重（同名资源多需求重复追踪）+ 保序
    anchored_references = list({
        (r["resource_name"], r["anchor_id"]): r for r in anchored_references
    }.values())

    return _build_result(
        "success",
        data={
            "feasibility": feasibility,
            "feasibility_score": round(feasibility_score, 3),
            "total_items": total_items,
            "satisfied_items": satisfied_items,
            "resource_gaps": gaps,
            "gap_count": len(gaps),
            "blocking_issues": blocking_issues,
            "adjustment_suggestions": suggestions,
            # 锚点溯源（新增，向后兼容：旧调用方忽略该字段即可）
            "anchored_references": anchored_references,
            "data_provenance": "synthetic_but_anchored",
            "anchor_usage_guard": list(_ANCHOR_USAGE_GUARD.values()),
        },
    )
