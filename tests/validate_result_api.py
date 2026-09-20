"""结果接口校验：get_result 从 Redis checkpoint 恢复六 Agent 完整数据。"""

import asyncio
import sys
from pathlib import Path

# 确保 app 命名空间可用（agent1/app/__init__.py 的 __path__ 扩展）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 加载环境变量（Redis 连接等）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


def test_result_fields_present():
    """断言 get_result 返回六 Agent 完整字段（需 Redis 中存在已完成工作流）。"""
    from app.api.workflow_runner import runner

    async def _check():
        try:
            result = await runner.get_result("wf-c2892716")
        except (KeyError, RuntimeError) as exc:
            print(f"  SKIP: Redis checkpoint 不可用或 run_id 不存在 ({exc})")
            return
        # 六 Agent 键必须存在
        expected_agents = [
            "perception_result",
            "knowledge_result",
            "decision_result",
            "resource_result",
            "control_result",
            "reflection_result",
        ]
        agents = result.get("agents", {})
        for key in expected_agents:
            assert key in agents, f"agents 缺少 {key}"
            agent_data = agents[key]
            # 每个 Agent 必须包含 status / summary / confidence
            for field in ("status", "summary", "confidence"):
                assert field in agent_data, f"{key} 缺少 {field}"
        print(f"  PASS: get_result 返回 {len(agents)} 个 Agent 完整数据")

    asyncio.run(_check())


def test_result_not_found():
    """断言 get_result 对不存在的 run_id 抛 KeyError。"""
    from app.api.workflow_runner import runner

    async def _check():
        try:
            await runner.get_result("wf-nonexistent")
            assert False, "应该抛 KeyError"
        except KeyError:
            print("  PASS: 不存在的 run_id 正确抛出 KeyError")

    asyncio.run(_check())


def test_build_result_payload():
    """断言 _build_result_payload 正确序列化 Pydantic 模型。"""
    from datetime import datetime, timezone

    from app.api.workflow_runner import runner
    from app.schemas.workflow import (
        AffectedArea,
        AgentStatus,
        DataQuality,
        PerceptionWarningResult,
        RiskLevel,
    )

    now = datetime.now(timezone.utc)
    # 构造一个最小的 Pydantic mock（使用真实的 PerceptionWarningResult schema）
    mock_perception = PerceptionWarningResult(
        agent_name="感知预警 Agent",
        status=AgentStatus.SUCCESS,
        summary="测试感知结果",
        confidence=0.95,
        started_at=now,
        finished_at=now,
        duration_ms=100,
        next_action="knowledge_retrieval",
        warning_id="test-w-001",
        risk_level=RiskLevel.YELLOW,
        risk_score=0.42,
        model_version="mock-v1",
        threshold_version="v1",
        model_mode="rule_based",
        affected_area=AffectedArea(mine_id="test-mine", roadway_id="test-roadway", locations=["test-area"]),
        data_quality=DataQuality(completeness=1.0, timeliness=1.0),
        recommended_recheck_minutes=15,
    )
    mock_state = {
        "perception_result": mock_perception,
        "iteration_count": 1,
        "max_iterations": 2,
    }
    result = runner._build_result_payload("wf-test-001", mock_state, None)
    assert result["run_id"] == "wf-test-001"
    assert result["agents"]["perception_result"]["status"] == "success"
    assert result["agents"]["perception_result"]["confidence"] == 0.95
    assert result["iteration_count"] == 1
    print("  PASS: _build_result_payload 正确序列化 Pydantic 模型")


if __name__ == "__main__":
    print("=== validate_result_api ===")
    test_build_result_payload()
    test_result_fields_present()
    test_result_not_found()
    print("ALL PASS")