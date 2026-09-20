"""反馈幂等、时序和前后比较必须由实际记录验证。"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.workflow_feedback import RunArchive, validate_feedback, compare_observations
from app.services.raw_monitoring import RawMonitoringTask, analyze_raw
from test_raw_monitoring import sample


class FeedbackTests(unittest.TestCase):
    def test_summary_feedback_uses_its_actual_windows_and_metrics(self):
        from app.services.monitoring_task import parse_monitoring_task
        from app.services.mine_configuration import prepare_actual
        import json
        task=dict(mine_id="SUMMARY-FEEDBACK",roadway_id="LANE-A",area="东区",source="同一监测系统",
                  window_minutes=60,window_end="2026-09-16T01:00:00Z",event_count=5,max_energy_j=6000,
                  frequency_end=5,b_end=1.1,missing_rate=0)
        snapshot=prepare_actual(parse_monitoring_task(json.dumps(task)))
        parent={"input":snapshot["task"],"result":{"status":"completed","agents":{"perception_result":{"assessment":snapshot["assessment"]},"decision_result":{"primary_plan":{"actions":[{"sequence":1}]}}}}}
        request={"observation":{**task,"window_end":"2026-09-16T03:00:00Z","max_energy_j":2000},"reported_by":"反馈人","actions":[]}
        new=validate_feedback(parent,request)
        result=compare_observations(parent,new,prepare_actual(new)["assessment"])
        self.assertTrue(result["comparable"],result)
        self.assertEqual(result["changes"]["max_energy_j"]["delta"],-4000)
    def test_malformed_action_and_quality_mismatch(self):
        req = self.request(); req["actions"] = [{}]
        with self.assertRaises(ValueError): validate_feedback(self.parent(), req)
        req = self.request(); req["observation"]["missing_rate"] = 0.5
        result = compare_observations(self.parent(), validate_feedback(self.parent(), req))
        self.assertFalse(result["comparable"])

    def test_same_run_id_does_not_execute_again_and_survives_restart(self):
        import asyncio, json, uuid
        from app.api.workflow_runner import WorkflowRunner
        async def exercise():
            runner = WorkflowRunner()
            run_id = "test-idempotent-" + uuid.uuid4().hex
            original = await runner.start_async(scenario_id="custom", run_id=run_id, query=json.dumps(sample()))
            await original.task
            events = len(original.events)
            repeated = await runner.start_async(scenario_id="custom", run_id=run_id, query=json.dumps(sample()))
            self.assertIs(repeated, original)
            self.assertEqual(len(repeated.events), events)
            self.assertEqual(WorkflowRunner().get_status(run_id)["status"], "completed")
            with self.assertRaises(ValueError):
                await runner.start_async(scenario_id="custom", run_id=run_id, query="different input")
        asyncio.run(exercise())

    def parent(self):
        task = sample()
        return {"input": task, "result": {"run_id": "parent", "status": "completed", "agents": {
            "decision_result": {"primary_plan": {"actions": [{"sequence": 1}]}},
            "perception_result": {"assessment": analyze_raw(RawMonitoringTask.model_validate(task))}}}}

    def request(self):
        task = sample(); task["window_start"] = "2020-01-01T02:00:00Z"; task["window_end"] = "2020-01-01T03:00:00Z"
        for e in task["events"]: e["timestamp"] = e["timestamp"].replace("T00:", "T02:")
        return {"request_id": "request1", "reported_by": "现场人工报告", "note": "复查", "observation": task,
                "actions": [{"sequence": 1, "status": "completed", "occurred_at": "2020-01-01T01:30:00Z"}]}

    def test_feedback_before_action_or_other_mine_rejected(self):
        for change in ["time", "mine"]:
            req = self.request()
            if change == "time": req["actions"][0]["occurred_at"] = "2020-01-01T04:00:00Z"
            else: req["observation"]["mine_id"] = "OTHER"
            with self.assertRaises(ValueError): validate_feedback(self.parent(), req)

    def test_idempotency_persists_and_conflicting_retry_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/"archive.sqlite"
            archive = RunArchive(path)
            archive.save_run("parent", self.parent())
            one, created = archive.reserve_feedback("parent", "same", {"v": 1})
            two, repeated = RunArchive(path).reserve_feedback("parent", "same", {"v": 1})
            self.assertTrue(created); self.assertFalse(repeated)
            self.assertEqual(one["child_run_id"], two["child_run_id"])
            with self.assertRaises(ValueError): archive.reserve_feedback("parent", "same", {"v": 2})
            self.assertEqual(RunArchive(path).get_run("parent")["result"]["status"], "completed")

    def test_matching_observation_reports_delta_without_causal_success(self):
        req = self.request(); req["observation"]["events"][1]["energy"] = 1000
        task = validate_feedback(self.parent(), req)
        result = compare_observations(self.parent(), task)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["changes"]["max_energy_j"]["delta"], -1000)
        self.assertFalse(result["causal_effect_verified"])

    def test_method_change_makes_observations_not_comparable(self):
        req = self.request(); req["observation"]["method_id"] = "elkcreek-hourly-q99-v1"
        task = validate_feedback(self.parent(), req)
        self.assertFalse(compare_observations(self.parent(), task)["comparable"])


if __name__ == "__main__": unittest.main()
