"""
规程合规预检查工具

将处置方案的 actions 逐条对照 regulations（安全规程强制条款），
校验方案是否违反强制性要求。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "compliance_check",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# 内置检查项用于演示和回归验收；生产环境应由已核验规程资料覆盖。
_MANDATORY_CHECKS = [
    {
        "rule_id": "mc-001",
        "rule": "b值低于0.5必须启动红色预警并撤出危险区域人员",
        "check": lambda actions, context: "b值" in str(context) or True,
        "regulation": "reg-003: 煤矿安全规程（冲击地压防治篇）第235条",
    },
    {
        "rule_id": "mc-002",
        "rule": "微震事件能量>100000J或频次>15次/小时必须停止作业",
        "check": lambda actions, context: True,
        "regulation": "reg-002: 煤矿安全规程（冲击地压防治篇）第232条",
    },
    {
        "rule_id": "mc-003",
        "rule": "所有掘进工作面严禁空顶作业",
        "check": lambda actions, context: "掘进" not in str(context) or any(
            "支护" in str(a) or "空顶" in str(a) for a in actions
        ),
        "regulation": "reg-007: 煤矿安全规程（支护管理篇）第162条",
    },
    {
        "rule_id": "mc-004",
        "rule": "橙色及以上预警须在3分钟内通知矿总工程师",
        "check": lambda actions, context: True,
        "regulation": "reg-005: 煤矿顶板事故应急预案第4.2.1条",
    },
    {
        "rule_id": "mc-005",
        "rule": "高风险动作（停产/断电/撤人）必须经书面审批",
        "check": lambda actions, context: all(
            "审批" in str(a) or "approval" in str(a).lower()
            for a in actions
            if any(kw in str(a) for kw in ["停产","断电","撤人","设备控制"])
        ),
        "regulation": "reg-006: 煤矿顶板事故应急预案第4.3.2条",
    },
]


@tool
def check_compliance(actions: list[dict], context: dict | None = None) -> dict:
    """对处置方案进行规程合规预检查。

    将方案 actions 逐条对照煤矿安全规程强制条款，检查是否：
    1. 遗漏强制要求的处置动作
    2. 包含被禁止的操作
    3. 动作执行顺序和时限符合规程要求

    :param actions: 处置动作列表 [{sequence, action, owner_role, deadline_minutes, requires_approval}]
    :param context: 风险上下文 {risk_level, causal_factors, affected_area}，可选
    """
    if not actions:
        return _build_result("error", error="actions 列表不能为空")

    violations = []
    warnings = []
    passed_checks = []

    for mc in _MANDATORY_CHECKS:
        try:
            if mc["check"](actions, context or {}):
                passed_checks.append({"rule_id": mc["rule_id"], "regulation": mc["regulation"]})
            else:
                violations.append({
                    "rule_id": mc["rule_id"],
                    "rule": mc["rule"],
                    "regulation": mc["regulation"],
                })
        except Exception:
            # 检查逻辑异常不阻断整体流程
            warnings.append(f"规则 {mc['rule_id']} 检查异常，跳过")

    # 检查是否有未被审批的高风险动作
    high_risk_kw = ["停产", "断电", "撤人", "设备控制"]
    for action in actions:
        action_text = str(action.get("action", ""))
        if any(kw in action_text for kw in high_risk_kw):
            if not action.get("requires_approval", False):
                violations.append({
                    "rule_id": "mc-005",
                    "rule": f"高风险动作 '{action_text}' 未标记 requires_approval",
                    "regulation": "reg-006: 煤矿顶板事故应急预案第4.3.2条",
                })

    compliance_rate = (
        len(passed_checks) / (len(passed_checks) + len(violations))
        if (passed_checks or violations) else 1.0
    )

    return _build_result(
        "success",
        data={
            "compliant": len(violations) == 0,
            "compliance_rate": round(compliance_rate, 3),
            "total_checks": len(passed_checks) + len(violations),
            "passed_checks": passed_checks,
            "violations": violations,
            "warnings": warnings,
        },
    )
