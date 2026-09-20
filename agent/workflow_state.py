"""六 Agent 工作流状态定义。

定义 LangGraph StateGraph 使用的 WorkflowState TypedDict，
以及六 Agent 之间传递的上下文模型（接口预留）。
贯穿六阶段执行、条件分支和反思回退全链路。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from app.schemas.workflow import (
    CollaborativeControlResult,
    DispatchDecisionResult,
    KnowledgeRetrievalResult,
    PerceptionWarningResult,
    ReflectionIterationResult,
    ResourceEvaluationResult,
    WorkflowContext,
)


# ═══════════════════════════════════════════════════════════════════════════
# 六 Agent 之间传递的上下文模型（agent_design.md §4 定义的输入接口）
# 初始为 None，由输入加载器或可选模型节点填充
# ═══════════════════════════════════════════════════════════════════════════


class MineContext(BaseModel):
    """知识检索 Agent 输入：矿井地质与工程背景。"""
    model_config = ConfigDict(extra="forbid")
    geological_conditions: dict[str, Any] = Field(default_factory=dict, description="地质条件（岩性/断层/喀斯特等）")
    support_type: str = Field(default="", description="当前支护类型")
    roadway_parameters: dict[str, Any] = Field(default_factory=dict, description="巷道参数（断面/埋深等）")
    recent_operations: list[str] = Field(default_factory=list, description="近期作业活动")


class OperationalContext(BaseModel):
    """调度决策 Agent 输入：当前生产运行状态。"""
    model_config = ConfigDict(extra="forbid")
    production_status: str = Field(default="正常生产", description="生产状态")
    current_shift: str = Field(default="早班", description="当前班次")
    affected_people_estimate: int = Field(default=0, ge=0, description="受影响人员估计")
    available_time_window_minutes: int = Field(default=120, ge=0, description="可用响应时间窗口（分钟）")


class EvaluationConstraints(BaseModel):
    """资源评估 Agent 输入：约束与规则。"""
    model_config = ConfigDict(extra="forbid")
    reserved_resources: list[str] = Field(default_factory=list, description="预留资源列表")
    minimum_staffing_rules: list[str] = Field(default_factory=list, description="最低人员配置规则")
    equipment_safety_rules: list[str] = Field(default_factory=list, description="设备安全规则")
    maximum_mobilization_minutes: int = Field(default=60, ge=0, description="最大动员时限（分钟）")


class ApprovalContext(BaseModel):
    """协同管控 Agent 输入：审批状态。"""
    model_config = ConfigDict(extra="forbid")
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = Field(default="not_required")
    approver: dict[str, str] | None = Field(default=None, description="审批人信息（user_id/role）")
    approved_at: str | None = Field(default=None, description="审批时间 ISO-8601")


class RecipientContext(BaseModel):
    """协同管控 Agent 输入：通知目标与终端。"""
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(default_factory=list, description="需通知的角色列表")
    terminals: list[str] = Field(default_factory=list, description="可用终端（web/screen/mobile/underground_terminal）")
    escalation_chain: list[str] = Field(default_factory=list, description="逐级上报链")


class ExecutionObservations(BaseModel):
    """反思迭代 Agent 输入：执行反馈与新监测数据。"""
    model_config = ConfigDict(extra="forbid")
    actions_completed: list[str] = Field(default_factory=list, description="已完成动作")
    actions_failed: list[str] = Field(default_factory=list, description="失败动作")
    risk_before: float = Field(default=0.0, ge=0.0, le=1.0, description="处置前风险评分")
    risk_after: float = Field(default=0.0, ge=0.0, le=1.0, description="处置后风险评分")
    new_monitoring_data: list[dict[str, Any]] = Field(default_factory=list, description="新的监测数据")
    human_feedback: list[str] = Field(default_factory=list, description="人工反馈意见")


# ═══════════════════════════════════════════════════════════════════════════
# 工作流状态
# ═══════════════════════════════════════════════════════════════════════════


class WorkflowState(TypedDict, total=False):
    """六 Agent 工作流共享状态。

    每个节点读取/写入对应字段，LangGraph 自动在节点间传递。
    TypedDict + total=False 允许部分字段未初始化。
    """

    # ── 运行上下文 ──
    context: WorkflowContext
    current_phase: str

    # ── 六阶段结果 ──
    perception_result: PerceptionWarningResult | None
    knowledge_result: KnowledgeRetrievalResult | None
    decision_result: DispatchDecisionResult | None
    resource_result: ResourceEvaluationResult | None
    control_result: CollaborativeControlResult | None
    reflection_result: ReflectionIterationResult | None

    # ── 成员 1 联调增强：MOA-Transformer 预测结果（可选）──
    # 感知节点调用 run_prediction 写入；仅增强观测，绝不参与 risk_level/risk_score 定级
    prediction_result: dict | None

    # ── 知识写回（END 前节点产物，设计 §8.5）──
    knowledge_writeback_result: dict | None

    # ── 六 Agent 之间的上下文对象（agent_design.md §4 定义的输入接口）──
    mine_context: MineContext | None
    operational_context: OperationalContext | None
    evaluation_constraints: EvaluationConstraints | None
    approval_context: ApprovalContext | None
    recipient_context: RecipientContext | None
    execution_observations: ExecutionObservations | None

    # ── 消息（LangGraph 标准流式输出）──
    messages: Annotated[list, add_messages]

    # ── 迭代与回退追踪 ──
    iteration_history: list[dict[str, Any]]
    iteration_count: int
    max_iterations: int

    # ── 快速告警旁路标记 ──
    fast_alert_triggered: bool

    # ── 模拟数据源标记 ──
    fixture_scenario_id: str | None
    # 自定义摘要独立保存为 JSON，保证检查点恢复和节点回退仍读取用户数据。
    custom_task: dict[str, Any] | None
    actual_snapshot: dict[str, Any] | None  # 本次实际数据配置及资料的不可变快照
    assessment_profile: dict[str, Any] | None  # 服务端在启动时冻结，恢复及回退沿用
    input_file_evidence: list[dict[str, Any]]  # 当前会话上传文件的SHA256与记录核对结果
    user_query: str | None

    # ── 错误与警告 ──
    errors: list[str]
    warnings: list[str]

    # ── 可选 LLM 增强态标记 ──
    # 仅 LLM_NODES=on 时写入：节点走 LLM 子图 → "llm"；LLM 失败回退确定性 → "deterministic_fallback"
    # 记在 WorkflowState 运行时元数据，不塞进各结果 Schema（六结果 Schema extra=forbid 无此字段）
    model_mode: str | None


def new_workflow_state(
    context: WorkflowContext,
    scenario_id: str | None = None,
) -> WorkflowState:
    """创建初始工作流状态。

    所有阶段结果和上下文对象初始为 None，随工作流执行逐步填充。
    """
    return WorkflowState(
        context=context,
        current_phase="perception_warning",
        perception_result=None,
        knowledge_result=None,
        decision_result=None,
        resource_result=None,
        control_result=None,
        reflection_result=None,
        prediction_result=None,
        knowledge_writeback_result=None,
        mine_context=None,
        operational_context=None,
        evaluation_constraints=None,
        approval_context=None,
        recipient_context=None,
        execution_observations=None,
        messages=[],
        iteration_history=[],
        iteration_count=context.iteration_count,
        max_iterations=context.max_iterations,
        fast_alert_triggered=False,
        fixture_scenario_id=scenario_id,
        custom_task=None,
        user_query=None,
        errors=[],
        warnings=[],
    )
