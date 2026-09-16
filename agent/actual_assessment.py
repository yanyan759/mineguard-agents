"""实际数据节点：只消费服务端冻结的输入、配置和资料，不读取演示数据。"""
from datetime import datetime, timezone
from app.schemas.workflow import (
    AffectedArea, DataQuality, DispatchAction, DispatchDecisionResult, DispatchPlan,
    Evidence, KnowledgeRetrievalResult, PerceptionWarningResult, ResourceEvaluationResult,
    ResourceGap, ResourceRequirements, SimilarCase, RegulationReference, SupportRecommendation,
)


def common(name, summary, next_action, evidence, status="success"):
    now = datetime.now(timezone.utc)
    return dict(agent_name=name, summary=summary, next_action=next_action, evidence=evidence,
                status=status, confidence=None, evaluation_basis="actual_snapshot", started_at=now,
                finished_at=now, duration_ms=0)


def reference(asset):
    return Evidence(source_type="uploaded_"+asset["category"], source_id=asset["asset_id"],
                    description=asset["title"], value={k:asset[k] for k in
                    ("sha256", "version", "source", "mine_id", "roadway_id", "area", "observed_at")})


def output(key, result):
    return {key:result, "current_phase":result.next_action,
            "messages":[{"role":"assistant","content":result.summary}]}


def perceive(state):
    # 直接图调用也经过公共准备入口，检查点中保留有效输入。
    snapshot = state.get("actual_snapshot")
    if snapshot is None:
        import json
        from app.services.monitoring_task import parse_monitoring_task
        from app.services.mine_configuration import prepare_actual
        snapshot = prepare_actual(parse_monitoring_task(json.dumps(state["custom_task"], ensure_ascii=False)))
    task, assessment = snapshot["task"], dict(snapshot["assessment"])
    assessment["file_evidence"] = state.get("input_file_evidence", [])
    config = snapshot["configuration"] or {}
    evidence = [Evidence(source_type="actual_monitoring", source_id=snapshot["snapshot_id"],
                         description="本次有效输入、参数版本与观测计算", value=assessment)]
    result = PerceptionWarningResult(**common("感知预警 Agent", assessment["summary"], "knowledge_retrieval", evidence),
        warning_id="warning-"+state["context"].workflow_run_id, risk_level=assessment.get("risk_level"),
        risk_score=assessment.get("risk_score"), assessment=assessment, model_version=assessment["method_id"],
        threshold_version=config.get("version", assessment["method_id"]), model_mode="rule_based",
        affected_area=AffectedArea(mine_id=task["mine_id"],roadway_id=task["roadway_id"],locations=[task["area"]]),
        data_quality=DataQuality(completeness=1-task["missing_rate"] if task.get("missing_rate") is not None else None,
                                 timeliness=None, issues=assessment["reasons"]),
        recommended_recheck_minutes=config.get("recheck_minutes"), fast_alert_required=bool(assessment["alarm"]),
        triggered_rules=[str(x) for x in assessment.get("triggered_rules",[])], warnings=assessment.get("warnings",[]))
    return {**output("perception_result",result),"actual_snapshot":snapshot,"custom_task":task}


def knowledge(state):
    snapshot = state["actual_snapshot"]
    cases, regulations, support, used = [], [], [], []
    query = (state.get("user_query") or "") + " ".join(state["perception_result"].triggered_rules)
    for asset in snapshot["documents"]:
        if asset["category"] == "resources":
            continue
        doc = asset["parsed"]
        # 配置已按测区明确选定资料；关键词只用于解释匹配，绝不编造相似度。
        hits = [term for term in doc["terms"] if term in query]
        used.append(reference(asset))
        if asset["category"] == "case":
            cases.append(SimilarCase(case_id=asset["asset_id"], match_terms=hits, source_status="user_scoped_document",
                                    outcome=doc["text"],source=asset["source"]))
        elif asset["category"] == "regulation":
            regulations.append(RegulationReference(document=asset["title"],requirement=doc["text"],source=asset["source"],
                                                   mandatory=False, applicability="按矿井、巷道、测区及配置版本选用"))
        else:
            support.append(SupportRecommendation(scheme_id=asset["asset_id"],scheme_name=asset["title"],
                applicable_conditions=[asset["area"]],actions=[x["action"] for x in doc["actions"]],
                expert_review=doc["text"],source=asset["source"]))
    result = KnowledgeRetrievalResult(**common("知识检索 Agent",f"已引用本测区配置的{len(cases)}份案例、{len(regulations)}份规程、{len(support)}份方案",
                                               "dispatch_decision",used),warning_id=state["perception_result"].warning_id,
        similar_cases=cases, regulations=regulations,support_recommendations=support,
        missing_topics=[name+"待上传并选入配置" for name, items in [("案例",cases),("规程",regulations),("支护方案",support)] if not items])
    return output("knowledge_result", result)


