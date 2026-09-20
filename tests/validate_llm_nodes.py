"""LLM 节点增强双模式验收脚本。

覆盖（设计 spec §7 + plan Task 9 Step 1）：
1. llm_service 封装层：合法输入 → {status:'ok', data}；LLM 不可达 → {status:'error'}
2. 子图工厂：LLM 增强节点均可构建（空工具列表，离线）；感知预警不在子图清单（红线守卫）
3. LLM_NODES=on 端到端（yellow 全链）：六阶段结果齐全 + model_mode=="llm" +
   调度决策安全属性（动作黑名单 / 强制审批 / 知识带来源）+ 路由正确
4. red 高风险审批：启用 require_approval 时协同管控触发 LangGraph interrupt（approval_request）
5. 兜底：模拟 LLM 失败（monkeypatch run_llm_subgraph 抛 LLMError）→ 节点回退确定性 +
   model_mode=="deterministic_fallback"

离线项（1 异常路径 / 2 / 5）不依赖网络，两种模式下均执行；
在线项（1 合法路径 / 3 / 4）仅 LLM_NODES=on 时执行（需要配置模型服务）。

用法：
    cd agent1
    LLM_NODES=on  python tests/validate_llm_nodes.py   # 全量（含真实 LLM，需 DeepSeek 在线）
    LLM_NODES=off python tests/validate_llm_nodes.py   # 仅离线项
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 导入设置
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

from pydantic import BaseModel

from app.agent import llm_service
from app.agent.agent_subgraph import (
    SUPPORTED_LLM_NODES,
    is_llm_node_enabled,
    _build_agent_subgraph_sync,
)
from app.agent.llm_service import LLMError, llm_call_structured
from app.agent.workflow_state import new_workflow_state

# LLM_NODES 开关：在线项仅 on 模式执行
LLM_ON = os.getenv("LLM_NODES", "off").lower() in ("1", "on", "true")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项检查结果。"""
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n  ── {title} ──")


# ═══════════════════════════════════════════════════════════════════════════
# 1. llm_service 封装层
# ═══════════════════════════════════════════════════════════════════════════


class _Probe(BaseModel):
    """最小结构化输出探针。"""
    level: str
    score: float


def test_llm_service_ok() -> None:
    """合法输入 → {status:'ok', data: Probe}（真实 LLM，json_mode 路径）。"""
    res = llm_call_structured(
        _Probe,
        "你是煤矿风险评分助手。只输出 JSON，不要解释。",
        '风险评分 0.9，输出 level（字符串）与 score（数字）。',
    )
    check(
        "llm_service 合法输入 → ok",
        res["status"] == "ok" and isinstance(res["data"], _Probe),
        detail=str(res.get("error", ""))[:120],
    )


def test_llm_service_unreachable() -> None:
    """LLM 不可达 → {status:'error'}（monkeypatch model，离线可跑）。"""
    orig = llm_service.model

    class _Boom:
        """模拟 LLM 封装不可达（with_structured_output 即抛）。"""
        def with_structured_output(self, *a, **k):
            raise RuntimeError("模拟 LLM 不可达")

    llm_service.model = _Boom()
    try:
        res = llm_call_structured(_Probe, "system", "user")
        check("llm_service LLM 不可达 → error", res["status"] == "error")
    finally:
        llm_service.model = orig


# ═══════════════════════════════════════════════════════════════════════════
# 2. 子图工厂构建性（离线）
# ═══════════════════════════════════════════════════════════════════════════


def test_subgraph_buildable() -> None:
    """LLM 增强节点均可构建子图（空工具列表，不拉起 MCP）。"""
    from app.schemas.workflow import (
        CollaborativeControlResult,
        DispatchDecisionResult,
        KnowledgeRetrievalResult,
        ReflectionIterationResult,
        ResourceEvaluationResult,
    )

    schema_map = {
        "knowledge_retrieval": KnowledgeRetrievalResult,
        "dispatch_decision": DispatchDecisionResult,
        "resource_evaluation": ResourceEvaluationResult,
        "collaborative_control": CollaborativeControlResult,
        "reflection_iteration": ReflectionIterationResult,
    }
    for key in sorted(SUPPORTED_LLM_NODES):
        try:
            graph = _build_agent_subgraph_sync(key, schema_map[key], [])
            check(f"子图构建 {key}", graph is not None)
        except Exception as exc:
            check(f"子图构建 {key}", False, detail=str(exc)[:120])
    # 红线守卫：感知预警不在 LLM 子图清单（规则定级，解释层单独处理）
    check("感知预警不在子图清单", "perception_warning" not in SUPPORTED_LLM_NODES)


