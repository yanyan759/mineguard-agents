"""
Markdown 文件生成工具模块

提供煤矿顶板灾变领域 Markdown 模板：
- 调度处置方案模板
- 合规报告模板
- 复盘报告模板
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from app.api.context import get_session_context


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "generate_markdown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# ── 煤矿顶板灾变领域 MD 模板（供 Agent 填充后调用 generate_markdown）──

DISPATCH_PLAN_TEMPLATE = """# 顶板灾变处置方案
- 方案编号：{plan_id}
- 关联预警：{warning_id} | 风险等级：{risk_level} | 风险评分：{risk_score}
- 矿井：{mine_id} | 巷道：{roadway_id}

## 致灾因子
{factors}

## 主方案：{primary_plan_name}
{actions}

## 备选方案
{alternatives}

## 资源需求
人员：{personnel} | 物资：{materials} | 设备：{equipment}

## 强制约束与禁止动作
约束：{constraints} | 禁止：{prohibited}

---
*调度决策 Agent 生成，需人工审批后执行。*
"""

COMPLIANCE_REPORT_TEMPLATE = """# 顶板灾变处置合规报告
- 预警：{warning_id} | 方案：{plan_id} | 状态：{dispatch_status}
- 时段：{start_time} — {end_time}

## 规程校验
{compliance_checks}

## 权限与审批
审批人：{approver}（{approval_role}）| 时间：{approved_at} | 结果：{approval_result}

## 通知与执行回执
通知：{notifications}
回执：{execution_feedback}

---
*协同管控 Agent 自动生成，用于合规审计。*
"""

REFLECTION_REPORT_TEMPLATE = """# 顶板灾变处置复盘报告
- 复盘编号：{reflection_id} | 迭代：{iteration_count}/{max_iterations}
- 结论：{outcome} | 目标完成度：{goal_completion_score:.0%}

## 风险变化
处置前：{risk_before:.2f} → 处置后：{risk_after:.2f} | 趋势：{risk_trend}

## 执行评估
已完成：{completed_actions}
失败：{failed_actions}
延迟：{delayed_actions}

## 根因分析
{root_causes}

## 优化建议
阈值：{threshold_suggestions} | 策略：{strategy_suggestions} | 流程：{coordination_suggestions}

## 知识沉淀候选
{knowledge_candidates}

## 后续决策
动作：{return_action} | 目标：{target_agent} | 原因：{return_reason}
需人工介入：{human_review}

---
*反思迭代 Agent 生成，知识候选需人工审核后入库。*
"""


@tool
def generate_markdown(filename: str, content: str) -> dict:
    """生成 Markdown 文件到当前会话工作目录。

    :param filename: 输出文件名（如 "dispatch_plan.md"）
    :param content: Markdown 内容
    """
    if not filename.endswith(".md"):
        filename += ".md"
    try:
        session_dir = get_session_context()
        file_path = Path(session_dir) / filename
        file_path.write_text(content, encoding="utf-8")
        return _build_result("success", data={"file": str(file_path), "size_bytes": len(content)})
    except Exception as e:
        return _build_result("error", error=str(e))
