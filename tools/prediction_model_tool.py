"""
预测模型调用工具

封装 MOA-Transformer 时序预警模型推理接口，对接成员 1。

运行模式：
- 配置 `PREDICTION_MODEL_URL`（或兼容旧变量 `MCP_PREDICTION_MODEL_URL`）时，
  走真实 HTTP 调用：构造 `ModelInferenceRequest` → POST 推理端点 →
  解析 `ModelInferenceResponse`；调用失败/超时自动降级为 rule_based 兜底。
- 未配置 URL 时保持离线模式，风险判断由感知预警 Agent 的
  rule_based 规则引擎完成。

接口契约见 schemas/member1_interface.py（成员 1 按此实现模型侧代码）。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

# 推理端点与健康检查端点的默认路径（成员 1 接口就绪后按实际路径调整）
_PREDICT_PATH = "/predict"
_HEALTH_PATH = "/health"


def _get_model_url() -> str | None:
    """调用时读取成员 1 模型服务地址（支持 PREDICTION_MODEL_URL 与旧变量兼容）。

    每次调用读取环境变量，便于 .env 修改后无需重启进程即生效（与 db_tools 一致）。
    """
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv("PREDICTION_MODEL_URL") or os.getenv("MCP_PREDICTION_MODEL_URL")


def _get_timeout_seconds() -> float:
    """读取单次推理调用超时（秒），超时后降级为 rule_based，不中断 Agent 执行链路。"""
    return float(os.getenv("PREDICTION_TIMEOUT_SECONDS", "10"))


def _build_result(
    status: str,
    data: Any = None,
    error: str | None = None,
    model_mode: str = "offline",
) -> dict:
    """构建统一返回格式（model_mode: online | degraded | offline）。"""
    return {
        "status": status, "data": data, "error": error,
        "model_mode": model_mode,  # online | degraded | offline
        "audit": {
            "tool": "prediction_model",
            "model_mode": model_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def _call_model_http(
    features_json: str,
    forecast_horizon_minutes: int,
    model_version: str,
) -> tuple[Any | None, str | None]:
    """真实 HTTP 调用成员 1 推理服务。

    :return: (ModelInferenceResponse | None, error | None)
    """
    import requests

    from app.schemas.member1_interface import (
        ModelInferenceRequest,
        ModelInferenceResponse,
        SensorFeatureRow,
    )

    model_url = _get_model_url()
    try:
        raw = json.loads(features_json)
        # 十维特征行通过 Pydantic 契约校验，非法字段直接报错降级
        rows = [SensorFeatureRow.model_validate(item) for item in raw]
        if not rows:
            return None, "features 列表为空"
        request = ModelInferenceRequest(
            request_id=f"pred-{uuid.uuid4().hex[:8]}",
            model_version=model_version,
            features=rows,
            forecast_horizon_minutes=forecast_horizon_minutes,
        )
        url = f"{model_url.rstrip('/')}{_PREDICT_PATH}"
        resp = requests.post(
            url,
            json=request.model_dump(mode="json"),
            timeout=_get_timeout_seconds(),
        )
        resp.raise_for_status()
        parsed = ModelInferenceResponse.model_validate(resp.json())
        return parsed, None
    except Exception as e:
        # 网络/超时/契约校验失败 → 返回错误，由调用方降级
        return None, str(e)


@tool
def run_prediction(
    features_json: str,
    forecast_horizon_minutes: int = 10,
    model_version: str = "MOA-Transformer-v1",
) -> dict:
    """调用 MOA-Transformer 时序预警模型进行顶板灾变风险预测。

    输入为监测窗口内各测点的十维统计特征（JSON 字符串），
    输出为未来 N 分钟的风险概率预测和 SHAP 特征重要度排序。

    配置 PREDICTION_MODEL_URL 后走真实模型调用；未配置或调用失败时
    降级为 rule_based 规则引擎判断（不中断 Agent 执行链路）。

    :param features_json: 十维特征行列表的 JSON 字符串（格式见 schemas/member1_interface.py SensorFeatureRow）
    :param forecast_horizon_minutes: 预测时长（分钟），默认 10，范围 5-60
    :param model_version: 模型版本号
    """
    if forecast_horizon_minutes < 5 or forecast_horizon_minutes > 60:
        return _build_result(
            "rejected",
            error=f"forecast_horizon_minutes 需在 5-60 之间，当前值 {forecast_horizon_minutes}",
        )

    # ── 已配置模型服务地址 → 真实 HTTP 调用 ──
    if _get_model_url():
        parsed, err = _call_model_http(
            features_json, forecast_horizon_minutes, model_version
        )
        if parsed is not None:
            return _build_result(
                "success",
                data={
                    "forecasts": [f.model_dump() for f in parsed.forecasts],
                    "shap_importance": [s.model_dump() for s in parsed.shap_importance],
                    "top_causal_factors": parsed.top_causal_factors,
                    "model_status": parsed.model_status,
                    "inference_time_ms": parsed.inference_time_ms,
                    "warnings": parsed.warnings,
                    "errors": parsed.errors,
                },
                model_mode="online",
            )
        # 模型调用失败 → 降级为 rule_based 兜底，携带失败原因便于排查
        return _build_result(
            "success",
            data={
                "forecasts": [
                    {"forecast_minutes": forecast_horizon_minutes,
                     "risk_probability": 0.0, "predicted_risk_level": "unknown",
                     "confidence_interval_lower": 0.0, "confidence_interval_upper": 0.0},
                ],
                "shap_importance": [],
                "top_causal_factors": [],
                "note": (
                    f"模型接口调用失败（{err}），已降级为 rule_based 规则引擎判断。"
                    f"请检查 PREDICTION_MODEL_URL={_get_model_url()} 可达性。"
                ),
            },
            model_mode="degraded",
        )

    # ── 未配置 URL：离线模式 ──
    return _build_result(
        "success",
        data={
            "forecasts": [
                {"forecast_minutes": forecast_horizon_minutes,
                 "risk_probability": 0.0, "predicted_risk_level": "unknown",
                 "confidence_interval_lower": 0.0, "confidence_interval_upper": 0.0},
            ],
            "shap_importance": [],
            "top_causal_factors": [],
            "note": "离线模式：未配置外部预测模型接口，"
                    "风险判断由感知预警 Agent 的 rule_based 规则引擎完成。"
                    "待模型接口上线后，设置 PREDICTION_MODEL_URL 环境变量即可切换。",
        },
        model_mode="offline",
    )


@tool
def check_model_health() -> dict:
    """检查成员 1 MOA-Transformer 模型服务健康状态。

    未配置 PREDICTION_MODEL_URL 时返回 unavailable（离线）；配置后请求
    健康检查端点并解析 ModelHealthResponse。健康检查失败不中断调用方。

    返回值结构：
      {status, data: {model_version, status, avg_inference_time_ms, ...}, error, model_mode}
    """
    from app.schemas.member1_interface import ModelHealthResponse

    if not _get_model_url():
        return _build_result(
            "success",
            data={
                "model_version": "unknown",
                "status": "unavailable",
                "avg_inference_time_ms": 0,
                "note": "未配置 PREDICTION_MODEL_URL，模型服务不可用（离线模式）",
            },
            model_mode="offline",
        )

    import requests

    try:
        url = f"{_get_model_url().rstrip('/')}{_HEALTH_PATH}"
        resp = requests.get(url, timeout=_get_timeout_seconds())
        resp.raise_for_status()
        health = ModelHealthResponse.model_validate(resp.json())
        return _build_result(
            "success",
            data=health.model_dump(),
            model_mode="online",
        )
    except Exception as e:
        return _build_result(
            "error",
            data="模型健康检查失败",
            error=str(e),
            model_mode="degraded",
        )
