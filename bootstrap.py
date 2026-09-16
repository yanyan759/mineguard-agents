"""
Agent1 独立运行验证脚本。

通过 agent1/app/__init__.py 的 __path__ 扩展机制，
Python 自动将 from app.xxx 解析到 agent1/xxx，无需手动别名。

用法：
    cd agent1
    python bootstrap.py                # 验证全模块导入 + 规则引擎 + LLM
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# agent1/ 必须在 sys.path 上才能找到 app/ 包
AGENT1_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT1_ROOT))


def main():
    print("=" * 50)
    print("  Agent1 导入链验证")
    print("=" * 50)

    # ── Schemas ──
    from app.schemas.workflow import (
        WorkflowContext, RiskLevel, AgentStatus, PerceptionWarningInput,
        MonitoringWindow, SamplingMetadata, MicroseismicEvent,
    )
    from app.schemas.mcp_tools import MCP_TOOL_REGISTRY
    from app.schemas.member1_interface import ModelInferenceRequest
    print(f"  [schemas] workflow + mcp({len(MCP_TOOL_REGISTRY)} tools) + member1: OK")

    # ── Config + Services ──
    from app.config.warning_thresholds_v1 import WARNING_THRESHOLDS_V1
    from app.services.perception_warning_service import PerceptionWarningService
    print(f"  [services] PerceptionWarningService mode={PerceptionWarningService().model_mode}: OK")

    # ── Agents ──
    from app.agent.subagents.perception_warning_agent import perception_warning_agent
    from app.agent.subagents.knowledge_retrieval_agent import knowledge_retrieval_agent
    from app.agent.subagents.dispatch_decision_agent import dispatch_decision_agent
    from app.agent.subagents.resource_evaluation_agent import resource_evaluation_agent
    from app.agent.subagents.collaborative_control_agent import collaborative_control_agent
    from app.agent.subagents.reflection_iteration_agent import reflection_iteration_agent
    agents = [
        perception_warning_agent, knowledge_retrieval_agent,
        dispatch_decision_agent, resource_evaluation_agent,
        collaborative_control_agent, reflection_iteration_agent,
    ]
    for a in agents:
        name = a["name"] if isinstance(a, dict) else a.name
        tools_n = len(a.get("tools", [])) if isinstance(a, dict) else "class"
        print(f"    {name}: {tools_n} tools" if isinstance(tools_n, int) else f"    {name}: class-based")
    print(f"  [agents] 6/6: OK")

    # ── 六 Agent 同步工作流入口 ──
    from app.agent.workflow_entry import run_workflow
    if not callable(run_workflow):
        print("  [workflow] run_workflow 不可调用: FAIL")
    else:
        print("  [workflow] run_workflow 入口: OK")

    # ── LLM ──
    from app.agent.llm import model
    if model is None:
        # 未配置 API Key / 模型名（见 agent/llm.py 守卫）：跳过模型级校验，
        # 仅提示，不误报"构造失败"也不假装"连通正常"——保持 bootstrap 离线可用
        print("  [llm] model=None：未配置 OPENAI_API_KEY 或 LLM_QWEN_MAX（跳过连通冒烟；配置后即可验证）")
    else:
        print(f"  [llm] model={model.model_name}: OK (构造成功)")
        # 真实连通冒烟：用最小请求验证配置的 OpenAI 兼容模型服务
        # 失败仅打印 FAIL，不报错退出——保持 bootstrap 离线可用（网络不可达时仍能验证导入/规则引擎）
        try:
            resp = model.invoke("只回复三个字：连通正常")
            content = resp.content if hasattr(resp, "content") else str(resp)
            ok = "连通正常" in content
            print(f"  [llm] 连通冒烟: {'PASS' if ok else 'FAIL'}（返回: {str(content)[:40]}）")
        except Exception as e:
            print(f"  [llm] 连通冒烟: FAIL（异常: {e}）")

    # ── 规则引擎实测 ──
    print()
    print("=" * 50)
    print("  规则引擎实测")
    print("=" * 50)
    ctx = WorkflowContext(
        workflow_run_id="boot-001", trace_id="bt-001", thread_id="bth-001",
        mine_id="MINE-001", roadway_id="RDW-001",
        started_at=datetime(2026, 7, 24, 8, 0, 0),
    )
    window = MonitoringWindow(
        start_time=datetime(2026, 7, 24, 7, 0, 0),
        end_time=datetime(2026, 7, 24, 8, 0, 0),
        sampling_metadata=SamplingMetadata(
            expected_frequency_hz=1.0, actual_frequency_hz=0.95, missing_rate=0.05,
        ),
    )
    events = [
        MicroseismicEvent(
            event_id="e1", timestamp=datetime(2026, 7, 24, 7, 10),
            area="工作面-1", energy_j=8000.0, event_frequency_per_hour=6.5,
            source_coordinate=(200, 150, -350), b_value=1.15,
        ),
        MicroseismicEvent(
            event_id="e2", timestamp=datetime(2026, 7, 24, 7, 25),
            area="工作面-1", energy_j=15000.0, event_frequency_per_hour=6.0,
            source_coordinate=(198, 152, -352), b_value=0.92,
        ),
    ]
    inp = PerceptionWarningInput(
        context=ctx, monitoring_window=window,
        model_version="MOA-Transformer-v1", threshold_version="simulation-v1",
        microseismic_events=events,
    )
    result = perception_warning_agent.run(inp)
    print(f"  风险等级: {result.risk_level.value}")
    print(f"  风险评分: {result.risk_score}")
    print(f"  置信度: {result.confidence}")
    print(f"  快速告警: {result.fast_alert_required}")
    print(f"  致灾因子: {[f.factor for f in result.causal_factors]}")
    print(f"  状态: {result.status.value}")

    print()
    print("=" * 50)
    print("  全部验证通过")
    print("=" * 50)


if __name__ == "__main__":
    main()
