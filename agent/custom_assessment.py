"""自定义监测摘要的证据与建议：计算、资料引用、现场授权分别表达。"""

from datetime import datetime, timezone

from app.schemas.workflow import (
    AgentStatus, CollaborativeControlResult, DispatchAction, DispatchDecisionResult,
    DispatchPlan, Evidence, KnowledgeRetrievalResult, NotificationResult,
    PermissionCheck, RegulationReference, ResourceRequirements, RiskLevel, SimilarCase,
)
from app.services.verified_knowledge import retrieve_verified_knowledge


def knowledge_from_documents(state):
    """自定义路径只消费可追溯资料，保留缺项和适用性限制。"""
    started = datetime.now(timezone.utc)
    perception = state["perception_result"]
    terms = [state.get("user_query") or "微震", *[factor.factor for factor in perception.causal_factors]]
    data = retrieve_verified_knowledge(terms)
    finished = datetime.now(timezone.utc)
    warnings = list(data.missing_topics)
    if data.similar_cases:
        warnings.append("案例为本地整理摘要，附官方来源供复核；关键词命中不等于语义相似度或因果证明")
    if data.regulations:
        warnings.append("法规摘录已核对原文；条文适用范围和本矿现行要求仍须核验")
    result = KnowledgeRetrievalResult(
        agent_name="知识检索 Agent", status=AgentStatus.PARTIAL,
        summary=f"找到{len(data.regulations)}条可追溯法规摘录、{len(data.similar_cases)}份相关事故资料；现场适用性待核验",
        confidence=None, evaluation_basis="traceable_documents",
        evidence=[Evidence(source_type=data.source_type, description=data.evidence_description)],
        warnings=warnings, errors=[], started_at=started, finished_at=finished,
        duration_ms=round((finished-started).total_seconds()*1000), next_action="dispatch_decision",
        warning_id=perception.warning_id,
        similar_cases=[SimilarCase(**row) for row in data.similar_cases],
        regulations=[RegulationReference(**row) for row in data.regulations],
        support_recommendations=[], missing_topics=data.missing_topics,
    )
    return {"knowledge_result": result, "current_phase": "dispatch_decision",
            "messages": [{"role": "assistant", "content": result.summary}]}


def decision_from_observations(state):
    """由实际预警等级生成可核验的初步建议，不编造施工参数或规范时限。"""
    started = datetime.now(timezone.utc)
    perception = state["perception_result"]
    knowledge = state.get("knowledge_result")
    interval = perception.recommended_recheck_minutes
    risk = perception.risk_level
    urgent = risk in {RiskLevel.ORANGE, RiskLevel.RED}
    actions = [DispatchAction(
        sequence=1, action=f"建议在{interval}分钟内复查微震频次、能量和b值，并核对原始监测记录；周期需现场确认",
        owner_role="监测值班员", deadline_minutes=interval,
        acceptance_criteria=["提供复查时间、原始记录和指标变化，核验缺失数据"],
    )]
    actions.append(DispatchAction(
        sequence=2, action="核验本矿预警临界指标、矿井灾害鉴定与监测覆盖范围，确认风险初评是否适用",
        owner_role="矿总工程师", deadline_minutes=interval,
        acceptance_criteria=["提供经现场确定的阈值及审批版本，核对相关规程适用范围"],
    ))
    if urgent:
        actions.append(DispatchAction(
            sequence=3, action="将高风险初评和异常指标提交现场应急负责人，依据本矿预案研判处置，需人工确认系统建议",
            owner_role="矿总工程师", deadline_minutes=interval, requires_approval=True,
            acceptance_criteria=["记录现场研判意见；现场紧急避险不以本软件审批完成为前提"],
        ))
    if risk in {RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED}:
        actions.append(DispatchAction(
            sequence=len(actions)+1, action="核对支护现状与异常区域，补充地质和支护设计后由专业人员确定加固措施",
            owner_role="支护工程师", deadline_minutes=interval,
            acceptance_criteria=["提供现场检查及专项设计，不照搬历史事故的施工参数"],
        ))
    evidence = [Evidence(source_type="rule_based", description=f"风险{risk.value}、评分{perception.risk_score}；复查周期来自simulation-v1演示规则，非法定时限")]
    for reference in knowledge.regulations if knowledge else []:
        evidence.append(Evidence(source_type="official_regulation", source_id=reference.clause,
                                 description=f"{reference.clause}：{reference.requirement}",
                                 value={"source_url": reference.source_url, "applicability": reference.applicability}))
    finished = datetime.now(timezone.utc)
    warnings = ["初步建议由规则生成，尚未经过本矿现场验证；责任岗位与时限需现场确认", "未生成卸压、爆破或支护的具体施工参数"]
    if not knowledge or not knowledge.regulations:
        warnings.append("未取得已核验法规证据，目前只能建议补充数据及人工复核")
    result = DispatchDecisionResult(
        agent_name="调度决策 Agent", status=AgentStatus.PARTIAL, confidence=None,
        evaluation_basis="rule_based_draft", summary=f"依据{risk.value}初评生成{len(actions)}项复查与核验建议，尚非批准执行方案",
        evidence=evidence, warnings=warnings, started_at=started, finished_at=finished,
        duration_ms=round((finished-started).total_seconds()*1000), next_action="resource_evaluation",
        decision_id=f"decision-{perception.warning_id}", warning_id=perception.warning_id, decision_level=risk,
        objectives=["核实异常指标与适用阈值，再确认现场处置方案"],
        primary_plan=DispatchPlan(plan_id=f"plan-{perception.warning_id}", name="复查与现场核验建议", actions=actions),
        resource_requirements=ResourceRequirements(personnel=["监测值班员", "矿总工程师"], equipment=["微震监测设备"]),
        mandatory_constraints=["法规按明确适用条件使用", "系统不得自行解除预警或恢复生产"],
        prohibited_actions=["不得将演示评分当作现场安全许可", "不得由系统自动执行停产、断电或撤人指令"],
        human_approval_required=urgent,
        decision_rationale=[perception.summary, "建议复查周期与感知节点一致，依据和适用性分开记录"],
    )
    return {"decision_result": result, "current_phase": "resource_evaluation",
            "messages": [{"role": "assistant", "content": result.summary}]}


