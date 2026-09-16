"""执行公开历史目录回放，输出真实统计、固定研究参数与可追溯评价。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.historical_replay import (
    combine_catalogs, filter_catalog, hourly_features, fit_baseline,
    predict_baseline, evaluate_predictions,
)

BURST_SHA256 = "a379a92bf0cebf174451ee12135406278d96fbe6c7a36ed1220ef3585be2378f"
SOURCE_COMMIT = "e08af5c89a3ac440458d2ac93fe40eb7996a33ea"
START = pd.Timestamp("2010-08-01", tz="UTC")
SPLIT = pd.Timestamp("2010-10-30", tz="UTC")
END = pd.Timestamp("2013-02-01", tz="UTC")


def load_archive(path: Path):
    """读取白名单目录，不解压或执行外部代码；原始行号按文件内0起始记录。"""
    members = []
    with zipfile.ZipFile(path) as archive:
        burst_name = next(n for n in archive.namelist() if n.endswith("/data/raw/events/burst_events.csv"))
        prefix = burst_name.split("data/raw/")[0]

        def read(name):
            data = archive.read(name)
            members.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            return data

        burst_bytes = read(burst_name)
        if hashlib.sha256(burst_bytes).hexdigest() != BURST_SHA256:
            raise ValueError("事故标签文件与已在线核对版本不一致，需重新核验来源")
        bursts = pd.read_csv(io.BytesIO(burst_bytes))
        bursts["time"] = pd.to_datetime(bursts.time, utc=True)
        catalogs = []
        common = {"Date": "time", "Location X": "x", "Location Y": "y", "Location Z": "z"}
        for kind in ["rocksigma", "ims"]:
            mapping = common | ({"totalRadiatedEnergy": "energy_total", "Classification Tag": "event_status",
                                  "Location Error": "location_residual"} if kind == "rocksigma" else
                                 {"Energy Total": "energy_total", "Imported Tag": "event_status",
                                  "Location Residual": "location_residual"})
            frames = []
            names = sorted(n for n in archive.namelist()
                           if n.startswith(prefix+f"data/raw/events/{kind}_events/") and n.endswith(".parquet"))
            if not names:
                raise ValueError(f"缺少{kind}原始目录")
            for name in names:
                df = pd.read_parquet(io.BytesIO(read(name)), columns=list(mapping)).rename(columns=mapping)
                df["time"] = pd.to_datetime(df.time, utc=True, format="mixed", errors="raise")
                if df.time.isna().any():
                    raise ValueError(f"原始事件时间缺失：{name}")
                if kind == "rocksigma":
                    df["event_status"] = df.event_status.map({"e": "RockSigma", "n": "Reject"})
                df["source_file"] = name
                df["source_row"] = np.arange(len(df))
                frames.append(df)
            catalogs.append(pd.concat(frames, ignore_index=True))
        # 保存本次参照的上游规则源码哈希，未声称所有文件等同上游最新版本。
        for name in ["a010_combine_catalogs.py", "a020_filter_events.py", "src/elkcreek/events.py", "local.py", "data/raw/README.md"]:
            read(prefix+name)
    combined, merge_audit = combine_catalogs(*catalogs)
    filtered, quality_audit = filter_catalog(combined)
    return filtered, bursts, {"source_archive": str(path.resolve()), "source_url": "https://github.com/niosh-mining/elkcreek",
                              "verified_burst_commit": SOURCE_COMMIT, "members": members,
                              "merge": merge_audit, "quality": quality_audit}


def write_json(path, value):
    """禁止NaN/Infinity伪装成合法JSON数值。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def run(archive_path: Path, output: Path):
    print("读取原始目录并核对标签来源……", flush=True)
    events, bursts, audit = load_archive(archive_path)
    output.mkdir(parents=True, exist_ok=True)
    print(f"质量筛选后{len(events):,}条，计算严格历史窗口……", flush=True)
    if events.time.min() > START or events.time.max() < END:
        raise ValueError("原始目录时间范围不能覆盖预定校准/验证范围")
    features = hourly_features(events, pd.date_range(START+pd.Timedelta(hours=1), END, freq="h"))
    if not (features.last_event_time.dropna() < features.loc[features.last_event_time.notna(), "time"]).all():
        raise AssertionError("检测到未来事件进入输入")
    params = fit_baseline(features, START, SPLIT)
    test = features.loc[(features.time > SPLIT) & (features.time <= END)].copy()
    test["alarm"] = predict_baseline(test, params)
    metrics, event_results = evaluate_predictions(test, bursts, END)
    always = test.copy()
    always["alarm"] = always.eligible
    reference_metrics, _ = evaluate_predictions(always, bursts, END)
    audit["data_min_time_utc"] = events.time.min().isoformat()
    audit["data_max_time_utc"] = events.time.max().isoformat()
    audit["empty_catalog_hours"] = int(test.event_count.eq(0).sum())
    audit["invalid_energy_records"] = int((~np.isfinite(events.energy_total) | events.energy_total.lt(0)).sum())
    audit["no_future_event_leakage_check"] = True
    audit["hardware_uptime_verified"] = False
    audit["raw_catalog_reprocessed_retrospectively"] = True
    audit["current_simulation_v1_evaluation"] = "not_evaluable: b值和统计口径未对接；本次评价独立双指标研究基线"
    for name in ["services/historical_replay.py", "scripts/replay_elkcreek.py"]:
        audit.setdefault("implementation_sha256", {})[name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    write_json(output/"source_manifest.json", audit)
    write_json(output/"parameters.json", params)
    write_json(output/"metrics.json", {"research_baseline": metrics, "always_alarm_reference": reference_metrics,
                                      "validation_start_utc": SPLIT.isoformat(), "validation_end_utc": END.isoformat()})
    write_json(output/"event_results.json", event_results)
    events.to_parquet(output/"filtered_events.parquet", index=False)
    test.to_parquet(output/"hourly_predictions.parquet", index=False)
    # 每次已知事件前约6小时取一个输入快照。标签与输入分开，不能把事件本身能量喂入预测。
    samples = []
    for event in event_results:
        endpoint = (pd.Timestamp(event["time_utc"])-pd.Timedelta(hours=6)).floor("h")
        row = test.loc[test.time.eq(endpoint)].iloc[0]
        start = endpoint-pd.Timedelta(hours=1)
        observed = events.loc[(events.time >= start) & (events.time < endpoint)]
        samples.append({"input": {"dataset": "NIOSH Elk Creek", "window_start_utc": start.isoformat(),
                                  "prediction_time_utc": endpoint.isoformat(), "event_count": int(row.event_count),
                                  "max_energy_j": float(row.max_energy_j) if pd.notna(row.max_energy_j) else None,
                                  "frequency_per_hour": int(row.frequency_per_hour), "b_value": None,
                                  "missing_rate": None, "raw_records_file": f"event_{event['event_number']}_input.json"},
                        "computed_alarm": bool(row.alarm), "input_eligible": bool(row.eligible),
                        "evaluation_only": {"event_number": event["event_number"], "known_burst_time_utc": event["time_utc"],
                                            "known_burst_within_next_24h": True}})
        (output/f"event_{event['event_number']}_input.json").write_text(
            observed.to_json(orient="records", date_format="iso", indent=2, force_ascii=False), encoding="utf-8")
    write_json(output/"replay_samples.json", samples)
    pct = lambda n: f"{n:.2%}" if n is not None else "未定义"
    event_rows = "\n".join(f"| {e['event_number']} | {e['time_utc']} | {e['status']} | {e['first_lead_hours']:.2f} | {e['latest_lead_hours']:.2f} |"
                           if e["status"] == "hit" else f"| {e['event_number']} | {e['time_utc']} | {e['status']} | — | — |" for e in event_results)
    report = f"""# Elk Creek真实历史回放结果

这是已实际执行的双指标研究基线回放。**不是矿方批准参数，不是现有simulation-v1七指标评分的验证，也不是前瞻现场试验。**

## 数据和固定方法

- 来源：[NIOSH官方数据](https://github.com/niosh-mining/elkcreek)、[原始论文](https://doi.org/10.1007/s42461-026-01497-0)。五次标签文件已与在线固定版本逐字节核对；其他实际读取文件的SHA256见source_manifest.json。
- 两目录原始行数：RockSigma {audit['merge']['rs_rows']:,}、IMS {audit['merge']['ims_rows']:,}。按覆盖规则选取IMS {audit['merge']['ims_selected']:,}条；完全重复删除{audit['merge']['exact_duplicates_removed']}条。
- 最终质量筛选保留{len(events):,}条；未知定位残差{audit['quality']['retained_unknown_location_residual']:,}条，按原作条件保留并标记。未按已知事故名单豁免筛选。
- 校准：2010-08-01至2010-10-30，共{params['calibration_windows']}个有效小时窗口。验证：2010-10-30至2013-02-01。
- 每整点只使用过去1小时事件；预警目标为未来24小时内的已知burst。99%分位数严格超限取OR，事先固定，验证后未调参。
- 参数：每小时事件数 **>{params['count_threshold']}** 或该小时最大单次能量 **>{params['energy_threshold_j']:.6f} J** 触发研究预警。它们是本次校准段的统计分位数，不是生产阈值。

## 验证段实际结果

| 指标 | 研究基线 | 始终报警参考 |
| --- | --- | --- |
| 可评价窗口数 | {metrics['evaluated_windows']} | {reference_metrics['evaluated_windows']} |
| 事件命中 / 漏报 / 不可评价 | {metrics['event_hits']} / {metrics['event_misses']} / {metrics['events_not_evaluable']} | {reference_metrics['event_hits']} / {reference_metrics['event_misses']} / {reference_metrics['events_not_evaluable']} |
| 报警窗口占比 | {pct(metrics['alarm_fraction'])} | {pct(reference_metrics['alarm_fraction'])} |
| 窗口precision | {pct(metrics['precision'])} | {pct(reference_metrics['precision'])} |
| 窗口recall | {pct(metrics['recall'])} | {pct(reference_metrics['recall'])} |

研究基线窗口 TP={metrics['tp']}，FP={metrics['fp']}，FN={metrics['fn']}，TN={metrics['tn']}。连续报警段{metrics['alarm_episodes']}段，其中{metrics['episodes_without_known_burst']}段没有在其任一预警窗口的未来24小时内对应这5个已知事件。

末尾右删失排除{metrics['right_censored_windows']}个窗口；坏能量导致输入不可评价{metrics['invalid_input_windows']}个窗口。无目录记录的小时共{audit['empty_catalog_hours']}，不能据此认定设备正常在线或现场安全。

| 事件 | 真实UTC时间 | 结果 | 首次提前量(h) | 最近提前量(h) |
| --- | --- | --- | --- | --- |
{event_rows}

首次提前量仅指事件前24小时评价区间内最早报警，**不表示报警只持续了该时长**。hit不代表该算法只针对该次事件报警；必须结合总体报警占比与FP评估。

## 可复核文件

- parameters.json：冻结的研究参数、校准时间及比较规则。
- hourly_predictions.parquet：全部验证小时的输入、最后原始事件时间、完整性和预警结果。
- filtered_events.parquet：筛选后的原始事件字段、来源成员文件和0起始原始行号。
- source_manifest.json：数据成员哈希、质量统计、实现哈希和来源核对范围。
- metrics.json、event_results.json：完整评价及每次已知事件结果。
- replay_samples.json及event_1_input.json至event_5_input.json：各事件前约6小时的真实输入与实际结果。输入与事后标签分开。

## 结果能说明与不能说明的事情

本轮完成了真实目录读取、无事件时间泄漏的回放及独立时间段评价。没有验证设备在线率；非目标窗口只能称未对应这5个已知burst，不能认定没有其他危险。数据源是事后重处理目录，无法证明所有测值在原时刻已经可用。测区边界来自回顾性论文；参数只在前90天计算，不代表空间选择和研究设计达到严格盲测。

五次事件属于同一美国矿井的小样本，不能给出跨矿准确率或部署许可。本研究基线未使用b值、应力、地音或处置资源，没有完成顶板各灾种的综合预警。现有前端输入框仍要求b值和缺失率，**这些回放样本不能靠补造两个数值直接粘贴成现场有效任务**。

下一步有效性工作应补充目标矿井的原始序列、设备在线记录、灾害标签和适用参数，再按相同协议进行独立验证。本次所有参数保持research_only，没有自动写入warning_thresholds_v1.py。

## 重现命令

在agent1目录运行：`python scripts/replay_elkcreek.py`。
专项测试：`python -m unittest discover -s tests -p test_historical_replay.py`。
依赖：pandas、numpy、pyarrow；使用已存在的公开ZIP，不需要LLM、Docker或网络。
"""
    (output/"report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"report": str(output/"report.md"), "parameters": params, "metrics": metrics}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT/"煤矿资料收集/07_监测阈值标定_研究文献/ElkCreek矿震数据集_NIOSH开源.zip")
    parser.add_argument("--output", type=Path, default=ROOT/"output/validation/elkcreek")
    args = parser.parse_args()
    run(args.archive, args.output)
