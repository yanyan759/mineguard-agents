"""MCP 工具输入输出 Schema。

按项目进度汇报1中定义的 9 个工具提供 Pydantic 数据契约。
工作流默认以 LangChain @tool 形式实现，
但输入输出严格按本文件定义，方便后续切换到正式 MCP。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .workflow import Evidence, ResourceItem, RiskLevel


class McpToolModel(BaseModel):
    """MCP 工具的公共格式约束。"""
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# ═══════════════════════════════════════════════════════════════════════════
# 1. query_sensor_history — 查询监测传感器历史数据
# ═══════════════════════════════════════════════════════════════════════════


class QuerySensorHistoryInput(McpToolModel):
    """查询指定时间范围内指定测点的监测历史。"""
    mine_id: str = Field(description="矿井标识")
    roadway_id: str = Field(description="巷道/工作面标识")
    sensor_types: list[
        Literal[
            "borehole_stress",
            "roof_separation",
            "hydraulic_support",
            "microseismic",
            "roadway_displacement",
        ]
    ] = Field(description="传感器类型列表，可一次查询多种")
    start_time: datetime = Field(description="查询起始时间")
    end_time: datetime = Field(description="查询终止时间")
    point_ids: list[str] | None = Field(default=None, description="指定测点ID，为空则返回全部")
    limit: int = Field(default=500, ge=1, le=5000, description="返回行数上限")

    @field_validator("end_time")
    @classmethod
    def end_after_start(cls, value: datetime, info) -> datetime:
        start = info.data.get("start_time")
        if start is not None and value <= start:
            raise ValueError("end_time must be later than start_time")
        return value


class SensorHistoryRow(McpToolModel):
    """单条传感器历史记录。"""
    sensor_type: str
    point_id: str
    timestamp: datetime
    value: float
    unit: str


class QuerySensorHistoryOutput(McpToolModel):
    """传感器历史查询结果。"""
    rows: list[SensorHistoryRow] = Field(default_factory=list)
    total_count: int = Field(default=0, ge=0)
    truncated: bool = Field(default=False, description="结果是否因达到 limit 而被截断")
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 2. query_roof_risk_case — 查询顶板风险历史案例
# ═══════════════════════════════════════════════════════════════════════════


class QueryRoofRiskCaseInput(McpToolModel):
    """查询历史顶板事故案例。"""
    risk_level: RiskLevel | None = Field(default=None, description="按风险等级筛选")
    causal_factors: list[str] = Field(default_factory=list, description="按致灾因子关键词匹配")
    mine_id: str | None = Field(default=None, description="按矿井筛选")
    roadway_id: str | None = Field(default=None, description="按巷道筛选")
    top_k: int = Field(default=5, ge=1, le=20, description="返回数量")
    include_disposal: bool = Field(default=True, description="是否包含处置方案与结果")


class HistoricalCase(McpToolModel):
    """单条历史事故案例。"""
    case_id: str = Field(description="案例唯一标识")
    location: str = Field(description="事故位置")
    occurred_at: datetime = Field(description="发生时间")
    risk_level: RiskLevel = Field(description="风险等级")
    causes: list[str] = Field(default_factory=list, description="致灾因素")
    precursors: list[str] = Field(default_factory=list, description="前兆数据特征")
    actions: list[str] = Field(default_factory=list, description="处置动作")
    outcome: str = Field(description="处置结果")
    lessons: list[str] = Field(default_factory=list, description="经验教训")
    source: str = Field(description="数据来源")


class QueryRoofRiskCaseOutput(McpToolModel):
    """历史案例查询结果。"""
    cases: list[HistoricalCase] = Field(default_factory=list)
    total_matched: int = Field(default=0, ge=0)
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 3. query_safety_regulation — 查询安全规程
# ═══════════════════════════════════════════════════════════════════════════


class QuerySafetyRegulationInput(McpToolModel):
    """查询煤矿安全规程和防治规范。"""
    keywords: list[str] = Field(min_length=1, description="检索关键词列表")
    regulation_types: list[
        Literal[
            "safety_regulation",   # 煤矿安全规程
            "rock_burst",          # 冲击地压防治规范
            "emergency_plan",      # 应急预案
            "support_standard",    # 支护标准
            "expert_opinion",      # 专家研判
        ]
    ] = Field(default_factory=lambda: ["safety_regulation"], description="规程类型")
    mandatory_only: bool = Field(default=False, description="是否只返回强制性条款")
    top_k: int = Field(default=10, ge=1, le=30)


class RegulationClause(McpToolModel):
    """单条规程条款。"""
    regulation_id: str = Field(description="规程/文档标识")
    document: str = Field(description="规程名称")
    clause: str = Field(description="条款编号")
    requirement: str = Field(description="约束内容")
    mandatory: bool = Field(default=True, description="是否为强制性要求")
    applicable_conditions: list[str] = Field(default_factory=list, description="适用条件")
    source_type: str = Field(description="来源类型")
    source: str = Field(description="数据来源")


class QuerySafetyRegulationOutput(McpToolModel):
    """规程查询结果。"""
    clauses: list[RegulationClause] = Field(default_factory=list)
    total_matched: int = Field(default=0, ge=0)
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 4. query_available_workers — 查询可用人员
# ═══════════════════════════════════════════════════════════════════════════


class QueryAvailableWorkersInput(McpToolModel):
    """查询当班及可调度人员。"""
    mine_id: str = Field(description="矿井标识")
    roles: list[str] = Field(default_factory=list, description="筛选角色，为空返回全部")
    shift: Literal["current", "next", "all"] = Field(default="current", description="班次筛选")


class WorkerInfo(McpToolModel):
    """单个人员信息。"""
    worker_id: str = Field(description="人员标识")
    name: str = Field(description="姓名")
    role: str = Field(description="岗位角色")
    shift: str = Field(description="当前班次")
    location: str | None = Field(default=None, description="当前位置")
    certifications: list[str] = Field(default_factory=list, description="资质证书")
    available: bool = Field(default=True, description="是否可调度")


class QueryAvailableWorkersOutput(McpToolModel):
    """人员查询结果。"""
    workers: list[WorkerInfo] = Field(default_factory=list)
    total_available: int = Field(default=0, ge=0)
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 5. query_emergency_equipment — 查询应急装备与物资
# ═══════════════════════════════════════════════════════════════════════════


class QueryEmergencyEquipmentInput(McpToolModel):
    """查询应急设备与物资状态。"""
    mine_id: str = Field(description="矿井标识")
    equipment_types: list[
        Literal["support_material", "rescue_equipment", "communication", "transport", "medical"]
    ] = Field(default_factory=lambda: ["support_material"], description="物资/设备类型")
    status_filter: list[Literal["available", "in_use", "maintenance", "reserved"]] = Field(
        default_factory=lambda: ["available"], description="状态筛选"
    )


class EquipmentInfo(McpToolModel):
    """单个设备/物资信息。"""
    equipment_id: str = Field(description="物资/设备标识")
    name: str = Field(description="名称")
    equipment_type: str = Field(description="类型")
    quantity: float = Field(ge=0, description="数量")
    unit: str = Field(default="件", description="单位")
    location: str | None = Field(default=None, description="存放位置")
    status: str = Field(description="状态")
    last_checked_at: datetime | None = Field(default=None, description="最近检查时间")


class QueryEmergencyEquipmentOutput(McpToolModel):
    """应急装备查询结果。"""
    items: list[EquipmentInfo] = Field(default_factory=list)
    total_available: int = Field(default=0, ge=0)
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 6. create_warning_record — 创建预警记录
# ═══════════════════════════════════════════════════════════════════════════


class CreateWarningRecordInput(McpToolModel):
    """创建一条预警记录。"""
    warning_id: str = Field(description="预警唯一标识")
    workflow_run_id: str = Field(description="关联工作流运行ID")
    mine_id: str = Field(description="矿井标识")
    roadway_id: str = Field(description="巷道/工作面标识")
    risk_level: RiskLevel = Field(description="风险等级")
    risk_score: float = Field(ge=0.0, le=1.0, description="风险评分")
    summary: str = Field(description="预警摘要")
    affected_area: str = Field(default="", description="影响区域描述")
    causal_factors: list[str] = Field(default_factory=list, description="致灾因子列表")
    fast_alert: bool = Field(default=False, description="是否为快速告警")


class CreateWarningRecordOutput(McpToolModel):
    """预警记录创建结果。"""
    warning_id: str = Field(description="创建的预警 ID")
    created_at: datetime = Field(description="创建时间")
    status: Literal["created", "duplicate", "failed"] = Field(description="创建状态")
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 7. create_disposal_work_order — 创建处置工单
# ═══════════════════════════════════════════════════════════════════════════


class CreateDisposalWorkOrderInput(McpToolModel):
    """创建一条处置工单。"""
    warning_id: str = Field(description="关联预警ID")
    decision_id: str = Field(description="关联决策ID")
    plan_id: str = Field(description="处置方案ID")
    actions: list[str] = Field(min_length=1, description="处置动作列表")
    assigned_roles: list[str] = Field(default_factory=list, description="指派角色")
    deadline_minutes: int = Field(ge=0, description="处置时限（分钟）")
    requires_approval: bool = Field(default=False, description="是否需要审批")
    priority: Literal["normal", "urgent", "critical"] = Field(description="优先级")


class WorkOrderAction(McpToolModel):
    """工单中单个动作的状态。"""
    sequence: int = Field(ge=1, description="动作序号")
    action: str = Field(description="动作描述")
    status: Literal["pending", "assigned", "in_progress", "completed", "failed"] = Field(
        default="pending", description="执行状态"
    )
    assigned_to: str | None = Field(default=None, description="执行人")


class CreateDisposalWorkOrderOutput(McpToolModel):
    """处置工单创建结果。"""
    work_order_id: str = Field(description="工单ID")
    warning_id: str = Field(description="关联预警ID")
    status: Literal["pending", "dispatched", "rejected"] = Field(description="工单状态")
    actions: list[WorkOrderAction] = Field(default_factory=list)
    created_at: datetime = Field(description="创建时间")
    approval_required: bool = Field(default=False)
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 8. run_digital_twin_simulation — 运行数字孪生仿真
# ═══════════════════════════════════════════════════════════════════════════


class RunDigitalTwinSimulationInput(McpToolModel):
    """触发数字孪生仿真（对接成员 4 前端）。"""
    mine_id: str = Field(description="矿井标识")
    roadway_id: str = Field(description="巷道标识")
    simulation_type: Literal["stress_field", "displacement_field", "risk_overlay"] = Field(
        description="仿真类型"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="仿真参数（应力区间/位移范围/风险叠加选项）"
    )
    workflow_run_id: str = Field(description="关联工作流运行ID")


class SimulationResult(McpToolModel):
    """仿真计算结果。"""
    simulation_id: str = Field(description="仿真唯一标识")
    simulation_type: str = Field(description="仿真类型")
    status: Literal["running", "completed", "failed"] = Field(description="仿真状态")
    result_url: str | None = Field(default=None, description="前端渲染结果链接")
    summary: str = Field(description="仿真结果摘要")
    warnings: list[str] = Field(default_factory=list)


class RunDigitalTwinSimulationOutput(McpToolModel):
    """数字孪生仿真结果。"""
    simulation_id: str = Field(description="仿真ID")
    result: SimulationResult = Field(description="仿真结果")
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 9. query_disposal_result — 查询处置执行结果
# ═══════════════════════════════════════════════════════════════════════════


class QueryDisposalResultInput(McpToolModel):
    """查询处置方案的执行结果和回执。"""
    work_order_id: str | None = Field(default=None, description="按工单ID查询")
    warning_id: str | None = Field(default=None, description="按预警ID查询")
    decision_id: str | None = Field(default=None, description="按决策ID查询")

    @field_validator("work_order_id")
    @classmethod
    def at_least_one_id(cls, value: str | None, info) -> str | None:
        data = info.data
        if not value and not data.get("warning_id") and not data.get("decision_id"):
            raise ValueError("必须提供 work_order_id、warning_id 或 decision_id 中至少一个")
        return value


class DisposalFeedback(McpToolModel):
    """单条处置执行反馈。"""
    action: str = Field(description="处置动作")
    assigned_to: str | None = Field(default=None, description="执行人")
    status: Literal["completed", "in_progress", "failed", "pending"] = Field(description="执行状态")
    completed_at: datetime | None = Field(default=None, description="完成时间")
    result: str = Field(description="执行结果描述")
    issues: list[str] = Field(default_factory=list, description="执行中的问题")


class QueryDisposalResultOutput(McpToolModel):
    """处置结果查询输出。"""
    query_id: str = Field(description="查询关联的预警/决策/工单ID")
    overall_status: Literal["in_progress", "completed", "partial", "failed"] = Field(
        description="总体执行状态"
    )
    feedbacks: list[DisposalFeedback] = Field(default_factory=list)
    completion_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence: list[Evidence] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# 9 工具汇总清单（代码内自文档化）
# ═══════════════════════════════════════════════════════════════════════════

MCP_TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "query_sensor_history": {
        "description": "查询指定时间范围内指定测点的监测历史数据",
        "input_schema": QuerySensorHistoryInput,
        "output_schema": QuerySensorHistoryOutput,
        "category": "data_query",
        "read_only": True,
    },
    "query_roof_risk_case": {
        "description": "查询历史顶板事故案例，按风险等级/致灾因子匹配",
        "input_schema": QueryRoofRiskCaseInput,
        "output_schema": QueryRoofRiskCaseOutput,
        "category": "knowledge_retrieval",
        "read_only": True,
    },
    "query_safety_regulation": {
        "description": "查询煤矿安全规程、防治规范和应急预案条款",
        "input_schema": QuerySafetyRegulationInput,
        "output_schema": QuerySafetyRegulationOutput,
        "category": "knowledge_retrieval",
        "read_only": True,
    },
    "query_available_workers": {
        "description": "查询当班及可调度人员信息（角色/资质/位置）",
        "input_schema": QueryAvailableWorkersInput,
        "output_schema": QueryAvailableWorkersOutput,
        "category": "resource_query",
        "read_only": True,
    },
    "query_emergency_equipment": {
        "description": "查询应急装备与支护物资库存/状态",
        "input_schema": QueryEmergencyEquipmentInput,
        "output_schema": QueryEmergencyEquipmentOutput,
        "category": "resource_query",
        "read_only": True,
    },
    "create_warning_record": {
        "description": "创建一条预警记录（需审批）",
        "input_schema": CreateWarningRecordInput,
        "output_schema": CreateWarningRecordOutput,
        "category": "action",
        "read_only": False,  # 写操作，需权限校验
    },
    "create_disposal_work_order": {
        "description": "创建处置工单并下发（需审批）",
        "input_schema": CreateDisposalWorkOrderInput,
        "output_schema": CreateDisposalWorkOrderOutput,
        "category": "action",
        "read_only": False,  # 写操作，需权限校验
    },
    "run_digital_twin_simulation": {
        "description": "触发数字孪生仿真（应力场/位移场/风险叠加）",
        "input_schema": RunDigitalTwinSimulationInput,
        "output_schema": RunDigitalTwinSimulationOutput,
        "category": "simulation",
        "read_only": True,  # 仿真为只读操作
    },
    "query_disposal_result": {
        "description": "查询处置方案的执行回执和反馈",
        "input_schema": QueryDisposalResultInput,
        "output_schema": QueryDisposalResultOutput,
        "category": "feedback",
        "read_only": True,
    },
}
