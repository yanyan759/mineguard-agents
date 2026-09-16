"""把用户提供的监测摘要解析为真实输入；不补造事件、坐标或资源库存。"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.workflow import ResourceItem, ResourceSnapshot


class MonitoringTask(BaseModel):
    """摘要与逐条微震记录分开存储，缺少的观测保持 None。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mine_id: str = Field(min_length=1)
    roadway_id: str = Field(min_length=1)
    area: str = Field(min_length=1)
    source: str = Field(default="用户监测摘要",min_length=1)
    window_minutes: float = Field(gt=0)
    event_count: int = Field(ge=0)
    max_energy_j: float = Field(ge=0)
    frequency_start: float | None = Field(default=None, ge=0)
    frequency_end: float = Field(ge=0)
    b_start: float | None = Field(default=None, gt=0)
    b_end: float = Field(gt=0)
    missing_rate: float = Field(ge=0, le=1)
    high_energy_count: int | None = Field(default=None, ge=0)
    spatial_cluster_fraction: float | None = Field(default=None, ge=0, le=1)
    resources: ResourceSnapshot | None = None
    configuration_version: str | None = None
    inventory_source: Literal["input", "business", "mysql"] = "input"
    window_end: datetime | None = None
    parent_run_id: str | None = None
    feedback_id: str | None = None

    @model_validator(mode="after")
    def consistent_counts(self):
        """拒绝自相矛盾的数据，避免错误摘要得到看似有效的风险结果。"""
        if self.high_energy_count is not None and self.high_energy_count > self.event_count:
            raise ValueError("高能事件数不能超过总事件数")
        # 实际摘要不采用演示高能阈值；具体指标口径由矿井配置和数据来源定义。
        if self.window_end is not None and self.window_end.tzinfo is None:
            raise ValueError("观测截止时间必须带时区")
        if self.event_count == 0 and self.max_energy_j > 0:
            raise ValueError("零事件与非零最大能量矛盾")
        if self.resources and not (self.resources.personnel or self.resources.support_materials or self.resources.equipment):
            self.resources = None
        return self


NUMBER = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"


