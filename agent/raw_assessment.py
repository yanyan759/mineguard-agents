"""原始监测任务接入现有六节点，研究报警不伪装成五色现场等级。"""
from datetime import datetime, timezone

from app.schemas.workflow import (
    AgentStatus, AffectedArea, DataQuality, DispatchAction, DispatchDecisionResult,
    DispatchPlan, Evidence, PerceptionWarningResult, ResourceRequirements,
)
from app.services.raw_monitoring import RawMonitoringTask, analyze_raw


def perceive_records(state):
    started = datetime.now(timezone.utc)
    task = RawMonitoringTask.model_validate(state["custom_task"])
    assessment = analyze_raw(task, state["assessment_profile"]) if "assessment_profile" in state else analyze_raw(task)
    assessment["file_evidence"] = state.get("input_file_evidence", [])
    ctx = state["context"]
    result = PerceptionWarningResult(
        agent_name="感知预警 Agent", status=AgentStatus.PARTIAL, summary=assessment["summary"],
        confidence=None, evaluation_basis="raw_record_calculation",
        evidence=[Evidence(source_type="raw_monitoring_records", description="实际原始记录、单位和方法快照", value=assessment)],
        warnings=assessment["warnings"]+assessment["reasons"], errors=[], started_at=started,
        finished_at=datetime.now(timezone.utc), duration_ms=0, next_action="knowledge_retrieval",
        warning_id=ctx.warning_id or f"warning-{ctx.workflow_run_id}", risk_level=None, risk_score=None,
        model_version="raw-records-v1", threshold_version=task.method_id, model_mode="rule_based",
        affected_area=AffectedArea(mine_id=task.mine_id, roadway_id=task.roadway_id, locations=[task.area]),
        data_quality=DataQuality(completeness=1-task.missing_rate if task.missing_rate is not None else None,
                                 timeliness=None, missing_sources=["hardware_uptime"] if task.missing_rate is None else [],
                                 issues=assessment["reasons"]),
        recommended_recheck_minutes=None, fast_alert_required=False, assessment=assessment,
        triggered_rules=["research_threshold_exceeded"] if assessment["alarm"] else [],
    )
    return {"perception_result": result, "current_phase": "knowledge_retrieval",
            "messages": [{"role": "assistant", "content": result.summary}]}


def decide_from_records(state):
    perception = state["perception_result"]
    assessment = perception.assessment
    now = datetime.now(timezone.utc)
    actions = [DispatchAction(sequence=1, action="核对原始记录质量、统计口径和参数适用性，补充未评估事项",
                              owner_role="监测值班员", deadline_minutes=None, acceptance_criteria=assessment["reasons"] or ["记录核验依据"]),
               DispatchAction(sequence=2, action="结合现场监测和矿井预案研判处置；复查周期与措施由现场确定",
                              owner_role="矿总工程师", deadline_minutes=None, requires_approval=bool(assessment["alarm"]),
                              acceptance_criteria=["不能将研究报警当作批准执行方案"])]
    result = DispatchDecisionResult(
        agent_name="调度决策 Agent", status=AgentStatus.PARTIAL, confidence=None,
        evaluation_basis="raw_record_review", summary="已依据原始记录形成核验建议；现场风险未定级，未授权执行",
        evidence=perception.evidence, warnings=["未给定现场批准的复查时限与施工参数"],
        started_at=now, finished_at=now, duration_ms=0, next_action="resource_evaluation",
        decision_id=f"decision-{perception.warning_id}", warning_id=perception.warning_id, decision_level=None,
        objectives=["核验实测指标与现场适用性"], primary_plan=DispatchPlan(plan_id=f"plan-{perception.warning_id}", name="原始记录核验建议", actions=actions),
        resource_requirements=ResourceRequirements(), human_approval_required=bool(assessment["alarm"]),
        mandatory_constraints=["禁止据研究结果自动解除风险"], prohibited_actions=["未经现场研判自动下发生产指令"],
        decision_rationale=[assessment["summary"]],
    )
    return {"decision_result": result, "current_phase": "resource_evaluation",
            "messages": [{"role": "assistant", "content": result.summary}]}
