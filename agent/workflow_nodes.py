"""六 Agent 工作流节点函数。

每个节点接收 ``WorkflowState``，执行对应 Agent 的确定性规则和可选模型增强，
返回更新后的状态片段。所有结果都写回共享状态并附带证据与审计信息。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agent.agent_subgraph import is_llm_node_enabled, run_llm_subgraph
from app.agent.llm_service import llm_call_structured

from app.schemas.workflow import (
    AffectedArea,
    AgentStatus,
    CausalFactor,
    CollaborativeControlResult,
    DataQuality,
    DispatchDecisionResult,
    Evidence,
    KnowledgeRetrievalResult,
    PerceptionWarningResult,
    ReflectionIterationResult,
    ResourceEvaluationResult,
    ReturnDecision,
    RiskChange,
    RiskLevel,
    SimilarCase,
    RegulationReference,
    SupportRecommendation,
    DispatchPlan,
    DispatchAction,
    ResourceRequirements,
    ResourceGap,
    PermissionCheck,
    NotificationResult,
)
from app.agent.knowledge_sources import (
    KnowledgeSourceError,
    retrieve_knowledge,
    write_knowledge_to_graph,
    writeback_result,
)
from app.agent.workflow_state import WorkflowState


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _microseismic_feature_rows(perception_input) -> list[dict]:
    """把微震事件按测区聚合为 SensorFeatureRow 兼容的十维特征行（成员 1 联调用）。

    每测区聚合一行：count/mean/std/slope/min/max/range/cv/b_value/能量，
    满足 schemas/member1_interface.SensorFeatureRow 契约（缺失字段留 None）。
    """
    events = perception_input.microseismic_events or []
    if not events:
        return []
    by_area: dict[str, list] = {}
    for e in events:
        by_area.setdefault(e.area, []).append(e)

    rows: list[dict] = []
    for area, evs in by_area.items():
        energies = [e.energy_j for e in evs]
        n = len(evs)
        mean = sum(energies) / n
        std = (sum((x - mean) ** 2 for x in energies) / n) ** 0.5 if n > 1 else 0.0
        t0, t1 = evs[0].timestamp, evs[-1].timestamp
        dt_min = max(1e-9, (t1 - t0).total_seconds() / 60.0)
        slope = (energies[-1] - energies[0]) / dt_min
        rows.append({
            "point_id": area,
            "timestamp": t1.isoformat(),
            "sensor_type": "microseismic",
            "count": n,
            "mean": mean,
            "std": std,
            "slope": slope,
            "skewness": None,
            "kurtosis": None,
            "min_value": min(energies),
            "max_value": max(energies),
            "range_value": max(energies) - min(energies),
            "coefficient_of_variation": (std / mean) if mean else None,
            "b_value": sum(e.b_value for e in evs) / n,
            "energy_j": mean,
            "source_x": sum(e.source_coordinate[0] for e in evs) / n,
            "source_y": sum(e.source_coordinate[1] for e in evs) / n,
            "source_z": sum(e.source_coordinate[2] for e in evs) / n,
        })
    return rows


def _build_prediction_augmentation(perception_input) -> dict | None:
    """调用成员 1 MOA-Transformer 预测模型，返回预测增强字段（不参与风险定级）。

    仅当 PREDICTION_MODEL_URL 配置（真实联调）时调用；未配置 / 无微震事件
    返回 None。红线：prediction_result 仅作增强观测（追加证据/警告），
    risk_level/risk_score 始终由规则引擎输出，预测值绝不覆盖。
    """
    from app.tools.prediction_model_tool import _get_model_url, run_prediction

    if not _get_model_url():
        return None  # 未配置成员 1 服务：不产生预测增强（离线模式）
    rows = _microseismic_feature_rows(perception_input)
    if not rows:
        return None
    features_json = json.dumps(rows, ensure_ascii=False, default=str)
    # run_prediction 是 @tool 装饰的 StructuredTool，须用 .invoke() 调用（直接 () 会抛
    # TypeError: 'StructuredTool' object is not callable）
    out = run_prediction.invoke({
        "features_json": features_json,
        "forecast_horizon_minutes": 10,
    })
    return {
        "model_mode": out.get("model_mode"),   # online | degraded | offline
        "data": out.get("data"),
        "error": out.get("error"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. 感知预警节点
# ═══════════════════════════════════════════════════════════════════════════


def node_perception_warning(state: WorkflowState) -> dict:
    """调用规则引擎分析监测数据，生成分级预警结果。"""
    if state.get("custom_task"):
        from app.agent.actual_assessment import perceive
        return perceive(state)
    if (state.get("custom_task") or {}).get("input_type") == "raw_events":
        from app.agent.raw_assessment import perceive_records
        return perceive_records(state)
    from app.services.perception_warning_service import PerceptionWarningService

    # 自定义摘要优先，后续回退也读取同一状态，绝不重新套用 fixture。
    custom_task = state.get("custom_task")
    perception_input = None
    fixture_id = state.get("fixture_scenario_id")
    if custom_task:
        from app.services.monitoring_task import MonitoringTask
        task = MonitoringTask.model_validate(custom_task)
    elif fixture_id:
        from app.agent.fixture_loader import FixtureDataSource
        loader = FixtureDataSource()
        perception_input = loader.load_scenario(fixture_id)
    else:
        # 回退模式：使用 state.context 中的基础数据
        return {
            "errors": state.get("errors", []) + ["无 fixture 数据源"],
            "current_phase": "error",
        }

    ctx = state["context"]
    svc = PerceptionWarningService()
    analysis = svc.analyze_summary(task) if custom_task else svc.analyze(perception_input)

    # ── 规则定级（红线）：风险等级/评分只来自规则引擎，LLM 仅解释，绝不反写 ──
    summary = _describe_risk(analysis.risk_level, analysis.risk_score)
    if is_llm_node_enabled("perception_warning"):
        llm_summary = _llm_warning_explanation(analysis)
        if llm_summary:
            summary = llm_summary
    if custom_task:
        summary = "用户摘要初评（simulation-v1）：" + summary
        if analysis.quality.is_partial:
            summary += "；部分指标缺失或质量不足，需复核后确认风险"

    started = _now()
    result = PerceptionWarningResult(
        agent_name="感知预警 Agent",
        status=AgentStatus.PARTIAL if custom_task else AgentStatus.SUCCESS,
        summary=summary,
        confidence=None if custom_task else min(0.95, max(0.0,
            analysis.quality.completeness * analysis.quality.timeliness * 0.9)),
        evaluation_basis="user_summary_rule_score" if custom_task else "simulation",
        evidence=[Evidence(
            source_type="user_monitoring_summary", description="实际参与评分的用户监测摘要（未生成虚构事件）",
            value=custom_task,
        )] if custom_task else [
            Evidence(source_type="microseismic_monitor",
                     description=f"微震事件 {len(perception_input.microseismic_events)} 条",)
        ],
        warnings=analysis.quality.warnings,
        errors=analysis.quality.critical_errors + analysis.quality.issues,
        started_at=started,
        finished_at=_now(),
        duration_ms=100,
        next_action="knowledge_retrieval" if analysis.risk_level != RiskLevel.NORMAL else "finish",
        warning_id=ctx.warning_id or f"warning-{ctx.workflow_run_id}",
        risk_level=analysis.risk_level,
        risk_score=analysis.risk_score,
        model_version="user-summary-v1" if custom_task else perception_input.model_version,
        threshold_version=svc.threshold_version,
        model_mode=svc.model_mode,
        affected_area=AffectedArea(
            mine_id=ctx.mine_id, roadway_id=ctx.roadway_id,
            locations=[task.area] if custom_task else sorted({e.area for e in perception_input.microseismic_events}),
        ),
        causal_factors=[
            CausalFactor(factor=f.factor, importance=f.importance,
                         trend=f.trend, evidence=f.evidence)
            for f in analysis.causal_factors
        ],
        anomalies=analysis.anomalies,
        data_quality=DataQuality(
            completeness=analysis.quality.completeness,
            timeliness=analysis.quality.timeliness,
            missing_sources=analysis.quality.missing_sources,
            issues=analysis.quality.issues + analysis.quality.critical_errors,
        ),
        triggered_rules=analysis.triggered_rules,
        fast_alert_required=analysis.risk_level in {RiskLevel.ORANGE, RiskLevel.RED},
        recommended_recheck_minutes={
            RiskLevel.NORMAL: 60, RiskLevel.BLUE: 30, RiskLevel.YELLOW: 15,
            RiskLevel.ORANGE: 10, RiskLevel.RED: 5,
        }.get(analysis.risk_level, 60),
    )

    # ── 成员 1 联调增强：MOA-Transformer 预测（可选，配置 PREDICTION_MODEL_URL 时启用）──
    # 预测仅作增强观测追加到 evidence/warnings，绝不改写 risk_level/risk_score（红线）
    # 摘要没有模型所需的逐条观测，不制造时间序列调用预测模型。
    prediction_out = None if custom_task else _build_prediction_augmentation(perception_input)
    if prediction_out is not None:
        pdata = prediction_out.get("data") or {}
        if prediction_out.get("error"):
            result.warnings.append(
                f"预测模型调用异常（不中断主链路）：{prediction_out['error']}"
            )
        elif pdata.get("forecasts"):
            f0 = pdata["forecasts"][0]
            result.evidence.append(Evidence(
                source_type="prediction_model",
                description=(
                    f"MOA-Transformer 预测 {f0.get('forecast_minutes')}min 风险概率 "
                    f"{float(f0.get('risk_probability', 0)):.2f}（{pdata.get('model_status', 'unknown')}）"
                ),
            ))
            if pdata.get("top_causal_factors"):
                result.warnings.append(
                    "预测模型致灾因子："
                    + "、".join(str(x) for x in pdata["top_causal_factors"][:3])
                )
        result.warnings.append(f"预测模型模式：{prediction_out.get('model_mode')}")

    return {
        "perception_result": result,
        "current_phase": "knowledge_retrieval",
        "fast_alert_triggered": result.fast_alert_required,
        "prediction_result": prediction_out,
        "messages": [{"role": "assistant", "content": f"感知预警完成：风险等级 {result.risk_level.value}，评分 {result.risk_score}"}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. 知识检索节点
# ═══════════════════════════════════════════════════════════════════════════


def node_knowledge_retrieval(state: WorkflowState) -> dict:
    """知识检索：LLM 子图增强（LLM_NODES=on），否则/失败回退确定性（model_mode 标记）。"""
    if state.get("custom_task"):
        from app.agent.actual_assessment import knowledge
        return knowledge(state)
    if not is_llm_node_enabled("knowledge_retrieval"):
        return _knowledge_retrieval_deterministic(state)
    try:
        out = run_llm_subgraph("knowledge_retrieval", KnowledgeRetrievalResult, state)
        return _knowledge_llm_return(out["result"], state)
    except Exception as exc:
        print(f"[workflow_nodes] 知识检索 LLM 增强失败，回退确定性: {exc}")
        base = _knowledge_retrieval_deterministic(state)
        base["model_mode"] = "deterministic_fallback"
        return base


def _knowledge_llm_return(result: KnowledgeRetrievalResult, state: WorkflowState) -> dict:
    """LLM 知识检索结果 → 节点返回片段（补 current_phase/messages/model_mode）。"""
    return {
        "knowledge_result": result,
        "current_phase": "dispatch_decision",
        "model_mode": "llm",
        "messages": [{"role": "assistant", "content": f"知识检索（LLM）：{result.summary}"}],
    }


def _knowledge_retrieval_deterministic(state: WorkflowState) -> dict:
    """知识检索：配置驱动双轨（YAML 回退 / Neo4j 图谱），输出带来源的检索结果。"""
    perception = state.get("perception_result")
    if perception is None:
        return {"errors": state.get("errors", []) + ["缺少感知预警结果"]}

    risk_level = perception.risk_level
    factors = [f.factor for f in (perception.causal_factors or [])]
    mine_ctx = state.get("mine_context")  # 可选地质筛选上下文

    try:
        data = retrieve_knowledge(
            risk_level=risk_level,
            warning_id=perception.warning_id,
            factors=factors,
            mine_context=mine_ctx,
        )
    except KnowledgeSourceError as exc:
        # 运行期失败表面：返回 status=failed 合法结果（错误进 state，不静默回退 YAML）
        return _knowledge_failure_result(perception, exc)

    similar = [SimilarCase(**item) for item in data.similar_cases]
    regulations = [RegulationReference(**item) for item in data.regulations]
    support_recs = [SupportRecommendation(**item) for item in data.support_recommendations]
    missing = data.missing_topics

    started = _now()
    result = KnowledgeRetrievalResult(
        agent_name="知识检索 Agent",
        status=AgentStatus.PARTIAL if missing else AgentStatus.SUCCESS,
        summary=f"检索到 {len(similar)} 个相似案例、{len(regulations)} 条规程、{len(support_recs)} 个支护方案",
        confidence=0.65 if missing else 0.82,
        evidence=[Evidence(source_type=data.source_type, description=data.evidence_description)],
        warnings=[_warnings_prefix(data.source_type)] + missing,
        errors=[],
        started_at=started,
        finished_at=_now(),
        duration_ms=50,
        next_action="dispatch_decision",
        warning_id=perception.warning_id,
        similar_cases=similar,
        regulations=regulations,
        support_recommendations=support_recs,
        knowledge_conflicts=[],
        missing_topics=missing,
    )

    return {
        "knowledge_result": result,
        "current_phase": "dispatch_decision",
        "messages": [{"role": "assistant", "content": f"知识检索完成：{len(similar)} 案例，{len(regulations)} 规程"}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. 调度决策节点
# ═══════════════════════════════════════════════════════════════════════════


def node_dispatch_decision(state: WorkflowState) -> dict:
    """调度决策：LLM 子图增强（LLM_NODES=on），否则/失败回退确定性（model_mode 标记）。"""
    if state.get("custom_task"):
        from app.agent.actual_assessment import decide
        return decide(state)
    if (state.get("custom_task") or {}).get("input_type") == "raw_events":
        from app.agent.raw_assessment import decide_from_records
        return decide_from_records(state)
    if state.get("custom_task"):
        from app.agent.custom_assessment import decision_from_observations
        return decision_from_observations(state)
    if not is_llm_node_enabled("dispatch_decision"):
        return _dispatch_decision_deterministic(state)
    try:
        out = run_llm_subgraph("dispatch_decision", DispatchDecisionResult, state)
        return _dispatch_llm_return(out["result"], state)
    except Exception as exc:
        print(f"[workflow_nodes] 调度决策 LLM 增强失败，回退确定性: {exc}")
        base = _dispatch_decision_deterministic(state)
        base["model_mode"] = "deterministic_fallback"
        return base


# 动作黑名单（设计 spec §6.2 后置校验）：大模型不得自动解除预警/停产/断电/撤人。
# 命中黑名单的动作强制 requires_approval=True；红色预警强制人工审批（不信任 LLM 自报）。
_BLACKLIST_KEYWORDS = ("解除预警", "停产", "断电", "撤人")


def _postprocess_dispatch(result: DispatchDecisionResult, state: WorkflowState) -> None:
    """调度决策 LLM 结果后置校验（原地修改，安全红线确定性兜底）。

    - 动作黑名单：命中「解除预警/停产/断电/撤人」的动作强制 requires_approval=True；
      主方案含命中动作 → 强制 human_approval_required=True（协同管控节点据此进入人工审批）。
    - 红色预警强制 human_approval_required=True（与确定性语义一致，不信任 LLM 自报审批状态）。
    - prohibited_actions 兜底补齐黑名单文案（LLM 可能漏写）。
    """
    perception = state.get("perception_result")
    if perception is not None and perception.risk_level == RiskLevel.RED:
        result.human_approval_required = True

    force_warned = False
    plans = [result.primary_plan] + list(result.alternative_plans)
    for plan in plans:
        if plan is None:
            continue
        for action in plan.actions:
            if any(kw in action.action for kw in _BLACKLIST_KEYWORDS):
                action.requires_approval = True
                force_warned = True
                if plan is result.primary_plan:
                    result.human_approval_required = True

    if force_warned and "检测到高风险动作，已强制要求人工审批" not in (result.warnings or []):
        result.warnings = list(result.warnings or []) + ["检测到高风险动作，已强制要求人工审批"]

    text = "".join(result.prohibited_actions)
    if not any(kw in text for kw in ("停产", "断电", "撤人")):
        result.prohibited_actions.append("未经审批擅自实施停产、断电或撤人")
    if not any(kw in text for kw in ("解除预警", "恢复生产")):
        result.prohibited_actions.append("未经人工复核自动解除预警或恢复生产")


def _dispatch_llm_return(result: DispatchDecisionResult, state: WorkflowState) -> dict:
    """LLM 调度决策结果 → 节点返回片段（补 current_phase/messages/model_mode）。

    先过 _postprocess_dispatch 后置校验（安全红线：动作黑名单 + 红色强制审批）。
    """
    _postprocess_dispatch(result, state)
    return {
        "decision_result": result,
        "current_phase": "resource_evaluation",
        "model_mode": "llm",
        "messages": [{"role": "assistant", "content": f"调度决策（LLM）：{result.summary}"}],
    }


def _dispatch_decision_deterministic(state: WorkflowState) -> dict:
    """融合预警+知识，生成分级处置方案。"""
    perception = state.get("perception_result")
    knowledge = state.get("knowledge_result")
    op_ctx = state.get("operational_context")  # 可选的班次与现场运行上下文

    if perception is None:
        return {"errors": state.get("errors", []) + ["缺少感知预警结果"]}

    risk = perception.risk_level

    # 从知识结果中提取参考行动
    ref_actions = []
    for case in (knowledge.similar_cases if knowledge else []):
        ref_actions.extend(case.actions)

    # 演示场景使用配置内置的动作模板；实际任务必须引用已核验的处置方案资产。
    # 可选模型只补充解释，风险和动作约束仍由确定性规则与配置决定。
    if risk == RiskLevel.RED:
        actions = [
            DispatchAction(sequence=1, action="立即启动全矿应急广播", owner_role="调度室主任", deadline_minutes=3, requires_approval=False),
            DispatchAction(sequence=2, action="撤出危险区域全部人员", owner_role="安全副矿长", deadline_minutes=10, requires_approval=True),
            DispatchAction(sequence=3, action="启动应急预案，通知矿总工程师", owner_role="调度室主任", deadline_minutes=5, requires_approval=False),
        ]
    elif risk == RiskLevel.ORANGE:
        actions = [
            DispatchAction(sequence=1, action="加密支护（锚索+钢带）", owner_role="支护工程师", deadline_minutes=30, requires_approval=False),
            DispatchAction(sequence=2, action="加强监测频次至 5 分钟/次", owner_role="监测值班员", deadline_minutes=10, requires_approval=False),
            DispatchAction(sequence=3, action="通知矿总工程师评估", owner_role="调度室主任", deadline_minutes=15, requires_approval=False),
        ]
    elif risk == RiskLevel.YELLOW:
        actions = [
            DispatchAction(sequence=1, action="加强矿压观测频次", owner_role="监测值班员", deadline_minutes=15, requires_approval=False),
            DispatchAction(sequence=2, action="准备备用支护材料", owner_role="支护工程师", deadline_minutes=60, requires_approval=False),
        ]
    else:
        actions = [
            DispatchAction(sequence=1, action="保持常规监测", owner_role="监测值班员", deadline_minutes=60, requires_approval=False),
        ]

    primary = DispatchPlan(plan_id=f"plan-{perception.warning_id}", name=f"{risk.value.upper()} 处置方案", actions=actions)
    alt = DispatchPlan(plan_id=f"plan-{perception.warning_id}-alt", name="降级方案", actions=[actions[0]] if actions else [])

    started = _now()
    result = DispatchDecisionResult(
        agent_name="调度决策 Agent",
        status=AgentStatus.SUCCESS,
        summary=f"生成 {risk.value.upper()} 等级处置方案：{len(actions)} 个动作",
        confidence=0.78,
        evidence=[Evidence(source_type="perception_result", description=f"基于预警 {perception.warning_id}")],
        warnings=[],
        errors=[],
        started_at=started,
        finished_at=_now(),
        duration_ms=30,
        next_action="resource_evaluation",
        decision_id=f"decision-{perception.warning_id}",
        warning_id=perception.warning_id,
        decision_level=risk,
        objectives=[f"将风险从 {risk.value} 降低至可控范围"],
        primary_plan=primary,
        alternative_plans=[alt],
        resource_requirements=ResourceRequirements(
            personnel=["支护工程师", "监测值班员"],
            materials=ref_actions[:3],
            equipment=["监测设备"],
        ),
        mandatory_constraints=[f"遵守煤矿安全规程"],
        prohibited_actions=["空顶作业", "未经审批停产"],
        human_approval_required=risk in {RiskLevel.RED},
        decision_rationale=[f"基于风险等级 {risk.value} 触发"],
    )

    return {
        "decision_result": result,
        "current_phase": "resource_evaluation",
        "messages": [{"role": "assistant", "content": f"调度决策完成：{len(actions)} 动作，审批={'需要' if risk == RiskLevel.RED else '无需'}"}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 4. 资源评估节点
# ═══════════════════════════════════════════════════════════════════════════


def node_resource_evaluation(state: WorkflowState) -> dict:
    """资源评估：LLM 子图增强（LLM_NODES=on），否则/失败回退确定性（model_mode 标记）。"""
    if state.get("actual_snapshot"):
        from app.agent.actual_assessment import resources
        return resources(state)
    if state.get("custom_task") or not is_llm_node_enabled("resource_evaluation"):
        return _resource_evaluation_deterministic(state)
    try:
        out = run_llm_subgraph("resource_evaluation", ResourceEvaluationResult, state)
        return _resource_llm_return(out["result"], state)
    except Exception as exc:
        print(f"[workflow_nodes] 资源评估 LLM 增强失败，回退确定性: {exc}")
        base = _resource_evaluation_deterministic(state)
        base["model_mode"] = "deterministic_fallback"
        return base


def _resource_llm_return(result: ResourceEvaluationResult, state: WorkflowState) -> dict:
    """LLM 资源评估结果 → 节点返回片段（补 current_phase/iteration_count/messages/model_mode）。

    可行性字段由 Pydantic Literal 校验兜底；iteration_count 仅 infeasible 时递增，
    与确定性节点语义一致（决定是否回退调度决策）。
    """
    infeasible = result.feasibility == "infeasible"
    return {
        "resource_result": result,
        "current_phase": "dispatch_decision" if infeasible else "collaborative_control",
        "iteration_count": state.get("iteration_count", 0) + (1 if infeasible else 0),
        "model_mode": "llm",
        "messages": [{"role": "assistant", "content": f"资源评估（LLM）：{result.feasibility}"}],
    }


# fixture 人员项名 → MySQL 角色名的语义提示词（fixture 用语义类别，MySQL 用岗位角色）
# 目标角色词表已对齐权威源（需求 §4.2「role 名称必须对齐角色词表」）：
#   - config/permission_matrix.py ROLE_PERMISSIONS 7 角色（矿长/矿总工程师/安全副矿长/
#     调度室主任/支护工程师/监测值班员/操作工，逐条带条款出处）
#   - config/anchor_refs.py SA4 班制定员锚点（矿长/总工程师/安全副矿长 专职岗位框架，
#     每矿配置；见 任务成果/04_锚定素材/锚点溯源表.csv）
# 映射本身为 fixture 语义类别 → MySQL 岗位角色的工程适配（词表不变，仅归类口径）。
_PERSONNEL_ROLE_HINTS = (
    ("支护", "支护工程师"),   # 当班支护工 / 支护班组
    ("监测", "监测值班员"),   # 监测值班员
    ("值班", "监测值班员"),
    ("调度", "调度室主任"),
    ("安全", "安全副矿长"),
    ("工程师", "支护工程师"), # 当班工程师 → 现场支护技术员
    ("专家", "矿总工程师"),   # 顶板防治专家 → 最高技术决策角色
    ("操作", "操作工"),
)
# 聚合类人员项（不归属单一角色）→ 用 MySQL 在岗总人数兜底
_PERSONNEL_AGGREGATE_HINTS = ("人员", "队伍", "抢险", "班组")


def _merge_mysql_availability(snapshot, mysql_avail: dict) -> None:
    """用 MySQL 业务表可用量覆盖资源快照同名/同语义项的 available（就地修改深拷贝）。

    :param snapshot: 已 model_copy(deep=True) 的资源快照（避免污染缓存）
    :param mysql_avail: load_available_resources() 的返回（按组 dict[name]→数量）
    匹配策略：
    - support_materials / equipment：按 name 精确匹配（物资名/设备名两套口径可对上）
    - personnel：MySQL 按角色聚合、fixture 按语义类别，先精确匹配，再按角色提示词
      子串映射，最后用 MySQL 在岗总人数兜底（聚合类条目）
    匹配不到的项保留 fixture 语义（required/unit 不变），MySQL 不可达时整体回退 fixture。
    """
    for attr, key in (("support_materials", "support_materials"), ("equipment", "equipment")):
        items = getattr(snapshot, attr, None) or []
        avail = (mysql_avail.get(key) or {})
        for item in items:
            qty = avail.get(item.name)
            if qty is not None:
                item.available = float(qty)

    # 人员：精确角色名 → 角色提示词子串 → 聚合兜底
    p_avail = (mysql_avail.get("personnel") or {})
    p_total = float(sum(p_avail.values()))
    for item in (getattr(snapshot, "personnel", None) or []):
        if p_avail.get(item.name) is not None:
            item.available = float(p_avail[item.name])
            continue
        role = _match_personnel_role(item.name)
        if role and role in p_avail:
            item.available = float(p_avail[role])
        elif p_total > 0 and _is_aggregate_personnel(item.name):
            item.available = p_total


def _match_personnel_role(item_name: str) -> str | None:
    """按提示词子串把 fixture 人员类别映射到 MySQL 角色名；未命中返回 None。"""
    for hint, role in _PERSONNEL_ROLE_HINTS:
        if hint in item_name:
            return role
    return None


def _is_aggregate_personnel(item_name: str) -> bool:
    """是否为聚合类人员条目（全部井下人员/应急队伍等）。"""
    return any(h in item_name for h in _PERSONNEL_AGGREGATE_HINTS)


def _resource_evaluation_deterministic(state: WorkflowState) -> dict:
    """核查处置方案的资源可行性。"""
    decision = state.get("decision_result")
    fixture_id = state.get("fixture_scenario_id")
    constraints = state.get("evaluation_constraints")  # 设备安全规则与最低人员配置

    if decision is None:
        return {"errors": state.get("errors", []) + ["缺少调度决策结果"]}

    # 从 fixture 获取资源快照
    custom_task = state.get("custom_task")
    snapshot = None
    if custom_task:
        from app.services.monitoring_task import MonitoringTask
        if custom_task.get("input_type") == "raw_events":
            from app.services.raw_monitoring import RawMonitoringTask
            snapshot = RawMonitoringTask.model_validate(custom_task).resources
        else:
            snapshot = MonitoringTask.model_validate(custom_task).resources
    elif fixture_id:
        from app.agent.fixture_loader import FixtureDataSource
        snapshot = FixtureDataSource().get_resource_snapshot(fixture_id)

    # ── MySQL 主链消费：优先从 MySQL 业务表读取当前可用资源 ──
    # 深拷贝避免污染 FixtureDataSource 缓存；MySQL 可用量按同名覆盖 available
    # （保留 fixture 的 required/unit 场景语义），MySQL 不可达时完整回退 fixture。
    resource_source = "user_resource_snapshot" if custom_task else "fixture_resource_snapshot"
    inventory_evidence = None
    inventory_issue = None
    if custom_task and custom_task.get("inventory_source", "input") != "input":
        # 显式选择外部库存时按精确资源名查询；故障或缺项不能回退用户可用量。
        resource_source = custom_task["inventory_source"] + "_inventory"
        try:
            if custom_task["inventory_source"] == "business":
                from app.services.business_gateway import configured_gateway
                inventory_evidence = configured_gateway().inventory(custom_task["mine_id"])
            else:
                from app.services.mysql_bridge import scoped_inventory
                inventory_evidence = scoped_inventory(custom_task["mine_id"])
            if snapshot:
                snapshot = snapshot.model_copy(deep=True)
                for group in ("personnel", "support_materials", "equipment"):
                    counts = inventory_evidence["available_resources"][group]
                    for item in getattr(snapshot, group):
                        if item.name not in counts: raise ValueError(f"库存没有明确返回资源：{item.name}")
                        item.available = counts[item.name]
        except ValueError as exc:
            inventory_issue = str(exc)
            snapshot = None
    if snapshot is not None and not custom_task:
        snapshot = snapshot.model_copy(deep=True)
        from app.services.mysql_bridge import load_available_resources

        mysql_avail = load_available_resources()
        if mysql_avail is not None:
            resource_source = "mysql_business_tables"
            _merge_mysql_availability(snapshot, mysql_avail)

    if snapshot is None:
        # 无资源数据时默认 feasible
        feasibility = "unknown" if custom_task else "feasible"
        feasibility_score = 0.0 if custom_task else 0.8
        gaps = []
        blocking = ["未提供资源可用/需求数量，需补充后再确认执行条件"] if custom_task else []
    else:
        # 简化核算：对比方案需求与可用量
        # 这里使用确定性逻辑而非调用 resource_accounting_tool（避免工具依赖）
        gaps = []
        satisfied = 0
        total = 0
        for resource_type, req_list in [("personnel", snapshot.personnel), ("material", snapshot.support_materials), ("equipment", snapshot.equipment)]:
            for item in req_list:
                total += 1
                if item.available >= item.required:
                    satisfied += 1
                else:
                    gaps.append(ResourceGap(
                        resource_type=resource_type,
                        resource_name=item.name,
                        required=item.required,
                        available=item.available,
                        unit=item.unit,
                        reason="数量不足",
                    ))

        feasibility_score = satisfied / max(total, 1)
        if feasibility_score >= 0.95:
            feasibility = "feasible"
        elif feasibility_score >= 0.5:
            feasibility = "feasible_with_adjustment"
        else:
            feasibility = "infeasible"

        blocking = [f"{g.resource_name}: 需 {g.required}{g.unit}，仅 {g.available}{g.unit}" for g in gaps]

    started = _now()
    if custom_task:
        _evidence_desc = "按用户提供的可用/需求数量核算；不代表已覆盖处置方案全部需求"
        _warnings = ["需求数量来自用户，需现场核验方案所需的完整资源清单"] if snapshot else blocking
        if inventory_evidence:
            _evidence_desc = "需求数量来自用户，可用数量来自所选库存接口；保留来源及时间，不等同现场执行条件已确认"
            _warnings = [*_warnings, "外部库存的观测时间、人员资质及设备状态需现场核验"]
        if inventory_issue:
            _warnings = [*_warnings, inventory_issue]
    elif resource_source == "mysql_business_tables":
        _evidence_desc = "基于 MySQL 业务表（personnel/materials_inventory/equipment）"
        _warnings = ["当前可用资源来自 MySQL 业务表"]
    else:
        _evidence_desc = "基于 fixture 资源快照"
        _warnings = ["Fixture 模拟资源数据"] if snapshot else ["无资源快照，默认 feasible"]
    # 数量满足仅说明用户所列清单，不等于已证明整个处置方案可以执行。
    quantity_check = None
    summary = f"资源可行性：{feasibility}（{feasibility_score:.0%}）"
    if custom_task:
        quantity_check = "not_provided" if snapshot is None else "insufficient" if gaps else "sufficient"
        feasibility = "infeasible" if gaps else "unknown"
        feasibility_score = None
        blocking = [*blocking, "未核验完整方案需求、人员资质、设备状态与现场库存，执行条件未确认"]
        summary = {"not_provided": "未提供资源数量", "insufficient": "所列资源存在数量缺口", "sufficient": "所列资源数量满足用户填写的需求"}[quantity_check] + "；整体执行条件未核验"
    result = ResourceEvaluationResult(
        agent_name="资源评估 Agent",
        status=AgentStatus.PARTIAL if custom_task else AgentStatus.SUCCESS,
        summary=summary,
        confidence=None if custom_task else 0.85,
        evaluation_basis="user_quantity_comparison" if custom_task else "simulation",
        quantity_check=quantity_check,
        execution_readiness="unverified" if custom_task else None,
        evidence=[Evidence(source_type=resource_source, description=_evidence_desc,
                           value={"inventory":inventory_evidence,"comparison":snapshot.model_dump(mode="json") if snapshot else None,"error":inventory_issue} if custom_task and custom_task.get("inventory_source", "input") != "input" else snapshot.model_dump(mode="json") if snapshot else None)],
        warnings=_warnings,
        errors=[],
        started_at=started,
        finished_at=_now(),
        duration_ms=30,
        next_action="collaborative_control" if feasibility != "infeasible" else "dispatch_decision",
        evaluation_id=f"eval-{decision.decision_id}",
        plan_id=decision.primary_plan.plan_id if decision.primary_plan else "",
        feasibility=feasibility,
        feasibility_score=feasibility_score,
        resource_gaps=gaps,
        blocking_issues=blocking,
        adjustment_suggestions=(["核验库存、资质及需求，补充可用资源后重新评估"] if custom_task else ["考虑替代材料"]) if gaps else [],
        alternative_resources=[],
        redecision_required=(feasibility == "infeasible"),
    )

    return {
        "resource_result": result,
        "current_phase": "collaborative_control" if feasibility != "infeasible" else "dispatch_decision",
        "iteration_count": state.get("iteration_count", 0) + (1 if feasibility == "infeasible" else 0),
        "messages": [{"role": "assistant", "content": f"资源评估：{feasibility}" + (" → 回退决策" if feasibility == "infeasible" else "")}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. 协同管控节点
# ═══════════════════════════════════════════════════════════════════════════


def node_collaborative_control(state: WorkflowState) -> dict:
    """协同管控：LLM 子图增强（LLM_NODES=on），否则/失败回退确定性（model_mode 标记）。

    LLM 模式同样经过人工审批门（interrupt）：是否审批由调度方案的
    human_approval_required + config.require_approval 决定，不信任 LLM 自报状态
    （安全红线：高风险动作必须人工审批）。
    """
    if state.get("custom_task"):
        from app.agent.custom_assessment import record_control_review
        return record_control_review(state)
    if not is_llm_node_enabled("collaborative_control"):
        return _collaborative_control_deterministic(state)
    try:
        out = run_llm_subgraph("collaborative_control", CollaborativeControlResult, state)
    except Exception as exc:
        # 仅 LLM 子图失败回退确定性；interrupt() 抛出的 Interrupt 属 LangGraph
        # 控制流异常（异常子类），绝不能在这里吞掉，否则人工审批门失效并被误判
        # 为“LLM 失败”→ 二次 interrupt。故审批门后处理放在 try 之外。
        print(f"[workflow_nodes] 协同管控 LLM 增强失败，回退确定性: {exc}")
        base = _collaborative_control_deterministic(state)
        base["model_mode"] = "deterministic_fallback"
        return base
    # 审批门（interrupt）必须在 try 之外：approval_request 中断要让 LangGraph
    # 正常暂停工作流等待人工审批，而非被当作 LLM 异常吞掉。
    return _control_llm_return(out["result"], state)


def _control_llm_return(result: CollaborativeControlResult, state: WorkflowState) -> dict:
    """LLM 协同管控结果 → 节点返回片段；审批门逻辑与确定性节点保持一致。

    审批门：调度方案要求审批，或红色预警强制审批（红线：高风险动作必须人工审批，
    不信任 LLM 自报的 human_approval_required——即使调度阶段漏标，此处仍拦截）。
    """
    decision = state.get("decision_result")
    perception = state.get("perception_result")
    needs_approval = bool(
        decision
        and (
            decision.human_approval_required
            or (perception is not None and perception.risk_level == RiskLevel.RED)
        )
    )

    # 读取工作流 config：是否启用人工审批暂停。
    require_approval = False
    try:
        from langgraph.config import get_config
        cfg = get_config()
        require_approval = bool((cfg.get("configurable") or {}).get("require_approval", False))
    except Exception:
        require_approval = False

    if needs_approval and require_approval:
        approval_request = {
            "type": "approval_request",
            "coordination_id": result.coordination_id,
            "warning_id": perception.warning_id if perception else None,
            "required_role": "矿总工程师",
            "risk_level": perception.risk_level.value if perception else "unknown",
            "dispatched_actions": [
                a.action for a in (decision.primary_plan.actions if decision.primary_plan else [])
            ],
        }
        from langgraph.types import interrupt
        approval_decision = interrupt(approval_request)
        approved = bool(approval_decision and approval_decision.get("decision") == "approved")
        result.dispatch_status = "dispatched" if approved else "rejected"
        result.status = AgentStatus.SUCCESS if approved else AgentStatus.WAITING_HUMAN
        result.human_action_required = not approved
        result.next_action = "reflection_iteration" if approved else "finish"
    else:
        approved = not needs_approval
        result.dispatch_status = "dispatched" if approved else "waiting_approval"
        result.status = AgentStatus.SUCCESS if approved else AgentStatus.WAITING_HUMAN
        result.human_action_required = needs_approval
        result.next_action = "reflection_iteration" if approved else "wait_for_human"

    return {
        "control_result": result,
        "current_phase": "reflection_iteration",
        "model_mode": "llm",
        "messages": [{"role": "assistant", "content": f"协同管控（LLM）：{result.summary}"}],
    }


def _collaborative_control_deterministic(state: WorkflowState) -> dict:
    """执行权限校验、通知生成和工单下发。

    当调度方案要求审批（红警高风险动作）且工作流
    通过 config.configurable.require_approval 显式启用审批模式时，调用
    LangGraph interrupt() 暂停工作流，等待 API 层人工审批后恢复执行。
    演示场景直跑模式不启用审批暂停，保持同步验证行为。
    """
    decision = state.get("decision_result")
    perception = state.get("perception_result")
    approval_ctx = state.get("approval_context")  # 审批状态
    recipient_ctx = state.get("recipient_context")  # 通知终端和升级链

    if decision is None:
        return {"errors": state.get("errors", []) + ["缺少调度决策结果"]}

    risk = perception.risk_level if perception else RiskLevel.NORMAL
    needs_approval = decision.human_approval_required or bool(state.get("custom_task") and risk in {RiskLevel.ORANGE, RiskLevel.RED})

    # 读取工作流 config：是否启用人工审批暂停。
    # 非 LangGraph 运行上下文（如单测直接调用节点）时降级为不审批。
    require_approval = False
    try:
        from langgraph.config import get_config
        cfg = get_config()
        require_approval = bool(
            (cfg.get("configurable") or {}).get("require_approval", False)
        )
    except Exception:
        require_approval = False

    # 演示场景使用配置内的通知升级链；生产通知由业务接入层按矿方预案解析。
    notify_map = {
        RiskLevel.NORMAL: [],
        RiskLevel.BLUE: [{"role": "监测值班员", "channel": "web"}],
        RiskLevel.YELLOW: [{"role": "监测值班员", "channel": "web"}, {"role": "调度室主任", "channel": "web"}],
        RiskLevel.ORANGE: [{"role": "调度室主任", "channel": "web"}, {"role": "矿总工程师", "channel": "mobile"}],
        RiskLevel.RED: [{"role": "矿总工程师", "channel": "mobile"}, {"role": "安全副矿长", "channel": "mobile"}, {"role": "操作工", "channel": "underground_terminal"}],
    }
    notifications = [
        NotificationResult(recipient=n["role"], channel=n["channel"], status="pending", sent_at=None)
        for n in notify_map.get(risk, [])
    ]

    coordination_id = f"coord-{decision.decision_id}"
    dispatched_actions = [a.action for a in (decision.primary_plan.actions if decision.primary_plan else [])]

    # ── 人工审批中断与恢复 ──
    # 高风险动作且启用审批模式时，interrupt 暂停工作流并返回审批请求；
    # API 层审批后以 Command(resume=审批结果) 恢复，节点根据结果决定下发或终止。
    approved = False
    if needs_approval and require_approval:
        approval_request = {
            "type": "approval_request",
            "coordination_id": coordination_id,
            "warning_id": perception.warning_id if perception else None,
            "required_role": "矿总工程师",
            "risk_level": risk.value,
            "dispatched_actions": dispatched_actions,
        }
        from langgraph.types import interrupt

        approval_decision = interrupt(approval_request)
        approved = bool(
            approval_decision and approval_decision.get("decision") == "approved"
        )
        dispatch_status = "dispatched" if approved else "rejected"
        agent_status = AgentStatus.SUCCESS if approved else AgentStatus.WAITING_HUMAN
        next_action = "reflection_iteration" if approved else "finish"
        human_action_required = not approved
        summary_tail = "人工审批通过，已下发" if approved else "人工审批未通过"
    else:
        # 未启用审批暂停或无需审批时继续执行后续节点
        approved = not needs_approval
        dispatch_status = "dispatched" if approved else "waiting_approval"
        agent_status = AgentStatus.SUCCESS if approved else AgentStatus.WAITING_HUMAN
        next_action = "reflection_iteration" if approved else "wait_for_human"
        human_action_required = needs_approval
        summary_tail = "已下发" if approved else "等待审批"

    # 用户摘要用于评估与建议；资源未验证或未人工确认时不自动生成执行工单。
    if state.get("custom_task") and dispatch_status != "rejected":
        resource = state.get("resource_result")
        if not needs_approval or resource is None or resource.feasibility != "feasible":
            dispatch_status = "waiting_approval"
            agent_status = AgentStatus.PARTIAL
            human_action_required = True
            summary_tail = "已形成处置建议，待现场核验资源及人工确认，未下发执行"
            next_action = "wait_for_human"

    # ── MySQL 主链消费：下发后把处置动作写入 work_orders 工单表 ──
    # 只在 dispatch_status="dispatched" 时写库；离线/异常降级为 warning，
    # 绝不中断工作流（MySQL 不可用不影响协同管控主链路）。
    evidence_desc = "基于调度决策"
    _warnings = ["通知已生成，需通过业务接入配置发送"] if notifications else []
    if dispatch_status == "dispatched":
        from app.services.mysql_bridge import create_work_orders

        ctx = state.get("context")
        _wo_entries = [
            {"action": a.action, "owner_role": a.owner_role, "deadline_minutes": a.deadline_minutes}
            for a in (decision.primary_plan.actions if decision.primary_plan else [])
        ]
        _wo = create_work_orders(
            _wo_entries,
            workflow_run_id=(ctx.workflow_run_id if ctx else "") or "",
            coordination_id=coordination_id,
        )
        if _wo.get("status") == "success":
            _inserted = _wo["data"]["inserted"]
            evidence_desc = f"基于调度决策；工单已写入 work_orders（{_inserted} 条，{_wo['data']['work_order_ids']}）"
        else:
            _warnings.append(
                f"工单写入失败（降级继续执行）：{_wo.get('error') or '未知错误'}"
            )

    started = _now()
    result = CollaborativeControlResult(
        agent_name="协同管控 Agent",
        status=agent_status,
        summary=f"通知 {len(notifications)} 个角色，{summary_tail}",
        confidence=0.9,
        evidence=[Evidence(source_type="decision_result", description=evidence_desc)],
        warnings=_warnings,
        errors=[],
        started_at=started,
        finished_at=_now(),
        duration_ms=20,
        next_action=next_action,
        coordination_id=coordination_id,
        permission_check=PermissionCheck(passed=True, required_role="矿总工程师" if needs_approval else "调度室主任",
                                         actual_role="调度室主任", denied_actions=[]),
        notification_results=notifications,
        dispatch_status=dispatch_status,
        dispatched_actions=dispatched_actions if not state.get("custom_task") or dispatch_status == "dispatched" else [],
        execution_feedback=[],
        report_artifacts=[],
        human_action_required=human_action_required,
    )

    return {
        "control_result": result,
        "current_phase": "reflection_iteration",
        "messages": [{"role": "assistant", "content": f"协同管控：{summary_tail}通知 {len(notifications)} 个"}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 6. 反思迭代节点
# ═══════════════════════════════════════════════════════════════════════════


def node_reflection_iteration(state: WorkflowState) -> dict:
    """反思迭代：LLM 子图增强（LLM_NODES=on），否则/失败回退确定性（model_mode 标记）。"""
    if state.get("custom_task"):
        # 只有处置前摘要时不能套用 fixture 的风险减半；明确等待真实观测。
        before = state["perception_result"].risk_score
        comparison = None
        task_data = state["custom_task"]
        if task_data.get("parent_run_id"):
            from app.services.workflow_feedback import archive, compare_observations
            from app.services.monitoring_task import parse_monitoring_task
            import json
            comparison = compare_observations(archive.get_run(task_data["parent_run_id"]), parse_monitoring_task(json.dumps(task_data)), state["perception_result"].assessment)
            record, body = archive.get_feedback(task_data["feedback_id"])
            comparison["feedback"] = {**record, "reported_by": body["reported_by"], "actions": body["actions"], "note": body.get("note", "")}
        started = _now()
        result = ReflectionIterationResult(
            agent_name="反思迭代 Agent", status=AgentStatus.PARTIAL,
            summary="处置建议已生成；缺少处置后监测数据和执行回执，效果尚未验证",
            confidence=None, evaluation_basis="awaiting_observations", evidence=[], warnings=["处置后观测和目标完成度尚未评估"], errors=[],
            started_at=started, finished_at=_now(), duration_ms=0, next_action="wait_for_data",
            reflection_id=f"reflection-{state['context'].workflow_run_id}",
            iteration_count=state.get("iteration_count", 0), outcome="unknown", goal_completion_score=None,
            risk_change=RiskChange(before=before, after=None, trend="unknown"),
            return_decision=ReturnDecision(action="wait_for_data", reason="缺少处置后观测",
                                           required_inputs=["处置后监测数据", "执行回执"]),
            human_review_required=True,
        )
        if comparison is not None:
            result.summary = "已比较处置前后目录指标；变化不等同处置有效" if comparison["comparable"] else "前后观测口径不一致，不能比较处置效果"
            result.evidence = [Evidence(source_type="post_observation_comparison", description="关联原任务的真实观测比较", value=comparison)]
            result.warnings = comparison["caveats"] + comparison["reasons"]
            result.evaluation_basis = "post_observation_comparison"
            result.return_decision = ReturnDecision(action="wait_for_data", reason="已收到后续观测；仍需核验执行回执与现场效果", required_inputs=["可信执行回执", "现场效果核验"])
            from app.services.business_gateway import configured_gateway
            receipts = configured_gateway().submissions_for_run(task_data["parent_run_id"])
            parent = archive.get_run(task_data["parent_run_id"])
            actions = parent["result"]["agents"]["decision_result"]["primary_plan"]["actions"]
            # 所有原方案动作均有鉴权执行回执，且发生于新观测之前，才统计完成率。
            executed = {r["action_sequence"] for r in receipts if r["kind"] == "work_order" and r["status"] == "executed"
                        and r.get("receipt") and datetime.fromisoformat(r["receipt"]["occurred_at"]) <= datetime.fromisoformat(state["perception_result"].assessment["window_start"])}
            result.evidence.append(Evidence(source_type="authenticated_business_receipts", description="原运行工单与鉴权回执", value=receipts))
            if receipts and actions:
                result.goal_completion_score = len(executed & {a["sequence"] for a in actions}) / len(actions)
            if actions and result.goal_completion_score == 1 and comparison["comparable"]:
                result.status = AgentStatus.SUCCESS
                result.summary = "已收到全部方案动作执行回执，并完成实际后续观测对比"
                result.return_decision = ReturnDecision(action="finish",reason="执行记录及观测复核已完成；现场风险结论依据本次适用参数",required_inputs=[])
                result.next_action = "finish"
                result.warnings = ["观测变化已计算；变化与具体处置的因果关系需结合现场证据分析"]
        return {"reflection_result": result, "current_phase": "finish",
                "messages": [{"role": "assistant", "content": result.summary}]}
    if not is_llm_node_enabled("reflection_iteration"):
        return _reflection_iteration_deterministic(state)
    try:
        out = run_llm_subgraph("reflection_iteration", ReflectionIterationResult, state)
        return _reflection_llm_return(out["result"], state)
    except Exception as exc:
        print(f"[workflow_nodes] 反思迭代 LLM 增强失败，回退确定性: {exc}")
        base = _reflection_iteration_deterministic(state)
        base["model_mode"] = "deterministic_fallback"
        return base


def _reflection_llm_return(result: ReflectionIterationResult, state: WorkflowState) -> dict:
    """LLM 反思结果 → 节点返回片段（补 current_phase/iteration_history/messages/model_mode）。

    路由由 result.return_decision.action 驱动（_route_after_reflection）。
    循环上限由迭代计数强制（与确定性节点语义一致）：iteration >= max_iterations 时
    无论 LLM 决策如何，强制 return_decision.action="finish" + human_review_required=True，
    否则 LLM 反复 return_to_decision 会导致工作流无限循环（设计 §6.4 循环上限）。
    """
    action = result.return_decision.action
    target = result.return_decision.target_agent
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", 2)
    history = list(state.get("iteration_history", []))

    if iteration >= max_iter:
        result.return_decision.action = "finish"
        result.return_decision.target_agent = None
        result.return_decision.reason = f"达到最大迭代次数 {max_iter}"
        result.human_review_required = True
        action = "finish"
        target = None

    if action not in ("finish", "wait_for_data", "wait_for_human"):
        history.append({
            "from_agent": "reflection_iteration",
            "to_agent": target,
            "reason": result.return_decision.reason,
            "iteration": iteration,
        })
    return {
        "reflection_result": result,
        "current_phase": "finish" if action == "finish" else target or "finish",
        "iteration_count": iteration + 1,
        "iteration_history": history,
        "model_mode": "llm",
        "messages": [{"role": "assistant", "content": f"反思（LLM）：{result.outcome}，{action}"}],
    }


def _reflection_iteration_deterministic(state: WorkflowState) -> dict:
    """汇总全链路结果，评估处置效果，决定结束或回退。"""
    perception = state.get("perception_result")
    resource = state.get("resource_result")
    control = state.get("control_result")
    ctx = state["context"]
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", 2)
    history = state.get("iteration_history", [])
    observations = state.get("execution_observations")  # 执行反馈驱动复盘

    # 演示场景没有处置后观测时，使用场景定义的回放结果；实际任务只接受执行回执
    # 和后续监测窗口重新计算风险变化。
    fixture_id = state.get("fixture_scenario_id")
    if fixture_id and "rollback" in fixture_id:
        risk_before = perception.risk_score if perception else 0.7
        risk_after = risk_before * 0.95
        trend = "unchanged"
        outcome = "unchanged"
    elif fixture_id and "max_iterations" in fixture_id:
        risk_before = perception.risk_score if perception else 0.95
        risk_after = risk_before * 0.9
        trend = "decreased" if risk_after < risk_before else "unchanged"
        outcome = "worsened" if iteration >= max_iter else "improved"
    else:
        risk_before = perception.risk_score if perception else 0.5
        risk_after = risk_before * 0.5
        trend = "decreased"
        outcome = "resolved" if risk_after < 0.3 else "improved"

    # 场景回放的回退路由用于验证状态机；实际任务由观测、回执和资源状态驱动。
    if iteration >= max_iter:
        # 达到循环上限，强制结束
        return_action = "finish"
        target = None
        reason = f"达到最大迭代次数 {max_iter}"
        human_review = True
    elif fixture_id == "resource_insufficient" and iteration < 1:
        return_action = "return_to_decision"
        target = "dispatch_decision"
        reason = "资源不可行，需调整方案"
        human_review = False
    elif fixture_id == "reflection_rollback" and iteration < 1:
        return_action = "return_to_decision"
        target = "dispatch_decision"
        reason = "执行后风险未下降"
        human_review = False
    elif outcome in ("resolved", "improved") or iteration >= 1:
        return_action = "finish"
        target = None
        reason = "处置目标已完成" if outcome == "resolved" else "风险已改善"
        human_review = False
    else:
        return_action = "finish"
        target = None
        reason = "正常结束"
        human_review = False

    started = _now()
    result = ReflectionIterationResult(
        agent_name="反思迭代 Agent",
        status=AgentStatus.SUCCESS,
        summary=f"迭代 {iteration}: 风险 {risk_before:.2f}→{risk_after:.2f} ({trend})，决策: {return_action}",
        confidence=0.8,
        evidence=[Evidence(source_type="all_agent_results", description="全链路执行结果")],
        warnings=[],
        errors=[],
        started_at=started,
        finished_at=_now(),
        duration_ms=20,
        next_action=return_action,
        reflection_id=f"reflection-{ctx.workflow_run_id}-{iteration}",
        iteration_count=iteration + 1,
        outcome=outcome,
        goal_completion_score=max(0.0, min(1.0, 1.0 - risk_after)),
        risk_change=RiskChange(before=risk_before, after=risk_after, trend=trend),
        root_causes=[] if outcome == "resolved" else ["处置方案覆盖不足"],
        threshold_suggestions=[],
        strategy_suggestions=[],
        coordination_suggestions=[],
        knowledge_candidates=_derive_knowledge_candidates(
            perception, outcome, risk_before, risk_after,
            f"reflection-{ctx.workflow_run_id}-{iteration}"),
        return_decision=ReturnDecision(
            action=return_action, target_agent=target, reason=reason,
            required_inputs=[],
        ),
        human_review_required=human_review,
    )

    # 回退时记录历史
    new_history = list(history)
    if return_action not in ("finish", "wait_for_data", "wait_for_human"):
        new_history.append({"from_agent": "reflection_iteration", "to_agent": target, "reason": reason, "iteration": iteration})

    return {
        "reflection_result": result,
        "current_phase": "finish" if return_action == "finish" else target or "finish",
        "iteration_count": iteration + 1,
        "iteration_history": new_history,
        "messages": [{"role": "assistant", "content": f"反思：{outcome}，{return_action}" + (f" → {target}" if target else "")}],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 7. 知识写回节点（END 前，设计 §8.5）
# ═══════════════════════════════════════════════════════════════════════════


def _review_gate(candidate: dict) -> tuple[str, str]:
    """审核门状态机（设计 §8.2）：返回 (action, reason)，action ∈ write/skip/reject。

    - review_status=rejected          → ("reject", 拒绝原因)：记录审计，永不写回
    - review_status=approved + 审核人  → ("write", "")：放行至存储层（存储层再校验完整 SPO）
    - review_status=approved 缺审核人   → ("reject", "审核人必填")
    - 其余（pending_review / 未知）    → ("skip", 待审核)：保留审核队列
    """
    status = candidate.get("review_status")
    if status == "rejected":
        return "reject", candidate.get("review_comment") or "候选已被审核拒绝"
    if status == "approved":
        if not candidate.get("reviewed_by"):
            return "reject", "approved 候选审核人必填"
        return "write", ""
    return "skip", "候选待人工审核（pending_review）"


def _derive_knowledge_candidates(perception, outcome, risk_before, risk_after,
                                 reflection_id: str) -> list[dict]:
    """反思产出 → 候选池钩子（设计 §8.5）：从感知致灾因子派生知识候选。

    全部为 pending_review（治理：反思不得自动改知识库，必须经人工审核）；
    缺 object（非完整 SPO），待 UIE 抽取或人工补齐（设计 §8.2 / §2.4）。
    """
    candidates = []
    if perception is None:
        return candidates
    for i, cf in enumerate(perception.causal_factors[:2]):  # 最多 2 条，避免噪声
        evidence = "；".join(cf.evidence) if cf.evidence else f"感知评分因子（importance={cf.importance:.2f}）"
        candidates.append({
            "candidate_id": f"kc-{reflection_id}-{i:03d}",
            "entity": cf.factor,           # 致灾因子作为 subject
            "entity_type": "顶板灾变",      # 实体类型枚举（7 类之一）
            "relation": "诱发",             # 关系类型枚举（7 类之一）
            "evidence": evidence,
            "source": perception.warning_id,
            "confidence": cf.importance,
            "review_status": "pending_review",  # 绝不自动 approved
            # 缺 object：待 UIE/人工补齐完整 SPO 后才可审核通过并写回
        })
    return candidates


def node_knowledge_write_back(state: WorkflowState) -> dict:
    """END 前写回节点：只消费 approved 知识候选落图（设计 §8.5）。

    审核门只读 review_status，绝不自动审核；approved 候选仅由人工审核（或 UIE
    补齐 object）后产生。pending_review 保留审核队列，rejected 记录原因永不写回。
    """
    reflection = state.get("reflection_result")
    candidates = list(reflection.knowledge_candidates) if reflection else []
    if not candidates:
        return {"knowledge_writeback_result": writeback_result(
            "skipped", {"written": 0, "skipped": [], "rejected": []}, None,
            "无知识候选可写回")}

    # ── UIE 富化钩子（运行期，开关 UIE_ENRICH_KNOWLEDGE_CANDIDATES）──
    # 为缺 object 的候选补齐完整 SPO；富化只改 object/evidence 字段，
    # review_status 保持 pending_review，仍须过 _review_gate 人工审核才可写回。
    from app.tools.uie_extractor import enrich_candidates

    candidates, _uie_summary = enrich_candidates(candidates)
    # _uie_summary 仅作运行日志（enriched/attempted/failed），富化结果已就地
    # 写回 candidate dict；review_status 未变，仍走下方审核门流程。

    approved, skipped, rejected = [], [], []
    for c in candidates:
        action, reason = _review_gate(c)
        if action == "write":
            approved.append(c)
        elif action == "reject":
            rejected.append({"candidate_id": c.get("candidate_id", ""), "reason": reason})
        else:
            skipped.append({"candidate_id": c.get("candidate_id", ""), "reason": reason})

    if not approved:
        # 无 approved 候选：保留/拒绝，不落图（治理：反思不得自动改知识库）
        return {"knowledge_writeback_result": writeback_result(
            "skipped", {"written": 0, "skipped": skipped, "rejected": rejected}, None,
            "无 approved 候选，未落图（知识候选必须经人工审核）")}

    try:
        stored = write_knowledge_to_graph(approved)
    except KnowledgeSourceError as exc:
        # 落图失败：不静默，节点记录 error（对齐 §7.3 运行期失败表面）
        return {
            "knowledge_writeback_result": writeback_result(
                "failed", {"written": 0, "skipped": skipped, "rejected": rejected},
                str(exc), "Neo4j 知识写回失败"),
            "errors": list(state.get("errors", [])) + [str(exc)],
        }

    # 合并审核门 skip/reject 与存储层 reject（存储层拒写非完整 SPO 条目）。
    # 状态语义：任一候选被拒（gate 或存储层）→ partial；全部写回 → success。
    written_count = stored["data"].get("written", 0)
    merged_rejected = rejected + stored["data"].get("rejected", [])
    if merged_rejected:
        merged_status = "partial"
    elif written_count > 0:
        merged_status = "success"
    else:
        merged_status = stored["status"]  # 防御：无 written 且无 rejected 时沿用存储层状态
    return {"knowledge_writeback_result": {
        "status": merged_status,
        "data": {
            "written": written_count,
            "skipped": skipped,
            "rejected": merged_rejected,
            "note": stored["data"].get("note", ""),
        },
        "error": stored.get("error"),
        "audit": stored["audit"],
    }}


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _warnings_prefix(source_type: str) -> str:
    """数据源提示语（允许随数据源变化，见设计 §6.5）。"""
    if source_type == "neo4j_kg":
        return "当前为 Neo4j 图谱种子数据（YAML 权威摄取），非真实知识库"
    return "当前为 fixture 模拟数据，非真实知识库"


def _knowledge_failure_result(perception, exc) -> dict:
    """图谱运行期故障 → status=failed 的合法 KnowledgeRetrievalResult。"""
    started = _now()
    return {
        "knowledge_result": KnowledgeRetrievalResult(
            agent_name="知识检索 Agent",
            status=AgentStatus.FAILED,
            summary="知识检索失败：Neo4j 数据源不可用",
            confidence=0.0,
            evidence=[Evidence(source_type="neo4j_kg", description=str(exc))],
            warnings=["知识检索失败，未返回图谱知识"],
            errors=[str(exc)],
            started_at=started,
            finished_at=_now(),
            duration_ms=50,
            next_action="dispatch_decision",
            warning_id=perception.warning_id,
            similar_cases=[],
            regulations=[],
            support_recommendations=[],
            knowledge_conflicts=[],
            missing_topics=[],
        ),
        "current_phase": "dispatch_decision",
        "messages": [{"role": "assistant", "content": "知识检索失败（Neo4j 数据源不可用）"}],
    }


class WarningExplanation(BaseModel):
    """感知预警 LLM 解释层输出（仅用于生成自然语言说明，绝不参与风险定级）。"""
    summary: str = Field(..., description="预警自然语言说明")
    key_factors: list[str] = Field(default_factory=list, description="关键致灾因子要点")


def _llm_warning_explanation(analysis) -> str | None:
    """LLM 解释层：基于规则输出生成预警说明；失败/离线返回 None（保留确定性文案）。

    输入仅为规则引擎输出的因果因子/触发规则/风险等级；红线：risk_level/risk_score
    始终来自规则引擎（PerceptionWarningService），LLM 只解释，绝不反写。
    """
    payload = {
        "risk_level": analysis.risk_level.value,
        "risk_score": analysis.risk_score,
        "triggered_rules": list(analysis.triggered_rules or []),
        "causal_factors": [
            f"{cf.factor}(重要度{cf.importance:.2f})"
            for cf in (analysis.causal_factors or [])
        ],
        "anomalies": list(analysis.anomalies or []),
    }
    res = llm_call_structured(
        WarningExplanation,
        "你是煤矿顶板灾变预警解释专家。基于给定的规则评分输出，生成给值班人员看的"
        "预警说明（仅解释，不修改风险等级/评分）。只输出 JSON。",
        json.dumps(payload, ensure_ascii=False),
    )
    if res["status"] != "ok":
        return None
    return res["data"].summary


def _describe_risk(level: RiskLevel, score: float) -> str:
    labels = {
        RiskLevel.NORMAL: f"监测指标正常（评分 {score:.2f}）",
        RiskLevel.BLUE: f"轻微异常（评分 {score:.2f}），建议缩短复查周期",
        RiskLevel.YELLOW: f"多项指标异常（评分 {score:.2f}），触发黄色预警",
        RiskLevel.ORANGE: f"指标明显异常（评分 {score:.2f}），触发橙色预警",
        RiskLevel.RED: f"高风险组合（评分 {score:.2f}），触发红色预警",
    }
    return labels.get(level, f"风险等级 {level.value}（评分 {score:.2f}）")
