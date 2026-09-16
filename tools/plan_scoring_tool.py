"""
方案评分与排序工具

根据风险等级、规程匹配度、资源可行性和历史成功率对候选处置方案进行加权评分排序。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "plan_scoring",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def score_plans(plans: list[dict], context: dict | None = None) -> dict:
    """对候选处置方案进行加权评分排序。

    评分维度：
    1. 规程匹配度（权重 0.30）：方案 actions 与 mandatory regulations 的匹配比例
    2. 资源可行性（权重 0.25）：方案 resource_requirements 被满足的程度
    3. 历史成功率（权重 0.25）：相似案例中该处置类型的成功比例
    4. 响应速度（权重 0.20）：从触发到执行完成的预估时间评分

    :param plans: 候选方案列表，每个方案含 {plan_id, name, actions, resource_requirements, compliance_score, historical_success_rate, estimated_minutes}
    :param context: 评估上下文（risk_level, available_resources 等），可选
    """
    if not plans:
        return _build_result("error", error="候选方案列表不能为空")

    scored = []
    for plan in plans:
        # 四个维度评分（0-1），默认值处理缺失字段
        compliance = float(plan.get("compliance_score", 0.5))
        feasibility = float(plan.get("resource_feasibility", 0.5))
        history = float(plan.get("historical_success_rate", 0.5))
        speed = max(0.0, 1.0 - float(plan.get("estimated_minutes", 60)) / 120.0)

        # 内置权重用于演示排序；生产环境可由矿井配置覆盖并记录版本。
        total = (
            0.30 * compliance
            + 0.25 * feasibility
            + 0.25 * history
            + 0.20 * speed
        )
        scored.append({
            "plan_id": plan.get("plan_id", "?"),
            "name": plan.get("name", "未命名方案"),
            "scores": {
                "compliance_match": round(compliance, 3),
                "resource_feasibility": round(feasibility, 3),
                "historical_success": round(history, 3),
                "response_speed": round(speed, 3),
            },
            "total_score": round(total, 3),
            "rank": 0,  # 填充后排序
        })

    # 按总分降序排序并分配排名
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    for i, item in enumerate(scored):
        item["rank"] = i + 1

    return _build_result(
        "success",
        data={
            "ranked_plans": scored,
            "top_plan": scored[0] if scored else None,
            "plan_count": len(scored),
            "scoring_weights": {
                "compliance_match": 0.30,
                "resource_feasibility": 0.25,
                "historical_success": 0.25,
                "response_speed": 0.20,
            },
        },
    )
