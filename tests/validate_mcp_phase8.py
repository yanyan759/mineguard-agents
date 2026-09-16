"""阶段 8 MCP 生产化验收脚本。

运行：cd agent1 && python tests/validate_mcp_phase8.py
前置：无外部依赖（handler 确定性实现；变更工具需 MCP_APPROVAL_TOKEN 环境变量）。

7 个用例：
  1. 9 工具注册完整（_HANDLERS ↔ MCP_TOOL_REGISTRY 一一对应）
  2. PermissionGate（只读放行 / 变更无凭据 needs_approval / 配置后放行）
  3. 只读工具 query_sensor_history 返回合法 Output（Pydantic 校验）
  4. 只读工具过滤正确（query_roof_risk_case / query_safety_regulation）
  5. 变更工具 create_warning_record：无审批 → needs_approval；配置 → created
  6. 非法输入 → Pydantic ValidationError（extra=forbid / 校验器）
  7. 全部 9 handler 合法输入 → output_schema Pydantic 校验通过
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

from mcp_server.server import _HANDLERS  # noqa: E402
from mcp_server.permission import gate, requires_approval  # noqa: E402
from app.schemas.mcp_tools import MCP_TOOL_REGISTRY  # noqa: E402
from app.schemas.workflow import RiskLevel  # noqa: E402


def test_registry_complete() -> str:
    assert set(_HANDLERS) == set(MCP_TOOL_REGISTRY), \
        f"handler 与注册表不一致: handler={set(_HANDLERS)} reg={set(MCP_TOOL_REGISTRY)}"
    assert len(_HANDLERS) == 9, f"应 9 工具，实际 {len(_HANDLERS)}"
    return "9 工具 handler ↔ MCP_TOOL_REGISTRY 一一对应"


def test_permission_gate() -> str:
    saved = os.environ.get("MCP_APPROVAL_TOKEN")
    try:
        os.environ.pop("MCP_APPROVAL_TOKEN", None)
        assert gate("query_sensor_history") == (True, "ok"), "只读应直接放行"
        assert gate("create_warning_record") == (False, "needs_approval"), "变更无凭据应 needs_approval"
        assert requires_approval("create_disposal_work_order") is True
        os.environ["MCP_APPROVAL_TOKEN"] = "agent1-t"
        assert gate("create_warning_record", provided_token="agent1-t") == (True, "ok")
    finally:
        if saved is None:
            os.environ.pop("MCP_APPROVAL_TOKEN", None)
        else:
            os.environ["MCP_APPROVAL_TOKEN"] = saved
    return "只读放行 / 变更无凭据 needs_approval / 配置后放行"


def test_query_sensor_history() -> str:
    from app.schemas.mcp_tools import QuerySensorHistoryInput, QuerySensorHistoryOutput
    args = QuerySensorHistoryInput(
        mine_id="M1", roadway_id="201",
        sensor_types=["microseismic", "roof_separation"],
        start_time=datetime(2026, 8, 16, 0, 0),
        end_time=datetime(2026, 8, 16, 1, 0),
        limit=50,
    )
    res = _HANDLERS["query_sensor_history"](args)
    assert res["status"] == "success", res
    out = QuerySensorHistoryOutput(**res["data"])  # Pydantic 校验 output_schema
    assert 0 < out.total_count <= 50, out.total_count
    assert all(r.timestamp >= args.start_time for r in out.rows)
    return "sensor_history 合法 Output（total≤limit + 时间窗过滤）"


def test_readonly_filters() -> str:
    from app.schemas.mcp_tools import (
        QueryRoofRiskCaseInput, QueryRoofRiskCaseOutput,
        QuerySafetyRegulationInput, QuerySafetyRegulationOutput,
    )
    case_res = _HANDLERS["query_roof_risk_case"](QueryRoofRiskCaseInput(risk_level=RiskLevel.RED, causal_factors=["微震"], top_k=5))
    assert case_res["status"] == "success"
    case_out = QueryRoofRiskCaseOutput(**case_res["data"])
    assert all(c.risk_level.value == "red" for c in case_out.cases), "应只返回 red 案例"
    assert case_out.total_matched >= 0

    reg_res = _HANDLERS["query_safety_regulation"](QuerySafetyRegulationInput(keywords=["支护"], mandatory_only=True, top_k=10))
    assert reg_res["status"] == "success"
    reg_out = QuerySafetyRegulationOutput(**reg_res["data"])
    assert all(r.mandatory for r in reg_out.clauses), "mandatory_only 应全强制"
    return "roof_risk_case 按等级过滤 + regulation 按强制过滤"


def test_mutation_gated() -> str:
    from app.schemas.mcp_tools import CreateWarningRecordInput
    args = CreateWarningRecordInput(
        warning_id="W-GATED-1", workflow_run_id="run-1", mine_id="M1", roadway_id="201",
        risk_level=RiskLevel.RED, risk_score=0.9, summary="顶板断裂前兆",
    )
    saved = os.environ.get("MCP_APPROVAL_TOKEN")
    try:
        os.environ.pop("MCP_APPROVAL_TOKEN", None)
        res = _HANDLERS["create_warning_record"](args)
        assert res["status"] == "needs_approval", f"无凭据应 needs_approval: {res}"
        os.environ["MCP_APPROVAL_TOKEN"] = "agent1-t"
        res2 = _HANDLERS["create_warning_record"](args)
        assert res2["status"] == "success", f"配置凭据应执行: {res2}"
        assert res2["data"]["status"] == "created"
    finally:
        if saved is None:
            os.environ.pop("MCP_APPROVAL_TOKEN", None)
        else:
            os.environ["MCP_APPROVAL_TOKEN"] = saved
    return "create_warning_record：无凭据 needs_approval / 配置后 created"


def test_all_handlers_valid_output() -> str:
    """用例 7：9 个 handler 全部以合法输入直调，输出经 output_schema Pydantic 校验。"""
    from datetime import timedelta

    from app.schemas.mcp_tools import (
        CreateDisposalWorkOrderInput, CreateDisposalWorkOrderOutput,
        CreateWarningRecordInput, CreateWarningRecordOutput,
        QueryAvailableWorkersInput, QueryAvailableWorkersOutput,
        QueryDisposalResultInput, QueryDisposalResultOutput,
        QueryEmergencyEquipmentInput, QueryEmergencyEquipmentOutput,
        QueryRoofRiskCaseInput, QueryRoofRiskCaseOutput,
        QuerySafetyRegulationInput, QuerySafetyRegulationOutput,
        QuerySensorHistoryInput, QuerySensorHistoryOutput,
        RunDigitalTwinSimulationInput, RunDigitalTwinSimulationOutput,
    )
    t0 = datetime(2026, 8, 16, 0, 0)
    calls = [
        ("query_sensor_history",
         QuerySensorHistoryInput(mine_id="M1", roadway_id="201", sensor_types=["microseismic"],
                                 start_time=t0, end_time=t0 + timedelta(minutes=30), limit=20),
         QuerySensorHistoryOutput),
        ("query_roof_risk_case",
         QueryRoofRiskCaseInput(risk_level=RiskLevel.RED, top_k=3),
         QueryRoofRiskCaseOutput),
        ("query_safety_regulation",
         QuerySafetyRegulationInput(keywords=["支护"], top_k=5),
         QuerySafetyRegulationOutput),
        ("query_available_workers",
         QueryAvailableWorkersInput(mine_id="M1", roles=["支护工"], shift="current"),
         QueryAvailableWorkersOutput),
        ("query_emergency_equipment",
         QueryEmergencyEquipmentInput(mine_id="M1", equipment_types=["support_material"],
                                      status_filter=["available"]),
         QueryEmergencyEquipmentOutput),
        ("run_digital_twin_simulation",
         RunDigitalTwinSimulationInput(mine_id="M1", roadway_id="201",
                                       simulation_type="stress_field", workflow_run_id="r1"),
         RunDigitalTwinSimulationOutput),
        ("query_disposal_result",
         QueryDisposalResultInput(warning_id="W1"),
         QueryDisposalResultOutput),
        ("create_warning_record",
         CreateWarningRecordInput(warning_id="W-ALL-1", workflow_run_id="r1", mine_id="M1",
                                  roadway_id="201", risk_level=RiskLevel.RED, risk_score=0.85,
                                  summary="测试"),
         CreateWarningRecordOutput),
        ("create_disposal_work_order",
         CreateDisposalWorkOrderInput(warning_id="W1", decision_id="D1", plan_id="P1",
                                      actions=["撤人", "加强支护"], deadline_minutes=30,
                                      priority="urgent"),
         CreateDisposalWorkOrderOutput),
    ]
    saved = os.environ.get("MCP_APPROVAL_TOKEN")
    os.environ["MCP_APPROVAL_TOKEN"] = "agent1-t"  # 变更工具需凭据
    try:
        for tool_id, inp, out_model in calls:
            res = _HANDLERS[tool_id](inp)
            assert res["status"] == "success", f"{tool_id} 应成功: {res}"
            out_model(**res["data"])  # Pydantic 校验 output_schema
    finally:
        if saved is None:
            os.environ.pop("MCP_APPROVAL_TOKEN", None)
        else:
            os.environ["MCP_APPROVAL_TOKEN"] = saved
    return "9/9 handler 合法输入 → output_schema Pydantic 校验通过"


def test_invalid_input() -> str:
    from pydantic import ValidationError
    from app.schemas.mcp_tools import QuerySensorHistoryInput
    try:
        QuerySensorHistoryInput(
            mine_id="M1", roadway_id="201", sensor_types=["microseismic"],
            start_time=datetime(2026, 8, 16, 2, 0),
            end_time=datetime(2026, 8, 16, 1, 0),  # end <= start
            limit=50,
        )
        raise AssertionError("end_time <= start_time 应校验失败")
    except ValidationError:
        pass
    # extra=forbid
    try:
        QuerySensorHistoryInput(
            mine_id="M1", roadway_id="201", sensor_types=["microseismic"],
            start_time=datetime(2026, 8, 16, 0, 0), end_time=datetime(2026, 8, 16, 1, 0),
            limit=50, extra_field="x",
        )
        raise AssertionError("extra 字段应拒绝")
    except ValidationError:
        pass
    return "end_after_start 校验器 + extra=forbid"


def main() -> int:
    print("阶段 8 MCP 生产化验收")
    print("=" * 60)
    cases = [
        ("1. 9 工具注册完整", test_registry_complete),
        ("2. PermissionGate", test_permission_gate),
        ("3. sensor_history 只读", test_query_sensor_history),
        ("4. 只读过滤正确", test_readonly_filters),
        ("5. 变更工具门控", test_mutation_gated),
        ("6. 非法输入拒绝", test_invalid_input),
        ("7. 全部 9 handler 合法输出", test_all_handlers_valid_output),
    ]
    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"  [PASS] {name}  —  {detail}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {name}  —  {type(exc).__name__}: {str(exc)[:200]}")
    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
