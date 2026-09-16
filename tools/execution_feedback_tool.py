"""
执行回执查询工具

查询处置工单的执行状态，输出完成率、失败动作和延迟动作。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.tools import tool


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "execution_feedback",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def query_feedback(work_order_id: str = "", actions: list[dict] | None = None) -> dict:
    """查询处置工单的执行回执和反馈。

    :param work_order_id: 工单 ID
    :param actions: 工单中的动作列表 [{sequence, action, status}]
    """
    if not actions:
        return _build_result("error", error="actions 列表不能为空")

    completed = [a for a in actions if a.get("status") == "completed"]
    failed = [a for a in actions if a.get("status") == "failed"]
    delayed = [a for a in actions if a.get("status") == "in_progress"]
    pending = [a for a in actions if a.get("status") == "pending"]

    total = len(actions)
    completion_rate = len(completed) / total if total > 0 else 0.0

    overall_status: Literal["in_progress", "completed", "partial", "failed"] = (
        "completed" if completion_rate >= 0.95 and len(failed) == 0
        else "failed" if len(failed) > total * 0.5
        else "partial" if completion_rate >= 0.5
        else "in_progress"
    )

    return _build_result(
        "success",
        data={
            "work_order_id": work_order_id,
            "overall_status": overall_status,
            "total_actions": total,
            "completed_actions": [{"sequence": a.get("sequence"), "action": a.get("action")} for a in completed],
            "failed_actions": [{"sequence": a.get("sequence"), "action": a.get("action")} for a in failed],
            "delayed_actions": [{"sequence": a.get("sequence"), "action": a.get("action")} for a in delayed],
            "pending_actions": [{"sequence": a.get("sequence"), "action": a.get("action")} for a in pending],
            "completion_rate": round(completion_rate, 3),
            "counts": {"completed": len(completed), "failed": len(failed),
                        "delayed": len(delayed), "pending": len(pending)},
        },
    )
