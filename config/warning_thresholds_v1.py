"""感知预警 V1 的集中式模拟阈值。

这些数值仅依据仓库中的模拟数据设计，用于软件原型和流程验证，不代表真实煤矿
安全生产阈值、行业标准或现场处置依据。
"""

from dataclasses import dataclass


THRESHOLD_VERSION = "simulation-v1"
THRESHOLD_LABEL = "simulation/demo thresholds"
THRESHOLD_DISCLAIMER = (
    "simulation/demo thresholds：仅依据当前模拟数据设计，用于软件原型和流程验证，"
    "不代表真实煤矿安全生产阈值。"
)


@dataclass(frozen=True)
class WarningThresholdsV1:
    version: str = THRESHOLD_VERSION

    # 每组四个边界依次对应特征严重度 1～4，仅用于四套 fixture 的软件流程测试。
    max_energy_j: tuple[float, float, float, float] = (
        5_000.0,
        20_000.0,
        100_000.0,
        250_000.0,
    )
    max_frequency_per_hour: tuple[float, float, float, float] = (
        5.0,
        8.0,
        14.0,
        25.0,
    )
    low_b_value: tuple[float, float, float, float] = (1.15, 1.0, 0.8, 0.55)
    b_value_drop: tuple[float, float, float, float] = (0.03, 0.08, 0.15, 0.25)
    event_count: tuple[int, int, int, int] = (3, 5, 8, 12)
    high_energy_event_j: float = 50_000.0
    high_energy_event_count: tuple[int, int, int, int] = (1, 2, 3, 5)
    frequency_rise_per_hour: float = 2.0
    spatial_cluster_radius: float = 5.0
    spatial_cluster_min_events: int = 3
    spatial_cluster_fraction: float = 0.8

    # 综合评分权重之和为 1.0。
    weight_max_energy: float = 0.23
    weight_frequency: float = 0.20
    weight_b_value: float = 0.20
    weight_b_value_drop: float = 0.15
    weight_high_energy_count: float = 0.12
    weight_event_count: float = 0.05
    weight_spatial_cluster: float = 0.05

    # risk_score 到风险等级的 simulation-v1 映射边界。
    blue_score: float = 0.12
    yellow_score: float = 0.28
    orange_score: float = 0.48
    red_score: float = 0.75

    severe_missing_rate: float = 0.10


WARNING_THRESHOLDS_V1 = WarningThresholdsV1()