# ═══════════════════════════════════════════════════════════════════════════
# 3. LLM_NODES=on 端到端（yellow 全链）
# ═══════════════════════════════════════════════════════════════════════════

# 动作黑名单（与 workflow_nodes._BLACKLIST_KEYWORDS 一致）
_BLACKLIST = ("解除预警", "停产", "断电", "撤人")


def _load_scenario(scenario_id: str):
    from app.agent.fixture_loader import FixtureDataSource

    loader = FixtureDataSource()
    si = loader.load_scenario(scenario_id)
    ctx = si.context.model_copy(update={"iteration_count": 0, "max_iterations": 2})
    return new_workflow_state(ctx, scenario_id=scenario_id)


def test_yellow_end_to_end() -> None:
    """yellow 全链端到端：六结果齐全 + model_mode=="llm" + 安全属性 + 路由。

    按节点返回的 current_phase 递进驱动（与 StateGraph 条件边语义一致），
    回退循环受 max_iterations 限制（_reflection_llm_return 强制上限）。
    """
    from app.agent.workflow_nodes import (
        node_collaborative_control,
        node_dispatch_decision,
        node_knowledge_retrieval,
        node_perception_warning,
        node_reflection_iteration,
        node_resource_evaluation,
    )

    from app.agent.workflow import _route_after_reflection, _route_after_resource

    state = _load_scenario("yellow")
    node_fns = {
        "knowledge_retrieval": node_knowledge_retrieval,
        "dispatch_decision": node_dispatch_decision,
        "resource_evaluation": node_resource_evaluation,
        "collaborative_control": node_collaborative_control,
        "reflection_iteration": node_reflection_iteration,
    }

    steps: list[str] = ["perception_warning"]
    modes: list[str] = []            # 每次 LLM 节点调用的 model_mode 序列（含回退）
    llm_ok_nodes: set[str] = set()   # 至少一次以 LLM 成功的节点类型
    t0 = time.time()

    out = node_perception_warning(state)
    state.update(out)
    current = out.get("current_phase", "knowledge_retrieval")

    loop_guard = 0
    while current in node_fns and loop_guard < 12:
        out = node_fns[current](state)
        state.update(out)
        steps.append(current)
        if out.get("model_mode"):
            modes.append(out["model_mode"])
            if out["model_mode"] == "llm":
                llm_ok_nodes.add(current)
        # 条件边复用真实路由函数（资源 infeasible 受 max_iterations 上限约束），
        # 与 StateGraph 语义一致，避免 dispatch↔resource 空转；其余固定边跟 current_phase
        if current == "resource_evaluation":
            current = _route_after_resource(state)
        elif current == "reflection_iteration":
            current = _route_after_reflection(state)
        else:
            current = out.get("current_phase", "finish")
        loop_guard += 1
    dt = time.time() - t0

    all_six = all(
        p in steps
        for p in ["perception_warning", "knowledge_retrieval", "dispatch_decision",
                  "resource_evaluation", "collaborative_control", "reflection_iteration"]
    )
    check("yellow 全链六阶段路由", all_six, detail=" → ".join(steps)[:140])

    has_all_results = all(
        state.get(k) is not None
        for k in ["perception_result", "knowledge_result", "decision_result",
                  "resource_result", "control_result", "reflection_result"]
    )
    check("yellow 六 Agent 结果齐全", has_all_results)

    check(
        "yellow 末节点 model_mode=='llm'",
        state.get("model_mode") == "llm",
        detail=f"实际 model_mode={state.get('model_mode')}",
    )
    check(
        "yellow 调度决策节点 LLM 增强",
        "dispatch_decision" in llm_ok_nodes,
        detail=f"LLM 成功节点={sorted(llm_ok_nodes)}",
    )

    # 安全属性：知识结论带来源
    knowledge = state.get("knowledge_result")
    check(
        "知识结论带来源（evidence 非空）",
        knowledge is not None and bool(knowledge.evidence),
    )

    # 安全属性：调度决策动作黑名单（命中黑名单的动作必须强制 requires_approval）
    decision = state.get("decision_result")
    if decision is not None:
        primary = decision.primary_plan
        bad = [
            a.action
            for a in (primary.actions if primary else [])
            if any(kw in a.action for kw in _BLACKLIST) and not a.requires_approval
        ]
        check(
            "调度决策黑名单动作强制审批",
            not bad,
            detail="命中但未强制审批: " + "；".join(bad) if bad else "",
        )
        # prohibited_actions 兜底覆盖黑名单
        text = "".join(decision.prohibited_actions)
        check(
            "prohibited_actions 覆盖黑名单",
            any(kw in text for kw in ("停产", "断电", "撤人"))
            and any(kw in text for kw in ("解除预警", "恢复生产")),
            detail=f"prohibited_actions={decision.prohibited_actions}",
        )
    else:
        check("调度决策黑名单动作强制审批", False, detail="decision_result 缺失")

    print(f"      （yellow 全链耗时 {dt:.0f}s，节点序列 {len(steps)} 个，model_mode 序列={modes}）")


