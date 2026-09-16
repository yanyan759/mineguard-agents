"""感知预警 V1 的数据质量、特征统计和规则评分服务。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import fmean

from app.config.warning_thresholds_v1 import (
    THRESHOLD_DISCLAIMER,
    WARNING_THRESHOLDS_V1,
    WarningThresholdsV1,
)
from app.schemas import PerceptionWarningInput, RiskLevel


@dataclass(frozen=True)
class MicroseismicFeatures:
    event_count: int = 0
    high_energy_event_count: int = 0
    max_energy_j: float = 0.0
    average_energy_j: float = 0.0
    max_frequency_per_hour: float = 0.0
    average_frequency_per_hour: float = 0.0
    frequency_change_per_hour: float = 0.0
    average_b_value: float | None = None
    latest_b_value: float | None = None
    minimum_b_value: float | None = None
    b_value_change: float = 0.0
    b_value_drop: float = 0.0
    spatial_cluster_fraction: float = 0.0
    spatial_clustered: bool = False
    high_energy_spatial_clustered: bool = False

    def as_dict(self) -> dict[str, float | int | bool | None]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class QualityAssessment:
    completeness: float
    timeliness: float
    missing_sources: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    critical_errors: list[str] = field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        return bool(self.issues)

    @property
    def is_failed(self) -> bool:
        return bool(self.critical_errors)


@dataclass(frozen=True)
class FactorFinding:
    factor: str
    importance: float
    trend: str
    evidence: list[str]


@dataclass(frozen=True)
class PerceptionAnalysis:
    features: MicroseismicFeatures
    quality: QualityAssessment
    risk_level: RiskLevel
    risk_score: float
    triggered_rules: list[str]
    causal_factors: list[FactorFinding]
    anomalies: list[str]


def _ascending_severity(value: float, boundaries: tuple[float, ...]) -> int:
    return sum(value >= boundary for boundary in boundaries)


def _descending_severity(value: float | None, boundaries: tuple[float, ...]) -> int:
    if value is None:
        return 0
    return sum(value <= boundary for boundary in boundaries)


def _distance(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second)))


def _cluster_fraction(coordinates: list[tuple[float, float, float]], radius: float) -> float:
    if not coordinates:
        return 0.0
    centroid = tuple(fmean(axis) for axis in zip(*coordinates))
    inside = sum(_distance(point, centroid) <= radius for point in coordinates)
    return inside / len(coordinates)


class PerceptionWarningService:
    """不依赖 LLM 的可测试规则服务，可由未来真实模型后端替换。"""

    model_mode = "rule_based"

    def __init__(self, thresholds: WarningThresholdsV1 = WARNING_THRESHOLDS_V1):
        self.thresholds = thresholds
        self.threshold_version = thresholds.version

    def analyze(self, input_data: PerceptionWarningInput) -> PerceptionAnalysis:
        quality = self.check_data_quality(input_data)
        features = self.calculate_features(input_data)
        if quality.is_failed:
            return PerceptionAnalysis(
                features=features,
                quality=quality,
                risk_level=RiskLevel.NORMAL,
                risk_score=0.0,
                triggered_rules=["data_quality_failed"],
                causal_factors=[],
                anomalies=list(quality.critical_errors),
            )

        return self.analyze_features(features, quality)

    def analyze_summary(self, task) -> PerceptionAnalysis:
        """对用户提供的汇总指标复用同一规则评分，不插值制造逐条事件。"""
        unknown = []
        for name, value in (("高能事件数", task.high_energy_count),
                            ("空间集中比例", task.spatial_cluster_fraction),
                            ("起始频次", task.frequency_start), ("起始b值", task.b_start)):
            if value is None:
                unknown.append(name)
        warnings = [self.disclaimer, "输入为用户监测摘要，未提供逐条事件、坐标和采样频率；无法核验真实性及时效性"]
        issues = ["未提供" + "、".join(unknown) + "，评分仅基于已知指标，不能排除更高风险"] if unknown else []
        if task.missing_rate > self.thresholds.severe_missing_rate:
            issues.append("监测采样缺失率超过 simulation-v1 数据质量边界")
        warnings.extend(issues)
        b_change = task.b_end - task.b_start if task.b_start is not None else 0
        features = MicroseismicFeatures(
            event_count=task.event_count,
            high_energy_event_count=task.high_energy_count or 0,
            max_energy_j=task.max_energy_j,
            max_frequency_per_hour=max(task.frequency_start or 0, task.frequency_end),
            frequency_change_per_hour=task.frequency_end - task.frequency_start if task.frequency_start is not None else 0,
            latest_b_value=task.b_end,
            minimum_b_value=min(task.b_start, task.b_end) if task.b_start is not None else task.b_end,
            b_value_change=b_change, b_value_drop=max(0, -b_change),
            spatial_cluster_fraction=task.spatial_cluster_fraction or 0,
            spatial_clustered=task.event_count >= self.thresholds.spatial_cluster_min_events and (task.spatial_cluster_fraction or 0) >= self.thresholds.spatial_cluster_fraction,
        )
        quality = QualityAssessment(
            completeness=1-task.missing_rate, timeliness=0,
            missing_sources=["raw_microseismic_events", "sampling_frequency", *unknown],
            warnings=warnings, issues=issues,
        )
        return self.analyze_features(features, quality)

    def analyze_features(self, features: MicroseismicFeatures, quality: QualityAssessment) -> PerceptionAnalysis:
        """原始记录与摘要共用同一评分路径，输入方式不改变阈值和权重。"""
        severities = self._feature_severities(features)
        risk_score = self._risk_score(severities)
        risk_level = self._risk_level(risk_score)
        rules = self._triggered_rules(severities, features, risk_level)
        factors = self._causal_factors(severities, features)
        anomalies = [factor.factor for factor in factors]
        return PerceptionAnalysis(
            features=features,
            quality=quality,
            risk_level=risk_level,
            risk_score=risk_score,
            triggered_rules=rules,
            causal_factors=factors,
            anomalies=anomalies,
        )

    def check_data_quality(self, input_data: PerceptionWarningInput) -> QualityAssessment:
        events = input_data.microseismic_events
        metadata = input_data.monitoring_window.sampling_metadata
        optional_sources = {
            "borehole_stress": input_data.borehole_stress,
            "roof_separation": input_data.roof_separation,
            "hydraulic_support_resistance": input_data.hydraulic_support_resistance,
            "roadway_surface_displacement": input_data.roadway_surface_displacement,
        }
        missing_sources = [name for name, readings in optional_sources.items() if not readings]
        warnings = []
        issues = []
        critical_errors = []

        if not events:
            critical_errors.append("microseismic_events 为空，无法计算微震预警")
        else:
            timestamps = [event.timestamp for event in events]
            if timestamps != sorted(timestamps):
                issues.append("微震事件时间乱序")

            event_ids = [event.event_id for event in events]
            duplicate_ids = sorted({item for item in event_ids if event_ids.count(item) > 1})
            if duplicate_ids:
                issues.append(f"存在重复微震事件ID: {', '.join(duplicate_ids)}")

            window = input_data.monitoring_window
            outside_count = sum(
                event.timestamp < window.start_time or event.timestamp > window.end_time
                for event in events
            )
            if outside_count:
                issues.append(f"{outside_count} 条微震事件时间超出 monitoring_window")

        if metadata.missing_rate > 0:
            warnings.append(f"监测采样缺失率为 {metadata.missing_rate:.1%}")
        if metadata.missing_rate > self.thresholds.severe_missing_rate:
            issues.append("监测采样缺失率超过 simulation-v1 数据质量边界")
        if metadata.actual_frequency_hz == 0:
            critical_errors.append("实际采样频率为 0")

        expected_version = input_data.threshold_version
        if expected_version and expected_version != self.threshold_version:
            warnings.append(
                f"输入阈值版本 {expected_version} 未启用，实际使用 {self.threshold_version}"
            )

        completeness = max(0.0, min(1.0, 1.0 - metadata.missing_rate))
        timeliness = max(
            0.0,
            min(1.0, metadata.actual_frequency_hz / metadata.expected_frequency_hz),
        )
        return QualityAssessment(
            completeness=round(completeness, 4),
            timeliness=round(timeliness, 4),
            missing_sources=missing_sources,
            issues=issues,
            warnings=warnings,
            critical_errors=critical_errors,
        )

    def calculate_features(self, input_data: PerceptionWarningInput) -> MicroseismicFeatures:
        # 趋势按时间计算；原始顺序问题仍由数据质量检查记录，不让乱序改变风险结果。
        events = sorted(input_data.microseismic_events, key=lambda event: event.timestamp)
        if not events:
            return MicroseismicFeatures()

        energies = [event.energy_j for event in events]
        frequencies = [event.event_frequency_per_hour for event in events]
        b_values = [event.b_value for event in events]
        coordinates = [event.source_coordinate for event in events]
        high_energy_events = [
            event
            for event in events
            if event.energy_j >= self.thresholds.high_energy_event_j
        ]
        cluster_fraction = _cluster_fraction(
            coordinates, self.thresholds.spatial_cluster_radius
        )
        spatial_clustered = (
            len(events) >= self.thresholds.spatial_cluster_min_events
            and cluster_fraction >= self.thresholds.spatial_cluster_fraction
        )
        high_energy_cluster_fraction = _cluster_fraction(
            [event.source_coordinate for event in high_energy_events],
            self.thresholds.spatial_cluster_radius,
        )
        high_energy_clustered = (
            len(high_energy_events) >= 2
            and high_energy_cluster_fraction >= self.thresholds.spatial_cluster_fraction
        )
        b_change = b_values[-1] - b_values[0]
        return MicroseismicFeatures(
            event_count=len(events),
            high_energy_event_count=len(high_energy_events),
            max_energy_j=max(energies),
            average_energy_j=round(fmean(energies), 4),
            max_frequency_per_hour=max(frequencies),
            average_frequency_per_hour=round(fmean(frequencies), 4),
            frequency_change_per_hour=round(frequencies[-1] - frequencies[0], 4),
            average_b_value=round(fmean(b_values), 4),
            latest_b_value=b_values[-1],
            minimum_b_value=min(b_values),
            b_value_change=round(b_change, 4),
            b_value_drop=round(max(0.0, -b_change), 4),
            spatial_cluster_fraction=round(cluster_fraction, 4),
            spatial_clustered=spatial_clustered,
            high_energy_spatial_clustered=high_energy_clustered,
        )

    def _feature_severities(self, features: MicroseismicFeatures) -> dict[str, int]:
        return {
            "max_energy": _ascending_severity(
                features.max_energy_j, self.thresholds.max_energy_j
            ),
            "frequency": _ascending_severity(
                features.max_frequency_per_hour,
                self.thresholds.max_frequency_per_hour,
            ),
            "b_value": _descending_severity(
                features.minimum_b_value, self.thresholds.low_b_value
            ),
            "b_value_drop": _ascending_severity(
                features.b_value_drop, self.thresholds.b_value_drop
            ),
            "high_energy_count": _ascending_severity(
                features.high_energy_event_count,
                self.thresholds.high_energy_event_count,
            ),
            "event_count": _ascending_severity(
                features.event_count, self.thresholds.event_count
            ),
            "spatial_cluster": 1 if features.spatial_clustered else 0,
        }

    def _risk_score(self, severities: dict[str, int]) -> float:
        weights = {
            "max_energy": self.thresholds.weight_max_energy,
            "frequency": self.thresholds.weight_frequency,
            "b_value": self.thresholds.weight_b_value,
            "b_value_drop": self.thresholds.weight_b_value_drop,
            "high_energy_count": self.thresholds.weight_high_energy_count,
            "event_count": self.thresholds.weight_event_count,
            "spatial_cluster": self.thresholds.weight_spatial_cluster,
        }
        score = sum((severities[name] / 4.0) * weight for name, weight in weights.items())
        return round(min(1.0, max(0.0, score)), 4)

    def _risk_level(self, score: float) -> RiskLevel:
        if score >= self.thresholds.red_score:
            return RiskLevel.RED
        if score >= self.thresholds.orange_score:
            return RiskLevel.ORANGE
        if score >= self.thresholds.yellow_score:
            return RiskLevel.YELLOW
        if score >= self.thresholds.blue_score:
            return RiskLevel.BLUE
        return RiskLevel.NORMAL

    def _triggered_rules(
        self,
        severities: dict[str, int],
        features: MicroseismicFeatures,
        risk_level: RiskLevel,
    ) -> list[str]:
        rules = [
            f"{name}_severity_{severity}"
            for name, severity in severities.items()
            if severity > 0
        ]
        if features.frequency_change_per_hour >= self.thresholds.frequency_rise_per_hour:
            rules.append("frequency_rising")
        if features.high_energy_spatial_clustered:
            rules.append("high_energy_spatial_cluster")
        if risk_level is RiskLevel.NORMAL:
            rules.append("all_demo_indicators_below_blue_score")
        return rules

    def _causal_factors(
        self, severities: dict[str, int], features: MicroseismicFeatures
    ) -> list[FactorFinding]:
        factors: list[FactorFinding] = []
        if severities["frequency"] > 0 or (
            features.frequency_change_per_hour >= self.thresholds.frequency_rise_per_hour
        ):
            factors.append(
                FactorFinding(
                    factor="窗口首末频次净变化/频次阈值触发",
                    importance=min(1.0, 0.25 * max(1, severities["frequency"])),
                    trend="rising" if features.frequency_change_per_hour > 0 else "falling" if features.frequency_change_per_hour < 0 else "stable",
                    evidence=[
                        f"最大事件频次 {features.max_frequency_per_hour:.2f} 次/小时",
                        f"窗口首末频次变化 {features.frequency_change_per_hour:+.2f} 次/小时",
                    ],
                )
            )
        if features.high_energy_spatial_clustered:
            factors.append(
                FactorFinding(
                    factor="高能微震集中出现",
                    importance=min(1.0, 0.25 * severities["high_energy_count"] + 0.25),
                    trend="rising",
                    evidence=[
                        f"高能事件 {features.high_energy_event_count} 次",
                        f"空间集中比例 {features.spatial_cluster_fraction:.0%}",
                    ],
                )
            )
        if severities["max_energy"] > 0:
            factors.append(
                FactorFinding(
                    factor="最大能量达到配置阈值（无历史升降基线）",
                    importance=0.25 * severities["max_energy"],
                    trend="unknown",
                    evidence=[f"最大微震能量 {features.max_energy_j:.2f} J"],
                )
            )
        if severities["b_value"] > 0:
            factors.append(
                FactorFinding(
                    factor="b值低于配置阈值",
                    importance=0.25 * severities["b_value"],
                    trend="unknown",
                    evidence=[
                        f"最小 b 值 {features.minimum_b_value:.3f}",
                        f"最近 b 值 {features.latest_b_value:.3f}",
                    ],
                )
            )
        if severities["b_value_drop"] > 0:
            factors.append(
                FactorFinding(
                    factor="窗口首末b值净下降",
                    importance=0.25 * severities["b_value_drop"],
                    trend="falling",
                    evidence=[f"窗口首末 b 值下降 {features.b_value_drop:.3f}"],
                )
            )
        if features.spatial_clustered:
            factors.append(
                FactorFinding(
                    factor="微震事件空间集中",
                    importance=0.55,
                    trend="stable",
                    evidence=[
                        f"{self.thresholds.spatial_cluster_radius:.1f} 坐标单位半径内比例 "
                        f"{features.spatial_cluster_fraction:.0%}"
                    ],
                )
            )
        return factors

    @property
    def disclaimer(self) -> str:
        return THRESHOLD_DISCLAIMER
