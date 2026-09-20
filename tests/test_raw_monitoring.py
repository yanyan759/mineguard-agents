"""原始记录入口的手算与六节点运行测试。"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.raw_monitoring import RawMonitoringTask, analyze_raw, parse_csv_records


def sample():
    return {"input_type": "raw_events", "mine_id": "MINE-TEST", "roadway_id": "RDW-TEST", "area": "测区A",
            "source": "手算测试", "method_id": "statistics-v1", "window_start": "2020-01-01T00:00:00Z",
            "window_end": "2020-01-01T01:00:00Z", "events": [
                {"event_id": "a", "timestamp": "2020-01-01T00:10:00Z", "energy": 2, "energy_unit": "kJ"},
                {"event_id": "b", "timestamp": "2020-01-01T00:20:00Z", "energy": 3000, "energy_unit": "J"}]}


class RawTests(unittest.TestCase):
    def test_aggregate_overflow_is_input_error(self):
        data = sample()
        for event in data["events"]: event.update(energy=1e308, energy_unit="J")
        with self.assertRaises(ValueError): RawMonitoringTask.model_validate(data)
        data=sample();data["resources"]={"equipment":[{"resource_id":"drill","name":"钻机","unit":"台","required":1,"available":float("inf")}]}
        with self.assertRaises(ValueError): RawMonitoringTask.model_validate(data)

    def test_profile_rejects_invalid_parameters_and_freezes_selected_version(self):
        from app.services.raw_monitoring import ResearchProfile, select_profile
        from app.services.monitoring_files import real_sample
        from unittest.mock import patch
        task = RawMonitoringTask.model_validate(real_sample(1)["task"])
        frozen = select_profile(task.method_id)
        bad = copy.deepcopy(frozen); bad["energy_threshold_j"] = float("nan")
        with self.assertRaises(ValueError): ResearchProfile.model_validate(bad)
        with patch("app.services.raw_monitoring.profiles", return_value=[]):
            self.assertEqual(analyze_raw(task, frozen)["profile"], frozen)

    def test_real_statistics_without_inventing_unknowns(self):
        result = analyze_raw(RawMonitoringTask.model_validate(sample()))
        self.assertEqual(result["metrics"]["event_count"], 2)
        self.assertEqual(result["metrics"]["max_energy_j"], 3000)
        self.assertEqual(result["metrics"]["frequency_per_hour"], 2)
        self.assertIsNone(result["metrics"]["b_value"])
        self.assertIsNone(result["metrics"]["missing_rate"])
        self.assertIsNone(result["risk_level"])

    def test_future_record_rejected(self):
        data = sample(); data["events"][0]["timestamp"] = data["window_end"]
        with self.assertRaisesRegex(ValueError, "窗口"):
            RawMonitoringTask.model_validate(data)

    def test_duplicate_conflicting_ids_rejected(self):
        data = sample(); data["events"][1]["event_id"] = "a"
        with self.assertRaisesRegex(ValueError, "重复"):
            RawMonitoringTask.model_validate(data)

    def test_csv_and_json_units_match(self):
        data = sample()
        data["events"] = parse_csv_records("event_id,timestamp,energy,energy_unit\na,2020-01-01T00:10:00Z,2,kJ\nb,2020-01-01T00:20:00Z,3000,J\n")
        self.assertEqual(analyze_raw(RawMonitoringTask.model_validate(data))["metrics"]["max_energy_j"], 3000)

    def test_research_parameters_cannot_apply_to_other_mine(self):
        data = sample(); data["method_id"] = "elkcreek-hourly-q99-v1"
        result = analyze_raw(RawMonitoringTask.model_validate(data))
        self.assertEqual(result["applicability"], "not_applicable")
        self.assertIsNone(result["alarm"])

    def test_nan_and_unknown_energy_unit_rejected(self):
        for energy, unit in [(float("nan"), "J"), (1, "MJ?")]:
            data = sample(); data["events"][0].update(energy=energy, energy_unit=unit)
            with self.assertRaises(ValueError): RawMonitoringTask.model_validate(data)

    def test_b_estimation_has_independent_value_and_sample_gate(self):
        data = sample(); data["b_method"] = {"method": "energy_mle", "minimum_energy_j": 1000, "minimum_samples": 50}
        data["events"] = [{"event_id": str(i), "timestamp": "2020-01-01T00:30:00Z", "energy": 10000, "energy_unit": "J"} for i in range(50)]
        result = analyze_raw(RawMonitoringTask.model_validate(data))
        self.assertAlmostEqual(result["metrics"]["b_value"], 0.4342944819, places=9)
        data["events"] = data["events"][:2]
        self.assertIsNone(analyze_raw(RawMonitoringTask.model_validate(data))["metrics"]["b_value"])

    def test_custom_parser_preserves_raw_records(self):
        from app.services.monitoring_task import parse_monitoring_task
        parsed = parse_monitoring_task(json.dumps(sample()))
        self.assertIsInstance(parsed, RawMonitoringTask)
        self.assertEqual(len(parsed.events), 2)

    def test_actual_six_node_workflow_with_unknown_risk(self):
        from app.api.workflow_runner import WorkflowRunner
        runner = WorkflowRunner()
        record = runner.start(scenario_id="custom", query=json.dumps(sample()))
        record.thread.join(25)
        self.assertEqual(record.status, "completed", record.error)
        for node in ["perception_warning", "knowledge_retrieval", "dispatch_decision", "resource_evaluation", "collaborative_control", "reflection_iteration"]:
            self.assertIn(node, record.phases)
        perception = record.final_state["perception_result"]
        self.assertIsNone(perception.risk_level)
        self.assertEqual(perception.assessment["metrics"]["max_energy_j"], 3000)
        self.assertIsNone(record.final_state["reflection_result"].risk_change.after)


if __name__ == "__main__": unittest.main()
