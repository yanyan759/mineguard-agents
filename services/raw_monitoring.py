"""原始监测记录、方法适用性与可复核统计；缺失观测始终保留未知。"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.workflow import ResourceSnapshot

PROFILE_DIR = Path(__file__).resolve().parents[1]/"config/assessment_profiles"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RawEvent(StrictModel):
    event_id: str = Field(min_length=1)
    timestamp: datetime
    energy: float = Field(ge=0)
    energy_unit: Literal["J", "kJ"]
    coordinates: tuple[float, float, float] | None = None

    @property
    def energy_j(self):
        return self.energy * (1000 if self.energy_unit == "kJ" else 1)


class BMethod(StrictModel):
    method: Literal["energy_mle"]
    minimum_energy_j: float = Field(gt=0)
    minimum_samples: int = Field(ge=50)


class RawMonitoringTask(StrictModel):
    input_type: Literal["raw_events"]
    mine_id: str = Field(min_length=1)
    roadway_id: str = Field(min_length=1)
    area: str = Field(min_length=1)
    source: str = Field(min_length=1)
    method_id: str = "statistics-v1"
    window_start: datetime
    window_end: datetime
    events: list[RawEvent] = Field(max_length=100000)
    missing_rate: float | None = Field(default=None, ge=0, le=1)
    b_method: BMethod | None = None
    resources: ResourceSnapshot | None = None
    configuration_version: str | None = None
    inventory_source: Literal["input", "business", "mysql"] = "input"
    input_files: list[str] = Field(default_factory=list)
    parent_run_id: str | None = None
    feedback_id: str | None = None

    @model_validator(mode="after")
    def coherent_records(self):
        """每条记录必须属于明确窗口；不接受隐式本地时区和矛盾事件ID。"""
        times = [self.window_start, self.window_end, *[e.timestamp for e in self.events]]
        if any(t.tzinfo is None for t in times):
            raise ValueError("时间必须包含Z或明确时区偏移")
        if self.window_end <= self.window_start:
            raise ValueError("监测窗口结束时间必须晚于开始时间")
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("事件ID重复，请先核对原始记录")
        if any(not self.window_start <= e.timestamp < self.window_end for e in self.events):
            raise ValueError("事件超出监测窗口[start,end)，未来事件不可进入本次计算")
        if any(not math.isfinite(e.energy_j) for e in self.events):
            raise ValueError("能量换算溢出")
        if not math.isfinite(sum(e.energy_j for e in self.events)):
            raise ValueError("总能量超出可计算范围")
        if any(not math.isfinite(sum(e.coordinates[i] for e in self.events if e.coordinates)) for i in range(3)):
            raise ValueError("坐标合计超出可计算范围")
        if self.resources and any(not math.isfinite(value) for group in (self.resources.personnel,self.resources.support_materials,self.resources.equipment) for item in group for value in (item.available,item.required)):
            raise ValueError("资源可用量和需求量必须是有限数值")
        return self


def parse_csv_records(text: str):
    """CSV字段固定并强制写能量单位，避免J/kJ混用及重复表头。"""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    fields = reader.fieldnames or []
    required = {"event_id", "timestamp", "energy", "energy_unit"}
    if len(set(fields)) != len(fields) or not required.issubset(fields) or set(fields)-required-{"x", "y", "z"}:
        raise ValueError("CSV列应为event_id,timestamp,energy,energy_unit，可选x,y,z")
    result = []
    for number, row in enumerate(reader, 2):
        if None in row:
            raise ValueError(f"CSV第{number}行列数不一致")
        entry = {k: row[k] for k in required}
        coordinates = [row.get(k, "") for k in ("x", "y", "z")]
        if any(coordinates):
            if not all(coordinates): raise ValueError(f"CSV第{number}行坐标不完整")
            entry["coordinates"] = coordinates
        result.append(RawEvent.model_validate(entry).model_dump(mode="json"))
    return result


class ResearchProfile(StrictModel):
    """研究参数只接受已实现的预测契约，不允许文件自称现场批准。"""
    method_id: str = Field(min_length=1)
    label: str
    status: Literal["research"]
    mine_id: str
    area: str
    source: str
    window_minutes: float = Field(gt=0)
    count_threshold: float = Field(ge=0)
    energy_threshold_j: float = Field(ge=0)
    coordinates_bounds: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    valid_from: datetime
    valid_to: datetime
    calibration_period: str
    validation: str
    source_url: str
    parameter_source: str

    @model_validator(mode="after")
    def valid_scope(self):
        if self.valid_from.tzinfo is None or self.valid_to.tzinfo is None or self.valid_from >= self.valid_to:
            raise ValueError("参数有效期必须包含时区且起止有序")
        if any(low >= high for low, high in self.coordinates_bounds):
            raise ValueError("参数坐标范围必须有序")
        return self


def profiles():
    """只读服务端固定目录，不允许输入指定路径或自行声称已批准。"""
    result = [{"method_id": "statistics-v1", "status": "statistics", "label": "原始记录统计（不定级）"}]
    for path in sorted(PROFILE_DIR.glob("*.json")):
        profile = ResearchProfile.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")
        if any(p["method_id"] == profile["method_id"] for p in result):
            raise ValueError(f"参数文件{path.name}的方法编号重复")
        result.append(profile)
    return result


def select_profile(method_id):
    return next((p for p in profiles() if p["method_id"] == method_id), None)


_LIVE_PROFILE = object()


def analyze_raw(task: RawMonitoringTask, profile_snapshot=_LIVE_PROFILE):
    """两指标研究方法复用回放预测函数；统计与现场风险结论分别输出。"""
    hours = (task.window_end-task.window_start).total_seconds()/3600
    energies = [e.energy_j for e in task.events]
    warnings = []
    b_value = None
    b_samples = 0
    if task.b_method:
        selected = [math.log10(e)-math.log10(task.b_method.minimum_energy_j) for e in energies if e >= task.b_method.minimum_energy_j]
        b_samples = len(selected)
        if b_samples >= task.b_method.minimum_samples and sum(selected) > 0:
            b_value = math.log10(math.e)/(sum(selected)/len(selected))
        else:
            warnings.append("b值估计样本不足或能量分布退化，保持未知")
        warnings.append("energy_mle为指定截断能量下的连续幂律估计；截断值未证明达到目录完整性，不套用其他论文的b值阈值")
    else:
        warnings.append("未选择b值估计方法，b值未知")
    if task.missing_rate is None: warnings.append("未提供设备在线/漏采记录，缺失率未知")
    if task.events != sorted(task.events, key=lambda e: e.timestamp):
        warnings.append("原始记录乱序，统计按时间口径处理，未修改原始记录")
    metrics = {"event_count": len(energies), "max_energy_j": max(energies, default=0),
               "total_energy_j": sum(energies), "frequency_per_hour": len(energies)/hours,
               "window_minutes": hours*60, "b_value": b_value, "b_sample_count": b_samples,
               "b_method": task.b_method.model_dump() if task.b_method else None,
               "missing_rate": task.missing_rate, "spatial_centroid": None}
    if task.events and all(e.coordinates is not None for e in task.events):
        metrics["spatial_centroid"] = [sum(e.coordinates[i] for e in task.events)/len(task.events) for i in range(3)]
    selected = select_profile(task.method_id) if profile_snapshot is _LIVE_PROFILE else profile_snapshot
    reasons = []
    alarm = None
    if selected is None: reasons.append("服务端不存在该方法版本")
    elif task.method_id != "statistics-v1":
        if selected.get("mine_id") != task.mine_id or selected.get("area") != task.area:
            reasons.append("参数矿井或测区与输入不匹配")
        if selected.get("source") != task.source: reasons.append("数据来源与参数适用来源不匹配")
        if selected.get("window_minutes") != metrics["window_minutes"]: reasons.append("统计时间窗口不匹配")
        if not datetime.fromisoformat(selected["valid_from"]) <= task.window_end <= datetime.fromisoformat(selected["valid_to"]):
            reasons.append("输入时间不在参数适用期间")
        bounds = selected.get("coordinates_bounds")
        if bounds and any(e.coordinates is None or any(not bounds[i][0] <= e.coordinates[i] <= bounds[i][1] for i in range(3)) for e in task.events):
            reasons.append("记录缺坐标或超出参数测区")
        if not reasons:
            import pandas as pd
            from app.services.historical_replay import predict_baseline
            alarm = bool(predict_baseline(pd.DataFrame([{"eligible": True, "event_count": len(energies), "max_energy_j": metrics["max_energy_j"]}]), selected)[0])
    return {"method_id": task.method_id, "profile": selected, "applicability": "not_applicable" if reasons else "applicable",
            "reasons": reasons, "metrics": metrics, "alarm": alarm, "risk_level": None, "risk_score": None,
            "warnings": warnings, "window_start": task.window_start.isoformat(), "window_end": task.window_end.isoformat(),
            "source": task.source, "input_files": task.input_files,
            "field_validity": "unverified", "summary": "方法不适用，已保留原始指标" if reasons else
            "研究规则触发报警，需现场研判" if alarm else "研究规则未触发报警，不代表现场安全" if alarm is False else "已完成原始记录统计，现场风险未定级"}
