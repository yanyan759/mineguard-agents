"""六 Agent 显式工作流定义。

基于 LangGraph StateGraph 构建六阶段状态流转，
包含条件分支（风险等级/资源可行性/审批状态）和反思回退逻辑。
"""

from __future__ import annotations

from datetime import datetime, timezone
from langgraph.config import get_stream_writer

from langgraph.graph import END, StateGraph

from app.agent.checkpoint import get_checkpointer
from app.agent.knowledge_sources import probe_neo4j
from app.agent.workflow_nodes import (
    node_collaborative_control,
    node_dispatch_decision,
    node_knowledge_retrieval,
    node_knowledge_write_back,
    node_perception_warning,
    node_reflection_iteration,
    node_resource_evaluation,
)
from app.agent.workflow_state import WorkflowState


def build_workflow() -> StateGraph:
    """构建六 Agent 工作流图。

    节点：
      perception_warning → knowledge_retrieval → dispatch_decision
      → resource_evaluation → collaborative_control → reflection_iteration

    条件边：
      - perception: normal → END, 其他 → knowledge_retrieval
      - fast_alert: 旁路通知 collaborative_control
      - resource: feasible → collaborative_control, infeasible → dispatch_decision
      - reflection: finish → END, return_to_* → 对应节点
    """
    builder = StateGraph(WorkflowState)

    # ── 注册六节点 ──
    # 开始事件在节点函数执行前发送，不能等计算完成后才假报“执行中”。
    def traced(name, function):
        def execute(state):
            started = datetime.now(timezone.utc)
            get_stream_writer()({"node_started": name, "started_at": started.isoformat()})
            output = function(state)
            finished = datetime.now(timezone.utc)
            # 耗时来自真实节点调用，不沿用演示结果中的固定毫秒数。
            for value in output.values():
                if hasattr(value, "agent_name") and hasattr(value, "duration_ms"):
                    value.started_at, value.finished_at = started, finished
                    value.duration_ms = round((finished-started).total_seconds()*1000)
            return output
        return execute
    for name, function in [("perception_warning", node_perception_warning), ("knowledge_retrieval", node_knowledge_retrieval),
                           ("dispatch_decision", node_dispatch_decision), ("resource_evaluation", node_resource_evaluation),
                           ("collaborative_control", node_collaborative_control), ("reflection_iteration", node_reflection_iteration),
                           ("knowledge_write_back", node_knowledge_write_back)]:
        builder.add_node(name, traced(name, function))

    # ── 入口 ──
    builder.set_entry_point("perception_warning")

    # ── 感知预警 → 条件分支 ──
    builder.add_conditional_edges(
        "perception_warning",
        _route_after_perception,
        {
            "finish": END,
            "knowledge_retrieval": "knowledge_retrieval",
        },
    )

    # ── 知识检索 → 调度决策（正常链路）──
    builder.add_edge("knowledge_retrieval", "dispatch_decision")

    # ── 调度决策 → 资源评估（正常链路）──
    builder.add_edge("dispatch_decision", "resource_evaluation")

    # ── 资源评估 → 条件分支 ──
    builder.add_conditional_edges(
        "resource_evaluation",
        _route_after_resource,
        {
            "collaborative_control": "collaborative_control",
            "dispatch_decision": "dispatch_decision",
        },
    )

    # ── 协同管控 → 反思迭代 ──
    builder.add_edge("collaborative_control", "reflection_iteration")

    # ── 反思迭代 → 条件分支（结束或回退）──
    builder.add_conditional_edges(
        "reflection_iteration",
        _route_after_reflection,
        {
            "finish": "knowledge_write_back",  # 结束前先过写回节点（设计 §8.5）
            "perception_warning": "perception_warning",
            "knowledge_retrieval": "knowledge_retrieval",
            "dispatch_decision": "dispatch_decision",
            "resource_evaluation": "resource_evaluation",
            "collaborative_control": "collaborative_control",
        },
    )

    # ── 知识写回 → 结束（END 前写回节点，设计 §8.5）──
    builder.add_edge("knowledge_write_back", END)

    return builder


def create_workflow():
    """创建编译后的工作流实例（带 InMemorySaver checkpointer）。

    注册所有 Pydantic Schema 类型到 msgpack 白名单，
    避免 LangGraph checkpoint 序列化时抛出警告。
    """
    from app.schemas.workflow import (
        AgentStatus, RiskLevel, WorkflowContext,
        PerceptionWarningResult, KnowledgeRetrievalResult,
        DispatchDecisionResult, ResourceEvaluationResult,
        CollaborativeControlResult, ReflectionIterationResult,
    )

    import langgraph.config
    langgraph.config.allowed_msgpack_modules = [
        ("app.schemas.workflow", "WorkflowContext"),
        ("app.schemas.workflow", "AgentStatus"),
        ("app.schemas.workflow", "RiskLevel"),
        ("app.schemas.workflow", "PerceptionWarningResult"),
        ("app.schemas.workflow", "KnowledgeRetrievalResult"),
        ("app.schemas.workflow", "DispatchDecisionResult"),
        ("app.schemas.workflow", "ResourceEvaluationResult"),
        ("app.schemas.workflow", "CollaborativeControlResult"),
        ("app.schemas.workflow", "ReflectionIterationResult"),
    ]

    builder = build_workflow()
    checkpointer = get_checkpointer()
    probe_neo4j()  # NEO4J_URI 配置时编译期连通探测（对齐 get_checkpointer 先例）
    return builder.compile(checkpointer=checkpointer)


# ── 条件路由函数 ──


def _route_after_perception(state: WorkflowState) -> str:
    """感知预警后的路由决策。

    normal → 结束；blue/yellow/orange/red → 知识检索
    """
    if state.get("custom_task"):
        return "knowledge_retrieval"
    result = state.get("perception_result")
    if result is None:
        return "knowledge_retrieval"
    if result.risk_level is not None and result.risk_level.value == "normal":
        return "finish"
    return "knowledge_retrieval"


def _route_after_resource(state: WorkflowState) -> str:
    """资源评估后的路由决策。

    feasible / feasible_with_adjustment → 协同管控
    infeasible → 返回调度决策（不超过 1 次回退，之后强制进入协同管控）
    """
    # 固定上传方案无法凭空增加库存；完整呈现缺口，不重复运行同一决策。
    if state.get("actual_snapshot"):
        return "collaborative_control"
    result = state.get("resource_result")
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", 2)

    if result is None:
        return "collaborative_control"

    if result.feasibility == "infeasible" and iteration < max_iter:
        # 回退到调度决策重新生成方案
        return "dispatch_decision"

    # feasible、feasible_with_adjustment、或达到迭代上限 → 进入协同管控
    return "collaborative_control"


def _route_after_reflection(state: WorkflowState) -> str:
    """反思迭代后的路由决策。

    finish / wait_for_data / wait_for_human → knowledge_write_back（END 前写回，设计 §8.5）
    return_to_* → 对应节点（perception/knowledge/decision/resource/control）
    """
    result = state.get("reflection_result")
    if result is None:
        return "finish"

    action = result.return_decision.action

    # 结束或等待 → END
    if action in ("finish", "wait_for_data", "wait_for_human"):
        return "finish"

    # 定向返回
    route_map = {
        "return_to_perception": "perception_warning",
        "return_to_knowledge": "knowledge_retrieval",
        "return_to_decision": "dispatch_decision",
        "return_to_resource": "resource_evaluation",
        "return_to_control": "collaborative_control",
    }
    return route_map.get(action, "finish")