def parse_monitoring_task(query: str | None) -> MonitoringTask:
    """支持明确带单位的中文摘要及同字段 JSON；格式或指标不足时要求补充。"""
    raw = (query or "").strip()
    if not raw:
        raise ValueError("请输入监测任务，至少包含矿井/巷道/工作面、时间窗口、事件数、最大能量、频次、b值和缺失率")
    if raw.startswith("{"):
        data = json.loads(raw)
        if data.get("input_type") == "raw_events":
            from app.services.raw_monitoring import RawMonitoringTask
            return RawMonitoringTask.model_validate(data)
        return MonitoringTask.model_validate(data)

    text = unicodedata.normalize("NFKC", raw)
    # 仅移除规范千分位；中文逗号是字段分隔符，不可全局删除。
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    missing = []

    def one(pattern: str, label: str, required: bool = True):
        matches = list(re.finditer(pattern, text, re.I))
        if len(matches) > 1:
            raise ValueError(f"{label}重复，请只保留同一监测窗口的一组指标")
        if not matches:
            if required:
                missing.append(label)
            return None
        return matches[0]

    def scalar(pattern: str, label: str, required: bool = True):
        match = one(pattern, label, required)
        return float(match[1]) if match else None

    # 完整首行允许矿方自有编号和测区名称，与配置页的作用域保持一致。
    header = re.match(r"^\s*([\w-]+)\s*/\s*([\w-]+)\s*/\s*([^:\n;]+)\s*:",text)
    if header:
        mine_id,roadway_id,area_name = (part.strip() for part in header.groups())
    else:
        mine = one(r"\bMINE-[\w-]+", "矿井编号（MINE-001）")
        roadway = one(r"\bRDW-[\w-]+", "巷道编号（RDW-010）")
        area = one(r"(?:工作面\s*-?\s*\d+|\d+\s*工作面)", "工作面编号（工作面-10）")
        mine_id,roadway_id,area_name = (match[0] if match else "" for match in (mine,roadway,area))
    version = one(r"配置版本\s*[:=]\s*([^;。\n,]+)","配置版本",False)
    end = one(r"观测截止\s*[:=]\s*([^;。\n,]+)","观测截止",False)
    source = one(r"数据来源\s*[:=]\s*([^;。\n,]+)","数据来源",False)
    window = one(rf"(?:过去|最近|近|监测窗口\s*[:为]?)\s*({NUMBER})\s*(分钟|小时|min|h)", "时间窗口（过去30分钟）")
    count = scalar(rf"(?:监测到|共|累计)\s*({NUMBER})\s*(?:条|次|个)\s*微震事件", "事件数（监测到7条微震事件）")
    if count is None and not re.search(r"(?:监测到|共|累计)", text):
        missing = [x for x in missing if not x.startswith("事件数")]
        count = scalar(rf"(?:微震事件数|事件总数)\s*[:为]?\s*({NUMBER})", "事件数")
    energy = one(rf"最大能量\s*[:为]?\s*({NUMBER})\s*([a-zA-Z]+|万焦耳|万J|焦耳)", "最大能量（85000 J）")
    freq = one(rf"(?:事件)?频次\s*(?:由|从)\s*({NUMBER})\s*次\s*/\s*(?:小时|h)\s*(?:升至|降至|增至|增加至|降低至|到|至)\s*({NUMBER})\s*次\s*/\s*(?:小时|h)", "事件频次", False)
    freq_end = float(freq[2]) if freq else scalar(rf"(?:事件)?频次\s*(?:峰值|最大值|为|:)?\s*({NUMBER})\s*次\s*/\s*(?:小时|h)", "事件频次（11.8次/h）")
    bvalue = one(rf"b\s*值\s*(?:由|从)\s*({NUMBER})\s*(?:升至|降至|增至|到|至)\s*({NUMBER})", "b值", False)
    b_end = float(bvalue[2]) if bvalue else scalar(rf"b\s*值\s*(?:为|:)?\s*({NUMBER})", "b值（b值由0.82降至0.68）")
    missing_rate = scalar(rf"缺失率\s*[:为]?\s*({NUMBER})\s*%", "缺失率（4%）")
    high_count = scalar(rf"高能(?:量)?事件\s*[:为]?\s*({NUMBER})\s*(?:条|次|个)", "高能事件数", False)
    cluster = scalar(rf"空间集中比例\s*[:为]?\s*({NUMBER})\s*%", "空间集中比例", False)
    if missing:
        raise ValueError("缺少或无法识别：" + "、".join(missing))

    units = {"j": 1, "kj": 1000, "焦耳": 1, "万j": 10000, "万焦耳": 10000}
    unit = energy[2].lower()
    if unit not in units:
        raise ValueError("最大能量单位仅支持 J、kJ、焦耳或万J，请换算后输入")

    # 可用/需求必须由用户明确给出，缺失资源不从 fixture 或 MySQL 偷换。
    groups = {"personnel": [], "support_materials": [], "equipment": []}
    resource_specs = (
        ("支护工|工程师|应急队", "personnel", "人"),
        ("锚杆|液压支柱", "support_materials", "根"),
        ("钻机", "equipment", "台"),
    )
    for names, group, unit_name in resource_specs:
        for match in re.finditer(rf"({names})\s*({NUMBER})\s*/\s*({NUMBER})\s*{unit_name}", text):
            if any(item.name == match[1] for item in groups[group]):
                raise ValueError(f"资源{match[1]}重复，请只提供一组可用/需求数量")
            groups[group].append(ResourceItem(resource_id=f"input-{group}-{len(groups[group])}", name=match[1], available=float(match[2]), required=float(match[3]), unit=unit_name))
    resources = ResourceSnapshot(**groups) if any(groups.values()) else None
    return MonitoringTask(
        mine_id=mine_id, roadway_id=roadway_id, area=area_name,
        source=source[1].strip() if source else "用户监测摘要",
        configuration_version=version[1].strip() if version else None,
        window_end=datetime.fromisoformat(end[1].strip()) if end else None,
        window_minutes=float(window[1]) * (60 if window[2] in ("小时", "h") else 1),
        event_count=count, max_energy_j=float(energy[1]) * units[unit],
        frequency_start=float(freq[1]) if freq else None, frequency_end=freq_end,
        b_start=float(bvalue[1]) if bvalue else None, b_end=b_end,
        missing_rate=missing_rate / 100,
        high_energy_count=high_count,
        spatial_cluster_fraction=cluster / 100 if cluster is not None else None,
        resources=resources,
    )
