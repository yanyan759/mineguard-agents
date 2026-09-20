"""感知预警 Agent：封装确定性规则分析并输出统一工作流结果。"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Literal, Protocol

from app.schemas import (
    AffectedArea,
    AgentStatus,
    CausalFactor,
    DataQuality,
    Evidence,
    PerceptionWarningInput,
    PerceptionWarningResult,
    RiskLevel,
)
from app.services import PerceptionAnalysis, PerceptionWarningService


AGENT_NAME = "感知预警 Agent"


class PerceptionInferenceBackend(Protocol):
    """规则服务与未来真实预警模型共同遵循的最小替换接口。"""

    model_mode: Literal["online", "mock", "rule_based"]
    threshold_version: str

    def analyze(self, input_data: PerceptionWarningInput) -> PerceptionAnalysis: ...

    @property
    def disclaimer(self) -> str: ...


class PerceptionWarningAgent:
    """把感知服务的可解释分析结果封装为统一工作流 Schema。"""

    name = AGENT_NAME

    def __init__(self, backend: PerceptionInferenceBackend | None = None):
        self.backend = backend or PerceptionWarningService()

    def run(self, input_data: PerceptionWarningInput) -> PerceptionWarningResult:
        if not isinstance(input_data, PerceptionWarningInput):
            raise TypeError("input_data must be a PerceptionWarningInput instance")

        started_at = datetime.now(timezone.utc)
        started_clock = perf_counter()
        analysis = self.backend.analyze(input_data)
        finished_at = datetime.now(timezone.utc)
        duration_ms = max(0, round((perf_counter() - started_clock) * 1000))

        status = self._status(analysis)
        confidence = self._confidence(analysis)
        warnings = [self.backend.disclaimer, *analysis.quality.warnings]
        if analysis.quality.missing_sources:
            warnings.append(
                "可选监测源缺失，当前结论主要基于微震数据："
                + ", ".join(analysis.quality.missing_sources)
            )
        fast_alert_required = analysis.risk_level in {
            RiskLevel.ORANGE,
            RiskLevel.RED,
        }

        return PerceptionWarningResult(
            agent_name=self.name,
            status=status,
            summary=self._summary(analysis),
            confidence=confidence,
            evidence=[
                Evidence(
                    source_type="rule_based_feature_statistics",
                    source_id=input_data.context.workflow_run_id,
                    description="基于 PerceptionWarningInput 实际微震数据计算的特征",
                    value=analysis.features.as_dict(),
                ),
                Evidence(
                    source_type="threshold_config",
                    source_id=self.backend.threshold_version,
                    description=self.backend.disclaimer,
                ),
            ],
            warnings=warnings,
            errors=[
                *analysis.quality.critical_errors,
                *analysis.quality.issues,
            ],
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            next_action=self._next_action(analysis),
            warning_id=(
                input_data.context.warning_id
                or f"warning-{input_data.context.workflow_run_id}"
            ),
            risk_level=analysis.risk_level,
            risk_score=analysis.risk_score,
            model_version=input_data.model_version,
            threshold_version=self.backend.threshold_version,
            model_mode=self.backend.model_mode,
            affected_area=AffectedArea(
                mine_id=input_data.context.mine_id,
                roadway_id=input_data.context.roadway_id,
                locations=sorted(
                    {event.area for event in input_data.microseismic_events}
                ),
            ),
            causal_factors=[
                CausalFactor(
                    factor=factor.factor,
                    importance=factor.importance,
                    trend=factor.trend,
                    evidence=factor.evidence,
                )
                for factor in analysis.causal_factors
            ],
            anomalies=analysis.anomalies,
            data_quality=DataQuality(
                completeness=analysis.quality.completeness,
                timeliness=analysis.quality.timeliness,
                missing_sources=analysis.quality.missing_sources,
                issues=[
                    *analysis.quality.issues,
                    *analysis.quality.critical_errors,
                ],
            ),
            triggered_rules=analysis.triggered_rules,
            fast_alert_required=fast_alert_required,
            recommended_recheck_minutes=self._recheck_minutes(analysis.risk_level),
        )

    @staticmethod
    def _status(analysis: PerceptionAnalysis) -> AgentStatus:
        if analysis.quality.is_failed:
            return AgentStatus.FAILED
        if analysis.quality.is_partial:
            return AgentStatus.PARTIAL
        return AgentStatus.SUCCESS

    @staticmethod
    def _confidence(analysis: PerceptionAnalysis) -> float:
        if analysis.quality.is_failed:
            return 0.05
        quality = analysis.quality.completeness * analysis.quality.timeliness
        optional_source_factor = max(
            0.6, 1.0 - 0.05 * len(analysis.quality.missing_sources)
        )
        confidence = (0.65 + 0.30 * quality) * optional_source_factor
        if analysis.quality.is_partial:
            confidence = min(confidence, 0.60)
        return round(min(0.95, max(0.0, confidence)), 4)

    @staticmethod
    def _summary(analysis: PerceptionAnalysis) -> str:
        if analysis.quality.is_failed:
            return "微震数据不足或关键采样信息无效，无法完成 V1 规则预警。"
        descriptions = {
            RiskLevel.NORMAL: "指标未达到蓝色评分边界，保持常规监测。",
            RiskLevel.BLUE: "指标出现轻微异常，建议缩短复查周期。",
            RiskLevel.YELLOW: "多项指标异常，触发黄色预警。",
            RiskLevel.ORANGE: "多项指标明显异常，触发橙色预警和快速告警。",
            RiskLevel.RED: "高风险特征组合出现，触发红色预警和快速告警。",
        }
        return descriptions[analysis.risk_level]

    @staticmethod
    def _next_action(analysis: PerceptionAnalysis) -> str:
        if analysis.quality.is_failed:
            return "wait_for_data"
        if analysis.risk_level is RiskLevel.NORMAL:
            return "finish"
        if analysis.risk_level in {RiskLevel.ORANGE, RiskLevel.RED}:
            return "fast_alert_and_knowledge_retrieval"
        return "knowledge_retrieval"

    @staticmethod
    # 复查间隔来自当前生效的矿井配置；内置值仅用于演示场景。
    @staticmethod
    def _recheck_minutes(risk_level: RiskLevel) -> int:
        return {
            RiskLevel.NORMAL: 60,
            RiskLevel.BLUE: 30,
            RiskLevel.YELLOW: 15,
            RiskLevel.ORANGE: 10,
            RiskLevel.RED: 5,
        }[risk_level]


perception_warning_agent = PerceptionWarningAgent()


def run_perception_warning(
    input_data: PerceptionWarningInput,
) -> PerceptionWarningResult:
    """供测试和未来工作流节点调用的同步入口。"""

    return perception_warning_agent.run(input_data)
