"""验证真实证据、未知状态和规则建议，不将模拟完成状态当作现场成功。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.workflow_state import new_workflow_state
from app.agent.fixture_loader import FixtureDataSource
from app.agent.workflow_nodes import (node_perception_warning, node_knowledge_retrieval,
    node_dispatch_decision, node_resource_evaluation, node_collaborative_control,
    node_reflection_iteration)
from app.services.monitoring_task import parse_monitoring_task

BLUE = "MINE-003 / RDW-027 / 工作面-27：过去45分钟监测到4条微震事件，频次由3.2次/h升至6.4次/h，最大能量12500 J，b值由1.28降至1.19，数据缺失率3%。高能事件0条，空间集中比例35%。支护工7/5人、工程师1/1人、锚杆260/140根、液压支柱36/24根、钻机2/1台。"


def blue_state():
    """保持真实节点计算，仅借已有契约创建隔离测试上下文。"""
    state = new_workflow_state(FixtureDataSource().load_scenario("yellow").context)
    state["custom_task"] = parse_monitoring_task(BLUE).model_dump(mode="json")
    state["user_query"] = BLUE
    state.update(node_perception_warning(state))
    return state


class AssessmentEvidenceTests(unittest.TestCase):
    def test_confidence_and_after_risk_are_unknown_not_zero(self):
        state = blue_state()
        self.assertIsNone(state["perception_result"].confidence)
        state.update(node_reflection_iteration(state))
        reflection = state["reflection_result"]
        self.assertIsNone(reflection.risk_change.after)
        self.assertIsNone(reflection.goal_completion_score)

    def test_unconfigured_custom_knowledge_does_not_inject_bundled_documents(self):
        state = blue_state()
        state.update(node_knowledge_retrieval(state))
        knowledge = state["knowledge_result"]
        self.assertIsNone(knowledge.confidence)
        self.assertFalse(knowledge.regulations)
        self.assertFalse(knowledge.similar_cases)
        for reference in knowledge.regulations:
            self.assertTrue(reference.source_url.startswith("https://www.gov.cn/"))
            self.assertTrue(reference.applicability)
            self.assertNotIn("100000J", reference.requirement)
        for case in knowledge.similar_cases:
            self.assertTrue(case.case_id.startswith("real-"))
            self.assertIsNone(case.similarity)
            self.assertTrue(case.match_terms)

    def test_blue_plan_matches_recheck_and_is_explicitly_a_draft(self):
        state = blue_state()
        state.update(node_knowledge_retrieval(state))
        state.update(node_dispatch_decision(state))
        result = state["decision_result"]
        actions = result.primary_plan.actions
        self.assertFalse(any("保持常规监测" in a.action for a in actions))
        self.assertTrue(any("复查" in a.action for a in actions))
        self.assertTrue(all(a.deadline_minutes is None for a in actions))
        self.assertIsNone(result.confidence)
        self.assertEqual(result.evaluation_basis, "actual_snapshot")

    def test_resource_counts_are_not_execution_permission(self):
        state = blue_state()
        state.update(node_dispatch_decision(state))
        state.update(node_resource_evaluation(state))
        result = state["resource_result"]
        self.assertEqual(result.quantity_check, "sufficient")
        self.assertEqual(result.execution_readiness, "unverified")
        self.assertEqual(result.feasibility, "unknown")
        self.assertIsNone(result.confidence)

    def test_no_identity_or_dispatch_is_invented(self):
        state = blue_state()
        state.update(node_dispatch_decision(state))
        state.update(node_resource_evaluation(state))
        state.update(node_collaborative_control(state))
        control = state["control_result"]
        self.assertIsNone(control.permission_check.passed)
        self.assertIsNone(control.permission_check.actual_role)
        self.assertEqual(control.dispatch_status, "not_dispatched")
        self.assertFalse(control.dispatched_actions)
        self.assertTrue(all(n.status == "pending" and n.sent_at is None for n in control.notification_results))
        self.assertIn("未发送", control.summary)

    def test_missing_corpus_cannot_fall_back_to_simulated_law(self):
        from tempfile import TemporaryDirectory
        from app.services.verified_knowledge import retrieve_verified_knowledge
        with TemporaryDirectory() as directory:
            data = retrieve_verified_knowledge(["微震"], root=Path(directory))
        self.assertFalse(data.regulations)
        self.assertFalse(data.similar_cases)
        self.assertTrue(data.missing_topics)

    def test_changed_regulation_requires_reverification(self):
        from tempfile import TemporaryDirectory
        from app.services.verified_knowledge import retrieve_verified_knowledge, REGULATION_FILE
        with TemporaryDirectory() as directory:
            path = Path(directory) / REGULATION_FILE
            path.parent.mkdir(parents=True)
            path.write_text("第三百四十二条 虚假替换内容", encoding="utf-8")
            data = retrieve_verified_knowledge(["微震"], root=Path(directory))
        self.assertFalse(data.regulations)
        self.assertTrue(any("版本已变化" in item for item in data.missing_topics))

    def test_regulation_quote_is_an_exact_substring_of_source(self):
        from app.services.verified_knowledge import retrieve_verified_knowledge, REGULATION_FILE, ROOT
        text = (ROOT / REGULATION_FILE).read_text(encoding="utf-8")
        data = retrieve_verified_knowledge(["微震"])
        for reference in data.regulations:
            self.assertIn(reference["requirement"], text)
        self.assertIn("根据现场实际考察资料和积累的数据", data.regulations[0]["requirement"])

    def test_recording_approval_does_not_authenticate_or_dispatch(self):
        import asyncio
        from app.api.workflow_runner import WorkflowRunner
        from tests.test_custom_monitoring import RED

        async def run():
            runner = WorkflowRunner()
            record = await runner.start_async(scenario_id="custom", query=RED)
            try:
                async with asyncio.timeout(30):
                    while record.status == "running":
                        await asyncio.sleep(.05)
                self.assertEqual(record.status, "waiting_human", record.error)
                await runner.approve(record.run_id, approved_by="测试意见", note="仅验证记录，未授权现场动作")
                await record.task
                control = record.final_state["control_result"]
                self.assertIsNone(control.permission_check.passed)
                self.assertEqual(control.dispatch_status, "not_dispatched")
                self.assertFalse(control.dispatched_actions)
                self.assertEqual(record.status, "completed")
                self.assertFalse(any(e["type"] == "execution_feedback" for e in record.events))
            finally:
                if not record.task.done():
                    await runner.cancel(record.run_id)
                    await record.task
        from tests.actual_helpers import configured_summary
        with configured_summary(RED):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
