"""
通知对象解析与消息模板工具

根据风险等级和受影响角色解析通知目标和终端，
并使用预置模板格式化通知消息。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.tools import tool


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "notification",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# 风险等级 → 通知角色构建映射
# 演示通知链；生产环境由矿井配置和业务接入层提供责任人与终端。
_NOTIFICATION_ESCALATION = {
    "normal": [],
    "blue": [{"role": "监测值班员", "channel": "web"}],
    "yellow": [
        {"role": "监测值班员", "channel": "web"},
        {"role": "调度室主任", "channel": "web"},
    ],
    "orange": [
        {"role": "监测值班员", "channel": "web"},
        {"role": "调度室主任", "channel": "web"},
        {"role": "支护工程师", "channel": "mobile"},
        {"role": "矿总工程师", "channel": "mobile"},
        {"role": "安全副矿长", "channel": "mobile"},
    ],
    "red": [
        {"role": "监测值班员", "channel": "web"},
        {"role": "调度室主任", "channel": "web"},
        {"role": "支护工程师", "channel": "mobile"},
        {"role": "矿总工程师", "channel": "mobile"},
        {"role": "安全副矿长", "channel": "mobile"},
        {"role": "操作工", "channel": "underground_terminal"},
    ],
}

# 终端 → 推送方式
_CHANNEL_MAP = {
    "web": "Web 端实时推送",
    "screen": "大屏展示",
    "mobile": "移动端应用推送",
    "underground_terminal": "井下终端声光报警",
}


@tool
def resolve_recipients(risk_level: str, extra_roles: list[str] | None = None) -> dict:
    """根据风险等级解析需要通知的人员角色和终端。

    :param risk_level: 风险等级（normal/blue/yellow/orange/red）
    :param extra_roles: 额外需要通知的角色列表（可选）
    """
    valid_levels = {"normal", "blue", "yellow", "orange", "red"}
    rl = risk_level.lower()
    if rl not in valid_levels:
        return _build_result(
            "error",
            error=f"无效风险等级 '{risk_level}'，有效值：{sorted(valid_levels)}",
        )

    recipients = list(_NOTIFICATION_ESCALATION.get(rl, []))
    for extra in (extra_roles or []):
        if not any(r["role"] == extra for r in recipients):
            recipients.append({"role": extra, "channel": "web"})

    for r in recipients:
        r["channel_label"] = _CHANNEL_MAP.get(r["channel"], r["channel"])
        r["status"] = "pending"  # 需要配置业务通知通道后发送

    return _build_result(
        "success",
        data={
            "risk_level": rl,
            "total_recipients": len(recipients),
            "recipients": recipients,
            "note": "通知模板已生成，需通过业务接入配置发送",
        },
    )
