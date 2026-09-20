"""成员 1 MOA-Transformer 时序预警模型的输入输出契约。

本文件定义多智能体后端与 MOA-Transformer 推理服务之间的 Pydantic 接口，
由成员 1 实现模型侧代码，我（成员 2+3）在后端 Agent 中以工具形式封装调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SchemaModel(BaseModel):
    """成员 1 接口契约的公共校验策略。"""
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# ── 请求模型 ──────────────────────────────────────────────────────────────


class SensorFeatureRow(SchemaModel):
    """单条监测窗口内的十维统计特征值。

    对应揭榜挂帅汇报中“均值、斜率、偏度、峰度等十维特征”的要求。
    每条 row 是一个测点 × 一个时间窗口的统计聚合结果。
    """
    point_id: str = Field(description="测点/支架/事件区域标识")
    timestamp: datetime = Field(description="窗口结束时刻")
    sensor_type: Literal[
        "borehole_stress",
        "roof_separation",
        "hydraulic_support",
        "microseismic",
        "roadway_displacement",
    ] = Field(description="传感器类型")
    # 基础统计
    count: int = Field(ge=0, description="窗口内有效采样数")
    mean: float = Field(description="均值")
    std: float = Field(ge=0.0, description="标准差")
    slope: float = Field(description="线性回归斜率")
    skewness: float | None = Field(default=None, description="偏度")
    kurtosis: float | None = Field(default=None, description="峰度")
    # 极值
    min_value: float = Field(description="最小值")
    max_value: float = Field(description="最大值")
    # 变化
    range_value: float = Field(ge=0.0, description="极差")
    coefficient_of_variation: float | None = Field(default=None, description="变异系数")
    # 微震专用
    b_value: float | None = Field(default=None, gt=0, description="b 值（仅微震类型）")
    energy_j: float | None = Field(default=None, ge=0, description="能量值（仅微震类型）")
    source_x: float | None = Field(default=None, description="震源 X 坐标")
    source_y: float | None = Field(default=None, description="震源 Y 坐标")
    source_z: float | None = Field(default=None, description="震源 Z 坐标")


class ModelInferenceRequest(SchemaModel):
    """MOA-Transformer 单次推理请求。"""
    request_id: str = Field(description="请求唯一标识，与 trace_id 关联")
    model_version: str = Field(description="模型版本标识")
    # 特征表：每一行是一个测点 × 一个窗口的十维统计特征
    features: list[SensorFeatureRow] = Field(min_length=1, description="十维特征行列表")
    forecast_horizon_minutes: int = Field(
        default=10, ge=5, le=60, description="预测提前时长（分钟），默认 10min"
    )
    return_shap: bool = Field(default=True, description="是否返回 SHAP 特征重要性")
    metadata: dict[str, str] = Field(
        default_factory=dict, description="透传元数据（如 mine_id/roadway_id）"
    )


# ── 响应模型 ──────────────────────────────────────────────────────────────


class ForecastPoint(SchemaModel):
    """单个预测时刻的风险概率。"""
    forecast_minutes: int = Field(ge=5, le=60, description="预测提前分钟数")
    risk_probability: float = Field(ge=0.0, le=1.0, description="该时刻的风险概率")
    predicted_risk_level: Literal["normal", "blue", "yellow", "orange", "red"] = Field(
        description="预测风险等级"
    )
    confidence_interval_lower: float = Field(ge=0.0, le=1.0, description="置信区间下界")
    confidence_interval_upper: float = Field(ge=0.0, le=1.0, description="置信区间上界")


class ShapFeatureImportance(SchemaModel):
    """单个特征的 SHAP 重要度。"""
    feature_name: str = Field(description="特征名称")
    shap_value: float = Field(description="SHAP 值")
    importance_rank: int = Field(ge=1, description="重要度排序，1 为最高")


class ModelInferenceResponse(SchemaModel):
    """MOA-Transformer 单次推理响应。"""
    request_id: str = Field(description="对应请求标识")
    model_version: str = Field(description="实际使用的模型版本")
    model_status: Literal["online", "degraded", "unavailable"] = Field(
        description="模型服务状态"
    )
    forecasts: list[ForecastPoint] = Field(default_factory=list, description="多步预测结果")
    shap_importance: list[ShapFeatureImportance] = Field(
        default_factory=list, description="SHAP 特征重要度排序"
    )
    top_causal_factors: list[str] = Field(
        default_factory=list, description="模型判定最重要的致灾因子列表"
    )
    inference_time_ms: int = Field(ge=0, description="推理耗时（毫秒）")
    warnings: list[str] = Field(default_factory=list, description="推理过程中的警告信息")
    errors: list[str] = Field(default_factory=list, description="推理错误信息")


# ── 健康检查接口 ──────────────────────────────────────────────────────────


class ModelHealthResponse(SchemaModel):
    """模型服务健康状态。"""
    model_version: str = Field(description="当前模型版本")
    status: Literal["healthy", "degraded", "unavailable"] = Field(description="服务健康状态")
    last_trained_at: datetime | None = Field(default=None, description="最近训练时间")
    avg_inference_time_ms: int = Field(default=0, ge=0, description="平均推理耗时")
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0, description="最近评估准确率")
    false_positive_rate: float | None = Field(default=None, ge=0.0, le=1.0, description="最近误报率")


# ── 指标汇总（联调验收用）──────────────────────────────────────────────────


class ModelMetricsReport(SchemaModel):
    """成员 1 模型量化指标报告结构。

    用于联调阶段验证模型是否达到揭榜挂帅汇报中声明的指标。
    """
    model_version: str = Field(description="模型版本")
    test_set_accuracy: float = Field(ge=0.0, le=1.0, description="测试集准确率，目标 ≥88%")
    false_positive_rate: float = Field(ge=0.0, le=1.0, description="误报率，目标 ≤12%")
    single_step_latency_ms: int = Field(ge=0, description="单步推理时延，目标 <500ms")
    test_set_size: int = Field(ge=0, description="测试集样本数")
    evaluated_at: datetime = Field(description="评估时间")
    notes: str = Field(default="", description="备注说明")
