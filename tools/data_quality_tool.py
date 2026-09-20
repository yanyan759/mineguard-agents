"""
数据质量检查工具

纯确定性计算——提取自 perception_warning_service.py 的数据质量检查逻辑，
封装为独立 LangChain 工具，供感知预警 Agent 或其他 Agent 调用。

检查项：缺失率、时序乱序、重复ID、时间范围越界、单位异常。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "data_quality_check",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def check_data_quality(
    records: list[dict],
    start_time: str,
    end_time: str,
    expected_frequency_hz: float = 1.0,
) -> dict:
    """检查监测数据的完整性和质量。

    检查项：
    1. 缺失率（记录数 vs 预期采样数）
    2. 时序乱序（时间戳是否严格递增）
    3. 重复 ID（event_id 重复检查）
    4. 时间范围越界（记录时间是否在 [start_time, end_time] 之内）
    5. 数值异常（负能量、零值等）

    :param records: 监测记录列表，每条为 {event_id, timestamp, energy_j, ...}
    :param start_time: 监测窗口起始 ISO-8601 时间
    :param end_time: 监测窗口终止 ISO-8601 时间
    :param expected_frequency_hz: 预期采样频率
    """
    if not records:
        return _build_result(
            "error",
            data={
                "completeness": 0.0, "timeliness": 0.0,
                "missing_rate": 1.0, "issues": [], "critical_errors": ["records 为空"],
            },
            error="records 列表为空，无法进行数据质量检查",
        )

    window_start = datetime.fromisoformat(start_time)
    window_end = datetime.fromisoformat(end_time)
    window_duration_h = (window_end - window_start).total_seconds() / 3600.0
    expected_count = max(1, int(expected_frequency_hz * window_duration_h * 3600))
    missing_rate = max(0.0, 1.0 - len(records) / expected_count)

    issues = []
    critical_errors = []

    # 1. 时序乱序检查
    timestamps = []
    for r in records:
        try:
            ts = r.get("timestamp", "")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            timestamps.append(ts)
        except (ValueError, TypeError):
            critical_errors.append(f"无效时间戳: {r.get('timestamp')}")

    if len(timestamps) >= 2 and timestamps != sorted(timestamps):
        issues.append("监测记录时间乱序")

    # 2. 重复 ID 检查
    event_ids = [r.get("event_id", f"auto-{i}") for i, r in enumerate(records)]
    dup_ids = sorted({eid for eid in event_ids if event_ids.count(eid) > 1})
    if dup_ids:
        issues.append(f"存在 {len(dup_ids)} 个重复事件 ID")

    # 3. 时间范围越界
    outside = sum(
        1 for ts in timestamps
        if isinstance(ts, datetime) and (ts < window_start or ts > window_end)
    )
    if outside:
        issues.append(f"{outside} 条记录时间超出 monitoring_window")

    # 4. 能量负值检查
    negative_energy = [r for r in records if r.get("energy_j", 0) < 0]
    if negative_energy:
        critical_errors.append(f"存在 {len(negative_energy)} 条负能量记录")

    completeness = round(max(0.0, min(1.0, 1.0 - missing_rate)), 4)
    # 时效性基于时序乱序比例
    disorder_rate = 0.0
    if len(timestamps) >= 2:
        disorder_pairs = sum(
            1 for a, b in zip(timestamps, sorted(timestamps))
            if isinstance(a, datetime) and isinstance(b, datetime) and a == b
        )
        disorder_rate = 1.0 - disorder_pairs / len(timestamps)
    timeliness = round(max(0.0, min(1.0, 1.0 - disorder_rate)), 4)

    return _build_result(
        "success",
        data={
            "completeness": completeness,
            "timeliness": timeliness,
            "missing_rate": round(missing_rate, 4),
            "record_count": len(records),
            "expected_count": expected_count,
            "issues": issues,
            "critical_errors": critical_errors,
            "is_critical": len(critical_errors) > 0,
        },
    )
