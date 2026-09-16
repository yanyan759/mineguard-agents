"""历史目录回放：冻结研究参数、隔离未来标签，不生成现场安全许可。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def combine_catalogs(rs: pd.DataFrame, ims: pd.DataFrame):
    """按作者给定的覆盖关系补目录，不能将两套对同一事件的估计相加。"""
    if rs.empty:
        raise ValueError("缺少RockSigma目录，不能静默替换来源")
    gap_start = pd.Timestamp("2012-12-02", tz="UTC")
    gap_end = gap_start + pd.Timedelta(hours=7)
    selected = ims.loc[(ims.time < rs.time.min()) | ((ims.time > gap_start) & (ims.time < gap_end))]
    # 缺口内仍可能有重处理记录；同一时间优先重处理来源。
    selected = selected.loc[~selected.time.isin(rs.time)]
    combined = pd.concat([rs, selected], ignore_index=True).sort_values("time")
    compare = [c for c in combined.columns if c not in {"source_file", "source_row"}]
    dedup = combined.drop_duplicates(subset=compare).reset_index(drop=True)
    return dedup, {"rs_rows": len(rs), "ims_rows": len(ims), "ims_selected": len(selected),
                   "exact_duplicates_removed": len(combined)-len(dedup),
                   "timestamp_conflicts": int(dedup.time.duplicated(keep=False).sum())}


def filter_catalog(df: pd.DataFrame):
    """质量口径参考作者；绝不按未来事故名单豁免，缺少残差单独计数。"""
    status = df.event_status
    allowed = status.isin(["RockSigma", "Auto", "Manual"])
    residual = df.location_residual
    quality = ((status.eq("RockSigma") & residual.lt(80)) |
               (status.isin(["Auto", "Manual"]) & residual.lt(30)) | residual.isna())
    spatial = df.x.between(9800, 12800) & df.y.between(4200, 6500) & df.z.between(1400, 2100)
    result = df.loc[allowed & quality & spatial].copy()
    return result, {"before_filter": len(df), "after_filter": len(result),
                    "rejected_status": int((~allowed).sum()),
                    "retained_unknown_location_residual": int(result.location_residual.isna().sum()),
                    "label_based_exceptions": 0}


def hourly_features(events: pd.DataFrame, endpoints: pd.DatetimeIndex):
    """整点t使用[t-1h,t)；零条目录记录不等于设备无漏采。"""
    endpoints = pd.DatetimeIndex(endpoints)
    if not endpoints.equals(endpoints.floor("h")):
        raise ValueError("预测时刻必须为整点")
    events = events.copy()
    events["endpoint"] = events.time.dt.floor("h") + pd.Timedelta(hours=1)
    energy = events.energy_total
    valid = np.isfinite(energy) & energy.ge(0)
    events["valid_energy"] = energy.where(valid)
    events["invalid_energy"] = ~valid
    grouped = events.groupby("endpoint").agg(
        event_count=("time", "size"), max_energy_j=("valid_energy", "max"),
        invalid_energy_count=("invalid_energy", "sum"), last_event_time=("time", "max"))
    result = grouped.reindex(endpoints)
    empty = result.event_count.isna()
    result["event_count"] = result.event_count.fillna(0).astype(int)
    result["invalid_energy_count"] = result.invalid_energy_count.fillna(0).astype(int)
    result.loc[empty, "max_energy_j"] = 0.
    result["eligible"] = result.invalid_energy_count.eq(0)
    # 有坏能量的窗口仍保留计数，但不让部分最大值冒充完整最大能量。
    result.loc[~result.eligible, "max_energy_j"] = np.nan
    result["missing_rate"] = None
    result["frequency_per_hour"] = result.event_count
    return result.rename_axis("time").reset_index()


def fit_baseline(features: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    """只从预先固定的校准段计算99%分位数，测试标签不参与拟合。"""
    calibration = features.loc[(features.time > start) & (features.time <= end) & features.eligible]
    # 预测端点end的输入是[end-1h,end)，仍完全属于校准时段。
    if calibration.empty:
        raise ValueError("没有可用于校准的完整窗口")
    return {"method": "elkcreek_hourly_q99_or_v1", "quantile": .99,
            "count_threshold": int(calibration.event_count.quantile(.99, interpolation="higher")),
            "energy_threshold_j": float(calibration.max_energy_j.quantile(.99, interpolation="higher")),
            "calibration_start_utc": start.isoformat(), "calibration_end_utc": end.isoformat(),
            "calibration_windows": len(calibration), "comparison": "strict_greater_or",
            "approval_status": "research_only", "horizon_hours": 24}


def predict_baseline(features: pd.DataFrame, params: dict):
    """这只是透明的研究基线，没有五色等级或现场危险概率含义。"""
    return features.eligible & ((features.event_count > params["count_threshold"]) |
                                (features.max_energy_j > params["energy_threshold_j"]))


def evaluate_predictions(predictions: pd.DataFrame, bursts: pd.DataFrame, data_end: pd.Timestamp):
    """窗口混淆矩阵和逐事件命中分别报告，右删失窗不冒充真负例。"""
    predictions = predictions.sort_values("time").copy()
    horizon = pd.Timedelta(hours=24)
    complete = predictions.time + horizon <= data_end
    evaluable = complete & predictions.eligible
    frames = predictions.loc[evaluable].copy()
    # 刚发生或发生在预测时刻的事件不能记为这次预测命中。
    targets = np.zeros(len(frames), dtype=bool)
    for timestamp in bursts.time:
        targets |= ((frames.time < timestamp) & (timestamp <= frames.time + horizon)).to_numpy()
    alarm = frames.alarm.to_numpy(dtype=bool)
    tp, fp = int((alarm & targets).sum()), int((alarm & ~targets).sum())
    fn, tn = int((~alarm & targets).sum()), int((~alarm & ~targets).sum())
    episode_starts = alarm & (~frames.alarm.shift(fill_value=False).to_numpy(dtype=bool) |
                              frames.time.diff().ne(pd.Timedelta(hours=1)).to_numpy())
    episode_id = np.cumsum(episode_starts)
    episode_targets = {int(i): False for i in episode_id[alarm]}
    for i, has_target in zip(episode_id[alarm], targets[alarm]):
        episode_targets[int(i)] |= bool(has_target)
    event_results = []
    for number, timestamp in enumerate(bursts.time, start=1):
        # 事件评价可以利用其之前的预测，即使该预测未来24h超出目录结束时间。
        pre = predictions.loc[(predictions.time < timestamp) & (predictions.time >= timestamp-horizon)]
        available = pre.loc[pre.eligible]
        hits = available.loc[available.alarm]
        covered = len(pre) == 24 and len(available) == 24 and timestamp <= data_end
        status = "hit" if covered and not hits.empty else "miss" if covered else "not_evaluable"
        event_results.append({"event_number": number, "time_utc": timestamp.isoformat(),
                              "status": status, "available_prediction_windows": len(available),
                              "first_lead_hours": (timestamp-hits.time.min()).total_seconds()/3600 if status == "hit" else None,
                              "latest_lead_hours": (timestamp-hits.time.max()).total_seconds()/3600 if status == "hit" else None})
    metrics = {"evaluated_windows": len(frames), "right_censored_windows": int((~complete).sum()),
               "invalid_input_windows": int((~predictions.eligible).sum()),
               "tp": tp, "fp": fp, "fn": fn, "tn": tn,
               "precision": tp/(tp+fp) if tp+fp else None,
               "recall": tp/(tp+fn) if tp+fn else None,
               "alarm_fraction": (tp+fp)/len(frames) if len(frames) else None,
               "alarm_episodes": len(episode_targets),
               "episodes_without_known_burst": sum(not v for v in episode_targets.values()),
               "event_hits": sum(e["status"] == "hit" for e in event_results),
               "event_misses": sum(e["status"] == "miss" for e in event_results),
               "events_not_evaluable": sum(e["status"] == "not_evaluable" for e in event_results)}
    return metrics, event_results
