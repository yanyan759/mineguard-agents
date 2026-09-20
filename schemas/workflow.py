"""煤矿顶板灾变六 Agent 工作流的结构化数据契约。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaModel(BaseModel):
    """工作流 Schema 的公共校验策略。"""

    # 业务契约明确需要 model_version/model_mode 字段；关闭 Pydantic 对 model_ 前缀的
    # 保护提示，同时仍禁止未声明字段进入工作流状态。
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class RiskLevel(str, Enum):
    """统一风险等级。"""

    NORMAL = "normal"
    BLUE = "blue"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class AgentStatus(str, Enum):
    """统一 Agent 执行状态。"""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    WAITING_HUMAN = "waiting_human"


class OperatorContext(SchemaModel):
    user_id: str
    role: str
    permissions: list[str] = Field(default_factory=list)


class WorkflowContext(SchemaModel):
    workflow_run_id: str
    trace_id: str
    thread_id: str
    mine_id: str
    roadway_id: str
    warning_id: str | None = None
    current_stage: str = "perception_warning"
    iteration_count: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=2, ge=0)
    started_at: datetime
    operator: OperatorContext | None = None

    @model_validator(mode="after")
    def validate_iteration_limit(self) -> WorkflowContext:
        if self.iteration_count > self.max_iterations:
            raise ValueError("iteration_count cannot exceed max_iterations")
        return self


class Evidence(SchemaModel):
    source_type: str
    source_id: str | None = None
    description: str
    value: Any | None = None


class CommonAgentResult(SchemaModel):
    agent_name: str
    status: AgentStatus
    summary: str
    # 没有经过定义和验证的置信度计算时保持未知，不使用0或固定常量冒充。
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evaluation_basis: str = "unassessed"
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    next_action: str


class SamplingMetadata(SchemaModel):
    expected_frequency_hz: float = Field(gt=0)
    actual_frequency_hz: float = Field(ge=0)
    missing_rate: float = Field(ge=0.0, le=1.0)


class MonitoringWindow(SchemaModel):
    start_time: datetime
    end_time: datetime
    sampling_metadata: SamplingMetadata

    @model_validator(mode="after")
    def validate_time_order(self) -> MonitoringWindow:
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be earlier than start_time")
        return self


class MicroseismicEvent(SchemaModel):
    event_id: str
    timestamp: datetime
    area: str
    energy_j: float = Field(ge=0)
    event_frequency_per_hour: float = Field(ge=0)
    source_coordinate: tuple[float, float, float]
    b_value: float = Field(gt=0)


class BoreholeStressReading(SchemaModel):
    point_id: str
    timestamp: datetime
    stress_mpa: float
    stress_gradient_mpa_h: float | None = None
    depth_m: float | None = Field(default=None, ge=0)


class RoofSeparationReading(SchemaModel):
    point_id: str
    timestamp: datetime
    displacement_mm: float
    velocity_mm_h: float | None = None
    depth_m: float | None = Field(default=None, ge=0)


class HydraulicSupportReading(SchemaModel):
    support_id: str
    timestamp: datetime
    left_pressure_mpa: float
    right_pressure_mpa: float
    peak_mpa: float | None = None


class RoadwayDisplacementReading(SchemaModel):
    point_id: str
    timestamp: datetime
    displacement_mm: float
    velocity_mm_h: float | None = None


class PerceptionWarningInput(SchemaModel):
    context: WorkflowContext
    monitoring_window: MonitoringWindow
    model_version: str
    threshold_version: str | None = None
    # 允许空列表进入数据质量检查，由感知服务返回明确的 failed 结果；事件字段本身
    # 仍由 MicroseismicEvent 做严格格式校验。
    microseismic_events: list[MicroseismicEvent] = Field(default_factory=list)
    borehole_stress: list[BoreholeStressReading] = Field(default_factory=list)
    roof_separation: list[RoofSeparationReading] = Field(default_factory=list)
    hydraulic_support_resistance: list[HydraulicSupportReading] = Field(
        default_factory=list
    )
    roadway_surface_displacement: list[RoadwayDisplacementReading] = Field(
        default_factory=list
    )


class AffectedArea(SchemaModel):
    mine_id: str
    roadway_id: str
    locations: list[str] = Field(default_factory=list)


class CausalFactor(SchemaModel):
    factor: str
    importance: float = Field(ge=0.0, le=1.0)
    trend: Literal["rising", "stable", "falling", "unknown"]
    evidence: list[str] = Field(default_factory=list)


class DataQuality(SchemaModel):
    completeness: float | None = Field(ge=0.0, le=1.0)
    timeliness: float | None = Field(ge=0.0, le=1.0)
    missing_sources: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class PerceptionWarningResult(CommonAgentResult):
    warning_id: str
    risk_level: RiskLevel | None
    risk_score: float | None = Field(ge=0.0, le=1.0)
    assessment: dict | None = None
    model_version: str
    threshold_version: str
    model_mode: Literal["online", "mock", "rule_based"]
    affected_area: AffectedArea
    causal_factors: list[CausalFactor] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    triggered_rules: list[str] = Field(default_factory=list)
    fast_alert_required: bool = False
    recommended_recheck_minutes: int | None = Field(ge=0)


class SimilarCase(SchemaModel):
    case_id: str
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    match_terms: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_status: str = "unverified"
    causes: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    outcome: str | None = None
    source: str


class RegulationReference(SchemaModel):
    document: str
    clause: str | None = None
    requirement: str
    mandatory: bool = True
    source: str
    source_url: str | None = None
    applicability: str | None = None
    verified_at: str | None = None


class SupportRecommendation(SchemaModel):
    scheme_id: str
    scheme_name: str
    # 第三数据域（导师历年支护方案 + 专家研判）字段：适用条件 / 专家研判要点
    applicable_conditions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    expert_review: str = ""
    source: str


class KnowledgeRetrievalResult(CommonAgentResult):
    warning_id: str
    similar_cases: list[SimilarCase] = Field(default_factory=list)
    regulations: list[RegulationReference] = Field(default_factory=list)
    support_recommendations: list[SupportRecommendation] = Field(default_factory=list)
    knowledge_conflicts: list[str] = Field(default_factory=list)
    missing_topics: list[str] = Field(default_factory=list)


class DispatchAction(SchemaModel):
    sequence: int = Field(ge=1)
    action: str
    owner_role: str
    deadline_minutes: int | None = Field(ge=0)
    requires_approval: bool = False
    acceptance_criteria: list[str] = Field(default_factory=list)


class DispatchPlan(SchemaModel):
    plan_id: str
    name: str
    actions: list[DispatchAction] = Field(default_factory=list)


class ResourceRequirements(SchemaModel):
    personnel: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    communication: list[str] = Field(default_factory=list)


class DispatchDecisionResult(CommonAgentResult):
    decision_id: str
    warning_id: str
    decision_level: RiskLevel | None
    objectives: list[str] = Field(default_factory=list)
    primary_plan: DispatchPlan
    alternative_plans: list[DispatchPlan] = Field(default_factory=list)
    resource_requirements: ResourceRequirements
    mandatory_constraints: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    human_approval_required: bool = False
    decision_rationale: list[str] = Field(default_factory=list)


class ResourceGap(SchemaModel):
    resource_type: Literal["personnel", "material", "equipment", "route", "other"]
    resource_name: str
    required: float | None = None
    available: float | None = None
    unit: str | None = None
    reason: str | None = None


class ResourceEvaluationResult(CommonAgentResult):
    evaluation_id: str
    plan_id: str
    feasibility: Literal[
        "feasible", "feasible_with_adjustment", "infeasible", "unknown"
    ]
    feasibility_score: float | None = Field(default=None, ge=0.0, le=1.0)
    quantity_check: Literal["sufficient", "insufficient", "not_provided"] | None = None
    execution_readiness: Literal["unverified", "verified"] | None = None
    resource_gaps: list[ResourceGap] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    adjustment_suggestions: list[str] = Field(default_factory=list)
    alternative_resources: list[str] = Field(default_factory=list)
    redecision_required: bool = False


class PermissionCheck(SchemaModel):
    passed: bool | None
    required_role: str | None = None
    actual_role: str | None = None
    denied_actions: list[str] = Field(default_factory=list)


class NotificationResult(SchemaModel):
    recipient: str
    channel: Literal["web", "screen", "mobile", "underground_terminal"]
    status: Literal["sent", "failed", "pending"]
    sent_at: datetime | None = None


class CollaborativeControlResult(CommonAgentResult):
    coordination_id: str
    permission_check: PermissionCheck
    notification_results: list[NotificationResult] = Field(default_factory=list)
    dispatch_status: Literal[
        "waiting_approval", "dispatched", "executing", "completed", "rejected", "not_dispatched"
    ]
    dispatched_actions: list[str] = Field(default_factory=list)
    execution_feedback: list[str] = Field(default_factory=list)
    report_artifacts: list[str] = Field(default_factory=list)
    human_action_required: bool = False


class RiskChange(SchemaModel):
    before: float | None = Field(ge=0.0, le=1.0)
    after: float | None = Field(default=None, ge=0.0, le=1.0)
    trend: Literal["decreased", "unchanged", "increased", "unknown"]


class ReturnDecision(SchemaModel):
    action: Literal[
        "finish",
        "return_to_perception",
        "return_to_knowledge",
        "return_to_decision",
        "return_to_resource",
        "return_to_control",
        "wait_for_data",
        "wait_for_human",
    ]
    target_agent: str | None = None
    reason: str
    required_inputs: list[str] = Field(default_factory=list)


class ReflectionIterationResult(CommonAgentResult):
    reflection_id: str
    iteration_count: int = Field(ge=0)
    outcome: Literal["resolved", "improved", "unchanged", "worsened", "unknown"]
    goal_completion_score: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_change: RiskChange
    root_causes: list[str] = Field(default_factory=list)
    threshold_suggestions: list[str] = Field(default_factory=list)
    strategy_suggestions: list[str] = Field(default_factory=list)
    coordination_suggestions: list[str] = Field(default_factory=list)
    knowledge_candidates: list[dict[str, Any]] = Field(default_factory=list)
    return_decision: ReturnDecision
    human_review_required: bool = False


class ResourceItem(SchemaModel):
    resource_id: str
    name: str
    required: float = Field(ge=0)
    available: float = Field(ge=0)
    unit: str
    status: str = "available"


class ResourceSnapshot(SchemaModel):
    personnel: list[ResourceItem] = Field(default_factory=list)
    support_materials: list[ResourceItem] = Field(default_factory=list)
    equipment: list[ResourceItem] = Field(default_factory=list)


class SimulationCase(SchemaModel):
    """用于可复现演示和回归验收的场景载体。"""

    scenario_id: str
    description: str
    perception_input: PerceptionWarningInput
    expected_warning: PerceptionWarningResult
    resource_snapshot: ResourceSnapshot | None = None
    expected_resource_feasibility: Literal[
        "feasible", "feasible_with_adjustment", "infeasible", "unknown"
    ] | None = None