def test_red_interrupt() -> None:
    """red 高风险审批：启用 require_approval → 协同管控触发 interrupt（approval_request）。

    红线：红色预警必须人工审批。即使调度阶段 LLM 漏标 human_approval_required，
    协同管控审批门也按 risk_level==RED 强制拦截（_control_llm_return 加固）。
    """
    from app.agent.workflow import create_workflow

    state = _load_scenario("red")
    workflow = create_workflow()
    config = {
        "configurable": {
            "thread_id": state["context"].thread_id,
            "require_approval": True,
        }
    }

    interrupted = False
    approval_request = None
    phases_seen = 0
    t0 = time.time()
    for event in workflow.stream(state, config):
        for node_name in event:
            if node_name == "__interrupt__":
                interrupted = True
                payload = event["__interrupt__"]
                if payload and len(payload) > 0:
                    # langgraph 的 Interrupt 对象：审批请求字典在其 .value 上
                    approval_request = getattr(payload[0], "value", payload[0])
        phases_seen += 1
        if interrupted or phases_seen > 20:
            break
    dt = time.time() - t0

    check(
        "red 触发人工审批 interrupt",
        interrupted and approval_request is not None,
        detail=f"耗时 {dt:.0f}s",
    )
    if interrupted and approval_request is not None:
        ar = approval_request
        check(
            "approval_request 含审批要素",
            isinstance(ar, dict) and ar.get("type") == "approval_request"
            and ar.get("risk_level") == "red"
            and bool(ar.get("dispatched_actions")),
            detail=str(ar)[:140],
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. 兜底：LLM 失败 → 确定性 + model_mode 标记
# ═══════════════════════════════════════════════════════════════════════════


def test_llm_fallback() -> None:
    """模拟 LLM 失败（monkeypatch run_llm_subgraph 抛 LLMError）→ 节点回退确定性。"""
    from app.agent import workflow_nodes as wfn
    from app.schemas.workflow import DispatchDecisionResult

    orig_enabled = wfn.is_llm_node_enabled
    orig_run = wfn.run_llm_subgraph

    # 用确定性节点构建到调度决策之前的 state（强制 off 避免解释层/子图 LLM 调用）
    wfn.is_llm_node_enabled = lambda agent_key: False
    state = _load_scenario("yellow")
    try:
        state.update(wfn.node_perception_warning(state))
        state.update(wfn.node_knowledge_retrieval(state))
    finally:
        wfn.is_llm_node_enabled = orig_enabled

    def _boom(*args, **kwargs):
        raise LLMError("模拟 LLM 不可达")

    wfn.is_llm_node_enabled = lambda agent_key: True
    wfn.run_llm_subgraph = _boom
    try:
        out = wfn.node_dispatch_decision(state)
        check(
            "LLM 失败 → deterministic_fallback",
            out.get("model_mode") == "deterministic_fallback",
            detail=f"实际 model_mode={out.get('model_mode')}",
        )
        check(
            "兜底结果为 DispatchDecisionResult",
            isinstance(out.get("decision_result"), DispatchDecisionResult),
        )
        # 兜底确定性结果仍合法（等级与感知一致）
        dec = out.get("decision_result")
        check(
            "兜底决策等级与感知一致",
            dec is not None and dec.decision_level == state["perception_result"].risk_level,
        )
    finally:
        wfn.is_llm_node_enabled = orig_enabled
        wfn.run_llm_subgraph = orig_run


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 60)
    print("  LLM 节点推理增强 — 双模式验收")
    print(f"  模式: {'LLM_NODES=on（在线项执行真实 LLM）' if LLM_ON else 'LLM_NODES=off（仅离线项）'}")
    print("=" * 60)

    section("封装层 llm_service")
    test_llm_service_unreachable()
    if LLM_ON:
        test_llm_service_ok()
    else:
        print("  （LLM_NODES=off：跳过合法输入在线项）")

    section("子图工厂构建性")
    test_subgraph_buildable()

    section("兜底：LLM 失败 → 确定性")
    test_llm_fallback()

    if LLM_ON:
        section("端到端：yellow 全链")
        test_yellow_end_to_end()
        section("端到端：red 人工审批 interrupt")
        test_red_interrupt()
    else:
        section("端到端（在线）")
        print("  （LLM_NODES=off：跳过真实 LLM 端到端，on 模式运行本脚本执行）")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
