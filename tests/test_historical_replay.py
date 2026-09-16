"""真实回放的独立手算测试：防未来泄漏、目录重复和评价虚报。"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.historical_replay import (
    combine_catalogs, filter_catalog, hourly_features, fit_baseline,
    predict_baseline, evaluate_predictions,
)


def utc(values):
    return pd.to_datetime(values, utc=True, format="mixed")


class HistoricalReplayTests(unittest.TestCase):
    def test_window_excludes_event_at_prediction_time_and_future(self):
        events = pd.DataFrame({
            "time": utc(["2020-01-01 00:00", "2020-01-01 00:59", "2020-01-01 01:00", "2020-01-01 02:00"]),
            "energy_total": [10., 20., 9999., 99999.],
        })
        result = hourly_features(events, utc(["2020-01-01 01:00"]))
        self.assertEqual(result.iloc[0].event_count, 2)
        self.assertEqual(result.iloc[0].max_energy_j, 20.)

    def test_empty_window_has_zero_catalog_records_but_unknown_hardware_loss(self):
        events = pd.DataFrame({"time": utc([]), "energy_total": []})
        result = hourly_features(events, utc(["2020-01-01 01:00"]))
        self.assertEqual(result.iloc[0].event_count, 0)
        self.assertEqual(result.iloc[0].max_energy_j, 0)
        self.assertIsNone(result.iloc[0].missing_rate)

    def test_missing_energy_is_not_silently_zeroed(self):
        events = pd.DataFrame({"time": utc(["2020-01-01 00:01"]), "energy_total": [float("nan")]})
        result = hourly_features(events, utc(["2020-01-01 01:00"]))
        self.assertEqual(result.iloc[0].event_count, 1)
        self.assertTrue(pd.isna(result.iloc[0].max_energy_j))
        self.assertFalse(result.iloc[0].eligible)

    def test_overlap_uses_reprocessed_catalog_and_only_named_ims_gaps(self):
        rs = pd.DataFrame({"time": utc(["2012-12-01", "2012-12-02 08:00"]), "energy_total": [2., 3.]})
        ims = pd.DataFrame({"time": utc(["2012-11-30", "2012-12-01", "2012-12-02 03:00", "2012-12-02 08:00", "2013-03-01"]), "energy_total": [1., 200., 4., 300., 5.]})
        result, audit = combine_catalogs(rs, ims)
        self.assertEqual(result.energy_total.tolist(), [1., 2., 4., 3.])
        self.assertEqual(audit["ims_selected"], 2)

    def test_known_burst_cannot_bypass_quality_and_unknown_status_rejected(self):
        df = pd.DataFrame({"time": utc(["2010-12-04", "2010-12-05", "2010-12-06", "2010-12-07"]),
            "x": [11000.]*4, "y": [5000.]*4, "z": [1700.]*4,
            "event_status": ["RockSigma", "RockSigma", "Reject", "mystery"],
            "location_residual": [114., 20., 1., float("nan")]})
        result, audit = filter_catalog(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0].time, pd.Timestamp("2010-12-05", tz="UTC"))

    def test_fit_excludes_test_interval_and_strict_threshold_comparison(self):
        features = pd.DataFrame({"time": utc(["2020-01-01", "2020-01-02", "2020-01-03"]),
            "event_count": [1, 2, 100000], "max_energy_j": [10., 20., 1e12], "eligible": [True]*3})
        params = fit_baseline(features, pd.Timestamp("2019-12-31 23:00", tz="UTC"), pd.Timestamp("2020-01-02", tz="UTC"))
        self.assertEqual(params["count_threshold"], 2)
        self.assertEqual(params["energy_threshold_j"], 20.)
        self.assertEqual(predict_baseline(features, params).tolist(), [False, False, True])

    def test_empty_calibration_fails_instead_of_inventing_thresholds(self):
        features = pd.DataFrame({"time": utc([]), "event_count": [], "max_energy_j": [], "eligible": []})
        with self.assertRaisesRegex(ValueError, "校准"):
            fit_baseline(features, pd.Timestamp("2020-01-01", tz="UTC"), pd.Timestamp("2020-01-03", tz="UTC"))

    def test_future_target_boundary_and_missed_event_are_honest(self):
        frame = pd.DataFrame({"time": pd.date_range("2020-01-01", "2020-01-04", freq="h", tz="UTC"),
            "eligible": True, "alarm": False})
        frame.loc[[0, 24], "alarm"] = True
        bursts = pd.DataFrame({"time": utc(["2020-01-02 00:00", "2020-01-04 00:00"])})
        metrics, events = evaluate_predictions(frame, bursts, pd.Timestamp("2020-01-05", tz="UTC"))
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]), (1, 1, 47, 24))
        self.assertEqual(metrics["event_hits"], 1)
        self.assertEqual(metrics["event_misses"], 1)
        self.assertEqual(events[0]["first_lead_hours"], 24.)
        self.assertIsNone(events[1]["first_lead_hours"])

    def test_right_censored_windows_excluded_and_adjacent_alarm_hours_one_episode(self):
        frame = pd.DataFrame({"time": utc(["2020-01-01 00:00", "2020-01-01 01:00", "2020-01-02 23:00"]),
            "eligible": [True]*3, "alarm": [True, True, True]})
        metrics, _ = evaluate_predictions(frame, pd.DataFrame({"time": utc([])}), pd.Timestamp("2020-01-03", tz="UTC"))
        self.assertEqual(metrics["evaluated_windows"], 2)
        self.assertEqual(metrics["right_censored_windows"], 1)
        self.assertEqual(metrics["alarm_episodes"], 1)
        self.assertEqual(metrics["episodes_without_known_burst"], 1)


if __name__ == "__main__":
    unittest.main()