def record_control_review(state):
    """记录高风险建议的人工意见；缺少可信身份与真实通道时保持未授权、未下发。"""
    decision = state["decision_result"]
    perception = state["perception_result"]
    high_risk = decision.human_approval_required or perception.fast_alert_required
    require_approval = False
    try:
        from langgraph.config import get_config
        require_approval = bool(get_config().get("configurable", {}).get("require_approval"))
    except RuntimeError:
        pass
    review = None
    if high_risk and require_approval:
        from langgraph.types import interrupt
        review = interrupt({
            "type": "approval_request", "coordination_id": f"coord-{decision.decision_id}",
            "warning_id": perception.warning_id, "required_role": "矿总工程师",
            "risk_level": perception.risk_level.value if perception.risk_level else "research_alert",
            "dispatched_actions": [action.action for action in decision.primary_plan.actions],
            "notice": "此操作仅记录研判意见；身份权限、资源条件和真实执行通道尚未核验，批准后也不自动下发现场指令。现场紧急避险不以本软件审批完成为前提。",
        })
    rejected = review and review.get("decision") in ("rejected", "cancelled")
    identity = review.get("verified_identity") if review else None
    started = datetime.now(timezone.utc)
    recipients = ["监测值班员", "矿总工程师"] if high_risk else ["监测值班员"]
    result = CollaborativeControlResult(
        agent_name="协同管控 Agent", status=AgentStatus.PARTIAL, confidence=None,
        evaluation_basis="review_record_only",
        summary=f"拟通知{len(recipients)}个岗位，实际未发送；" + ("已记录人工意见，未下发执行" if review else "待现场核验与人员确认，未下发执行"),
        evidence=[Evidence(source_type="review_record", description="人工意见仅作记录，不等同身份授权", value=review)],
        warnings=["研判人身份已由业务提供方验证；不等同生产方案批准" if identity else "未提供可信身份凭据，权限未核验",
                  "此次工作流未发送外部通知或创建生产工单；可在业务接入区查询接口状态"],
        started_at=started, finished_at=started, duration_ms=0, next_action="reflection_iteration",
        coordination_id=f"coord-{decision.decision_id}",
        permission_check=PermissionCheck(passed=None, required_role="矿总工程师" if high_risk else "调度室主任",
                                         actual_role="、".join(identity["roles"]) if identity else None),
        notification_results=[NotificationResult(recipient=name, channel="web", status="pending") for name in recipients],
        dispatch_status="rejected" if rejected else "not_dispatched", dispatched_actions=[], human_action_required=True,
    )
    return {"control_result": result, "current_phase": "reflection_iteration",
            "messages": [{"role": "assistant", "content": result.summary}]}
