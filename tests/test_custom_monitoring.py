"""自定义输入回归：防止再次记录文字却用固定场景计算。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.monitoring_task import parse_monitoring_task
from app.services.perception_warning_service import PerceptionWarningService


NORMAL = "MINE-001 / RDW-010 / 工作面-10：过去30分钟监测到2条微震事件，频次由1次/h升至2次/h，最大能量1000 J，b值由1.4降至1.3，数据缺失率0%。高能事件0条，空间集中比例0%。"
YELLOW = "MINE-001 / RDW-010 / 工作面-10：过去30分钟监测到7条微震事件，频次由7.5次/h升至11.8次/h，最大能量85,000 J，b值由0.82降至0.68，数据缺失率4%。高能事件2条，空间集中比例90%。支护工8/6人、锚杆400/180根、钻机3/2台。"
RED = "MINE-002 / RDW-020 / 工作面-20：过去30分钟监测到12条微震事件，频次由15次/h升至30次/h，最大能量300 kJ，b值由0.9降至0.4，数据缺失率2%。高能事件6条，空间集中比例95%。支护工8/6人、锚杆400/180根、钻机3/2台。"


class CustomMonitoringTests(unittest.TestCase):
    def test_metrics_change_risk_without_fixture(self):
        """丢弃用户指标、固定黄色情景或单位换算错误均应失败。"""
        svc = PerceptionWarningService()
        low = parse_monitoring_task(NORMAL)
        high = parse_monitoring_task(RED)
        self.assertEqual(svc.analyze_summary(low).risk_level.value, "normal")
        self.assertEqual(svc.analyze_summary(high).risk_level.value, "red")
        self.assertEqual(high.max_energy_j, 300000)
        self.assertEqual(high.event_count, 12)
        self.assertEqual(high.roadway_id, "RDW-020")

    def test_numbers_and_resources_preserved(self):
        task = parse_monitoring_task(YELLOW)
        self.assertEqual(task.max_energy_j, 85000)
        self.assertEqual(task.frequency_end, 11.8)
        self.assertEqual(task.missing_rate, .04)
        self.assertEqual(task.resources.personnel[0].available, 8)
        self.assertEqual(task.resources.personnel[0].required, 6)
        self.assertEqual(task.resources.support_materials[0].required, 180)
        self.assertNotIn("microseismic_events", task.model_dump())

    def test_missing_inputs_never_fall_back_to_yellow(self):
        for query in ("分析307工作面", "", NORMAL.replace("最大能量1000 J，", "")):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_monitoring_task(query)

    def test_invalid_and_conflicting_values_are_rejected(self):
        for query in (
            NORMAL.replace("1000 J", "-1000 J"),
            NORMAL.replace("缺失率0%", "缺失率104%"),
            NORMAL.replace("高能事件0条", "高能事件3条"),
            NORMAL + "最大能量300000 J。",
            NORMAL.replace("最大能量1000 J", "最大能量1000 MJ"),
            NORMAL.replace("b值由1.4降至1.3", "b值由1.4降至0"),
        ):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_monitoring_task(query)

    def test_missing_optional_data_is_explicit(self):
        query = NORMAL.split("高能事件")[0]
        task = parse_monitoring_task(query)
        analysis = PerceptionWarningService().analyze_summary(task)
        self.assertIsNone(task.resources)
        self.assertTrue(analysis.quality.is_partial)
        self.assertTrue(analysis.quality.warnings)

    def test_runner_consumes_custom_data_and_preserves_it_on_reentry(self):
        """实际 LangGraph 运行必须消费文本，不能重新加载 yellow fixture。"""
        import asyncio
        from app.api.workflow_runner import WorkflowRunner

        async def run():
            runner = WorkflowRunner()
            record = await runner.start_async(scenario_id="custom", query=NORMAL)
            await record.task
            self.assertEqual(record.status, "completed", record.error)
            state = record.final_state
            self.assertIsNone(state["perception_result"].risk_level)
            self.assertEqual(state["perception_result"].affected_area.roadway_id, "RDW-010")
            self.assertEqual(state["custom_task"]["max_energy_j"], 1000)
            self.assertIsNone(state["fixture_scenario_id"])
            self.assertEqual(len([p for p in record.phases if p != "knowledge_write_back"]), 6)
        asyncio.run(run())

    def test_custom_resource_input_not_overwritten_and_missing_is_unknown(self):
        from app.agent.workflow_nodes import node_perception_warning, node_dispatch_decision, node_resource_evaluation, node_reflection_iteration
        from app.agent.workflow_state import new_workflow_state
        from app.agent.fixture_loader import FixtureDataSource

        def run(query):
            task = parse_monitoring_task(query)
            state = new_workflow_state(FixtureDataSource().load_scenario("yellow").context)
            state["custom_task"] = task.model_dump(mode="json")
            state.update(node_perception_warning(state))
            state.update(node_dispatch_decision(state))
            state.update(node_resource_evaluation(state))
            state.update(node_reflection_iteration(state))
            return state
        state = run(YELLOW.replace("支护工8/6人", "支护工1/6人").replace("锚杆400/180根", "锚杆0/180根").replace("钻机3/2台", "钻机0/2台"))
        self.assertEqual(state["resource_result"].feasibility, "infeasible")
        self.assertEqual(state["resource_result"].resource_gaps[0].available, 1)
        self.assertEqual(state["reflection_result"].outcome, "unknown")
        self.assertEqual(state["reflection_result"].risk_change.trend, "unknown")
        self.assertEqual(state["reflection_result"].return_decision.action, "wait_for_data")
        state = run(YELLOW.split("支护工")[0])
        self.assertEqual(state["resource_result"].feasibility, "unknown")

    def test_api_reports_missing_fields_before_starting_a_run(self):
        import asyncio
        from fastapi import HTTPException
        from app.api.server import start_workflow, WorkflowStartRequest
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(start_workflow(WorkflowStartRequest(scenario_id="custom", query="分析307工作面")))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("最大能量", raised.exception.detail)

    def test_red_summary_reaches_approval_with_original_metrics(self):
        import asyncio
        from app.api.workflow_runner import WorkflowRunner

        async def run():
            runner = WorkflowRunner()
            record = await runner.start_async(scenario_id="custom", query=RED)
            try:
                async with asyncio.timeout(30):
                    while record.status == "running":
                        await asyncio.sleep(.05)
                self.assertEqual(record.status, "waiting_human", record.error)
                self.assertEqual(record.approval_request["risk_level"], "red")
                self.assertEqual(record.final_state["custom_task"]["max_energy_j"], 300000)
                self.assertIsNone(record.final_state["control_result"])
            finally:
                if not record.task.done():
                    await runner.cancel(record.run_id)
                    await record.task
        from tests.actual_helpers import configured_summary
        with configured_summary(RED):
            asyncio.run(run())

    def test_empty_json_resources_remain_unknown(self):
        import json
        data = parse_monitoring_task(NORMAL).model_dump()
        data["resources"] = {}
        self.assertIsNone(parse_monitoring_task(json.dumps(data)).resources)


if __name__ == "__main__":
    unittest.main()