def decide(state):
    snapshot, perception = state["actual_snapshot"],state["perception_result"]
    plan = snapshot["plan"]
    config = snapshot["configuration"] or {}
    if plan:
        actions = [DispatchAction.model_validate(a) for a in plan["parsed"]["actions"]]
        evidence = [reference(plan), *state["knowledge_result"].evidence]
        name = plan["title"]
    else:
        actions = [DispatchAction(sequence=1,action="复查本次观测指标并记录现场检查结果", owner_role="监测值班员",
                                  deadline_minutes=config.get("recheck_minutes"), acceptance_criteria=["保留原始记录和检查依据"])]
        evidence = perception.evidence
        name = "观测复查建议"
    requirements = {"personnel":[],"materials":[],"equipment":[]}
    for row in plan["parsed"]["requirements"] if plan else []:
        requirements["materials" if row["group"]=="support_materials" else row["group"]].append(f"{row['name']} {row['required']}{row['unit']}")
    result = DispatchDecisionResult(**common("调度决策 Agent",f"依据本次配置与资料生成{name}：{len(actions)}项动作", "resource_evaluation",evidence),
        decision_id="decision-"+perception.warning_id, warning_id=perception.warning_id, decision_level=perception.risk_level,
        primary_plan=DispatchPlan(plan_id=plan["asset_id"] if plan else "review-"+perception.warning_id,name=name,actions=actions),
        resource_requirements=ResourceRequirements(**requirements),
        human_approval_required=perception.fast_alert_required or any(a.requires_approval for a in actions),
        decision_rationale=[perception.summary, "动作及数量按选定方案原文执行核算" if plan else "提供结构化方案后可核算完整需求"],
        mandatory_constraints=["生产工单须由具备本矿权限的人员核验方案及现场条件后单独提交"])
    return output("decision_result",result)


def resources(state):
    snapshot=state["actual_snapshot"]
    quantities=snapshot["task"].get("resources")
    gaps=[]
    for group, items in (quantities or {}).items():
        for item in items:
            if item["available"] < item["required"]:
                gaps.append(ResourceGap(resource_type="material" if group=="support_materials" else group,
                    resource_name=item["name"],required=item["required"],available=item["available"],unit=item["unit"],reason="可用数量小于方案需求"))
    quantity_check="not_provided" if not quantities else "insufficient" if gaps else "sufficient"
    evidence=[Evidence(source_type="actual_resource_comparison",source_id=snapshot["snapshot_id"],description="按冻结库存与本次方案逐项核算",
                       value={"comparison":quantities,"inventory":snapshot.get("inventory_evidence"),"plan_asset_id":snapshot["plan"]["asset_id"] if snapshot["plan"] else None})]
    evidence.extend(reference(a) for a in snapshot["documents"] if a["category"]=="resources" and snapshot.get("inventory_asset_id")==a["asset_id"])
    result=ResourceEvaluationResult(**common("资源评估 Agent",{"sufficient":"资源数量已核算，满足所列需求；可进入现场条件核验",
        "insufficient":"资源数量已核算，存在实际缺口，请调整资源后重新运行","not_provided":"资源台账及方案需求待配置"}[quantity_check],"collaborative_control",evidence),
        evaluation_id="eval-"+state["context"].workflow_run_id,plan_id=state["decision_result"].primary_plan.plan_id,
        feasibility="infeasible" if gaps else "unknown", quantity_check=quantity_check,execution_readiness="unverified",resource_gaps=gaps,
        blocking_issues=[f"{g.resource_name}：需求{g.required}{g.unit}，可用{g.available}{g.unit}" for g in gaps],
        redecision_required=False)
    return output("resource_result",result)
