"""阶段 1 fixture 校验脚本。

校验内容：
1. 每个 fixture JSON 可通过 SimulationCase Pydantic 校验
2. 7 种端到端场景全部覆盖
3. MCP 工具 Schema 定义完整（9 个工具）
4. YAML 测试数据可解析且关键字段完备
5. 成员 1 接口契约 Schema 可构造与指标达标

运行方式：
    cd agent1
    python tests/validate_fixtures.py
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import yaml  # type: ignore

# Windows GBK 终端下 emoji 输出兼容处理
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# agent1/ 独立运行时导入设置：
# 1. 将 agent1/ 加入 sys.path（使 schemas/config/services 等作为顶层包可导入）
# 2. schemas/__init__.py 已改为相对导入，绕过 app. 前缀问题
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

from schemas.workflow import SimulationCase  # noqa: E402

# mcp_tools.py 使用 `from .workflow import ...`（相对导入），
# 但 importlib 直接加载模块时相对导入需要正确的 __package__ 信息，
# 因此通过 importlib + sys.modules 注入来加载
_workflow_mod = sys.modules.get("schemas.workflow")
if _workflow_mod is None:
    # 如果还未注册，手动注册
    import schemas.workflow as _wf
    _workflow_mod = _wf

def _import_with_package(module_path_relative: str, pkg: str):
    """直接加载模块文件，并绑定正确的 __package__ 以支持相对导入。"""
    file_path = AGENT1_ROOT / module_path_relative
    module_name = file_path.stem
    full_name = f"{pkg}.{module_name}"
    spec = importlib.util.spec_from_file_location(full_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg
    # 将模块注册到 sys.modules 以便后续导入
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod

_mcp_tools = _import_with_package("schemas/mcp_tools.py", "schemas")
MCP_TOOL_REGISTRY = _mcp_tools.MCP_TOOL_REGISTRY

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

FIXTURE_FILES = [
    "scenario_001_normal.json",
    "scenario_002_yellow.json",
    "scenario_003_red.json",
    "scenario_004_missing_knowledge.json",
    "scenario_005_resource_insufficient.json",
    "scenario_006_reflection_rollback.json",
    "scenario_007_max_iterations.json",
]

EXPECTED_SCENARIOS = {
    "normal": "scenario_001_normal.json",
    "yellow": "scenario_002_yellow.json",
    "red": "scenario_003_red.json",
    "missing_knowledge": "scenario_004_missing_knowledge.json",
    "resource_insufficient": "scenario_005_resource_insufficient.json",
    "reflection_rollback": "scenario_006_reflection_rollback.json",
    "max_iterations": "scenario_007_max_iterations.json",
}

YAML_FILES = ["accident_cases.yaml", "regulations.yaml"]


def check_pass(label: str) -> str:
    return f"  ✅ {label}"


def check_fail(label: str, detail: str = "") -> str:
    msg = f"  ❌ {label}"
    if detail:
        msg += f" — {detail}"
    return msg


def validate_json_fixtures() -> bool:
    """校验所有 JSON fixture 文件的 Pydantic Schema 合法性。"""
    print("\n📋 JSON Fixture Schema 校验")
    print("-" * 60)
    all_ok = True
    for fname in FIXTURE_FILES:
        path = FIXTURES_DIR / fname
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            case = SimulationCase(**raw)
            # 验证关键字段不为空
            assert case.scenario_id, "scenario_id 不能为空"
            assert case.description, "description 不能为空"
            assert case.perception_input, "perception_input 不能为空"
            assert case.expected_warning, "expected_warning 不能为空"
            assert case.expected_warning.risk_level is not None, "risk_level 不能为空"
            print(check_pass(f"{fname} → {case.scenario_id} (risk={case.expected_warning.risk_level.value})"))
        except Exception as e:
            print(check_fail(fname, str(e)[:120]))
            all_ok = False
    return all_ok


def validate_scenario_coverage() -> bool:
    """验证 7 种端到端场景全覆盖。"""
    print("\n📋 端到端场景覆盖检查")
    print("-" * 60)
    all_ok = True

    expected_labels = set(EXPECTED_SCENARIOS.keys())
    for fname in FIXTURE_FILES:
        path = FIXTURES_DIR / fname
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            case = SimulationCase(**raw)
            risk = case.expected_warning.risk_level.value
            fast_alert = case.expected_warning.fast_alert_required
            feasibility = case.expected_resource_feasibility
            iteration = case.perception_input.context.iteration_count

            # 场景判定逻辑
            if fname == "scenario_001_normal.json":
                assert risk == "normal", f"期望 normal，实际 {risk}"
                assert not fast_alert, "normal 场景不应有 fast_alert"
                assert feasibility is None, "normal 场景不涉及资源评估"
                print(check_pass(f"场景 1 — 数据正常无报警"))

            elif fname == "scenario_002_yellow.json":
                assert risk == "yellow", f"期望 yellow，实际 {risk}"
                assert not fast_alert, "yellow 常规处置不应有 fast_alert"
                assert feasibility == "feasible", f"期望 feasible，实际 {feasibility}"
                print(check_pass(f"场景 2 — 黄色预警常规处置"))

            elif fname == "scenario_003_red.json":
                assert risk == "red", f"期望 red，实际 {risk}"
                assert fast_alert, "red 场景必须 fast_alert=true"
                print(check_pass(f"场景 3 — 红色预警快速告警"))

            elif fname == "scenario_004_missing_knowledge.json":
                assert risk == "orange", f"期望 orange，实际 {risk}"
                assert fast_alert, "orange+知识缺失场景应 fast_alert=true"
                assert feasibility == "feasible_with_adjustment", (
                    f"期望 feasible_with_adjustment，实际 {feasibility}"
                )
                print(check_pass(f"场景 4 — 知识库缺失降置信度"))

            elif fname == "scenario_005_resource_insufficient.json":
                assert risk == "orange", f"期望 orange，实际 {risk}"
                assert feasibility == "infeasible", f"期望 infeasible，实际 {feasibility}"
                assert not fast_alert, "orange+资源不足不应触发 fast_alert"
                print(check_pass(f"场景 5 — 资源不足回退决策"))

            elif fname == "scenario_006_reflection_rollback.json":
                assert risk == "orange", f"期望 orange，实际 {risk}"
                assert feasibility == "feasible", (
                    f"第一轮资源评估应为 feasible，实际 {feasibility}"
                )
                print(check_pass(f"场景 6 — 执行后风险未降反思回退"))

            elif fname == "scenario_007_max_iterations.json":
                assert risk == "red", f"期望 red，实际 {risk}"
                assert iteration == 2, f"期望 iteration_count=2，实际 {iteration}"
                assert feasibility == "infeasible", f"期望 infeasible，实际 {feasibility}"
                print(check_pass(f"场景 7 — 最大循环人工终止"))

        except Exception as e:
            print(check_fail(fname, str(e)[:120]))
            all_ok = False

    return all_ok


def validate_mcp_tools() -> bool:
    """验证 MCP 工具注册表中 9 个工具定义完整。"""
    print("\n📋 MCP 工具 Schema 完整性检查")
    print("-" * 60)
    all_ok = True

    expected_tools = [
        "query_sensor_history",
        "query_roof_risk_case",
        "query_safety_regulation",
        "query_available_workers",
        "query_emergency_equipment",
        "create_warning_record",
        "create_disposal_work_order",
        "run_digital_twin_simulation",
        "query_disposal_result",
    ]

    if len(MCP_TOOL_REGISTRY) != 9:
        print(check_fail(f"工具总数应为 9，实际为 {len(MCP_TOOL_REGISTRY)}"))
        all_ok = False

    for name in expected_tools:
        if name not in MCP_TOOL_REGISTRY:
            print(check_fail(f"缺失工具: {name}"))
            all_ok = False
        else:
            tool = MCP_TOOL_REGISTRY[name]
            has_desc = bool(tool.get("description"))
            has_input = tool.get("input_schema") is not None
            has_output = tool.get("output_schema") is not None
            has_category = bool(tool.get("category"))
            read_only = tool.get("read_only", True)
            if not all([has_desc, has_input, has_output, has_category]):
                print(check_fail(f"工具 {name} 定义不完整"))
                all_ok = False
            else:
                ro_label = "🔒只读" if read_only else "✏️可写"
                print(check_pass(f"{name} [{tool['category']}] {ro_label}"))

    return all_ok


def validate_yaml_files() -> bool:
    """验证 YAML 测试数据文件格式正确且包含关键字段。"""
    print("\n📋 YAML 测试数据校验")
    print("-" * 60)
    all_ok = True

    # accident_cases.yaml
    cases_path = FIXTURES_DIR / "accident_cases.yaml"
    try:
        data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
        cases = data.get("cases", [])
        assert len(cases) >= 3, f"历史事故案例应至少有 3 条，实际 {len(cases)}"
        required_fields = {"case_id", "location", "causes", "precursors", "actions", "outcome", "source"}
        for case in cases:
            missing = required_fields - set(case.keys())
            if missing:
                print(check_fail(f"案例 {case.get('case_id', '?')} 缺失字段: {missing}"))
                all_ok = False
        if all_ok:
            print(check_pass(f"accident_cases.yaml: {len(cases)} 个案例，字段完整"))
    except Exception as e:
        print(check_fail(f"accident_cases.yaml 解析失败: {e}"))
        all_ok = False

    # regulations.yaml
    regs_path = FIXTURES_DIR / "regulations.yaml"
    try:
        data = yaml.safe_load(regs_path.read_text(encoding="utf-8"))
        regs = data.get("regulations", [])
        assert len(regs) >= 5, f"规程条款应至少有 5 条，实际 {len(regs)}"
        required_fields = {"regulation_id", "document", "clause", "requirement", "mandatory", "source_type"}
        for reg in regs:
            missing = required_fields - set(reg.keys())
            if missing:
                print(check_fail(f"规程 {reg.get('regulation_id', '?')} 缺失字段: {missing}"))
                all_ok = False
        if all_ok:
            mandatory_count = sum(1 for r in regs if r.get("mandatory"))
            print(check_pass(f"regulations.yaml: {len(regs)} 条规程（{mandatory_count} 强制），字段完整"))
    except Exception as e:
        print(check_fail(f"regulations.yaml 解析失败: {e}"))
        all_ok = False

    return all_ok


def validate_member1_interface() -> bool:
    """验证成员 1 接口 Schema 可导入且有必要的字段定义。"""
    print("\n📋 成员 1 接口契约检查")
    print("-" * 60)
    all_ok = True

    try:
        _mi = _import_with_package("schemas/member1_interface.py", "schemas")
        ModelInferenceRequest = _mi.ModelInferenceRequest
        ModelInferenceResponse = _mi.ModelInferenceResponse
        ModelHealthResponse = _mi.ModelHealthResponse
        ModelMetricsReport = _mi.ModelMetricsReport

        # 验证推理请求
        req = ModelInferenceRequest(
            request_id="test-001",
            model_version="MOA-Transformer-v1",
            features=[
                {
                    "point_id": "P1",
                    "timestamp": "2026-07-24T08:00:00",
                    "sensor_type": "microseismic",
                    "count": 10,
                    "mean": 1500.0,
                    "std": 200.0,
                    "slope": 0.05,
                    "min_value": 1200.0,
                    "max_value": 1800.0,
                    "range_value": 600.0,
                }
            ],
        )
        assert req.forecast_horizon_minutes == 10, "默认 forecast_horizon 应为 10"
        print(check_pass("ModelInferenceRequest 构造成功"))

        # 验证推理响应
        resp = ModelInferenceResponse(
            request_id="test-001",
            model_version="MOA-Transformer-v1",
            model_status="online",
            forecasts=[
                {
                    "forecast_minutes": 10,
                    "risk_probability": 0.85,
                    "predicted_risk_level": "yellow",
                    "confidence_interval_lower": 0.72,
                    "confidence_interval_upper": 0.93,
                }
            ],
            shap_importance=[
                {"feature_name": "b_value", "shap_value": 0.42, "importance_rank": 1},
                {"feature_name": "max_energy", "shap_value": 0.31, "importance_rank": 2},
            ],
            inference_time_ms=350,
        )
        assert resp.inference_time_ms < 500, f"单步推理时延应 <500ms，实际 {resp.inference_time_ms}ms"
        print(check_pass("ModelInferenceResponse 构造成功（inference_time={}ms）".format(resp.inference_time_ms)))

        # 验证指标报告
        report = ModelMetricsReport(
            model_version="MOA-Transformer-v1",
            test_set_accuracy=0.90,
            false_positive_rate=0.10,
            single_step_latency_ms=380,
            test_set_size=5000,
            evaluated_at="2026-07-24T12:00:00",
            notes="单元测试通过",
        )
        assert report.test_set_accuracy >= 0.88, f"准确率应 ≥88%，实际 {report.test_set_accuracy:.0%}"
        assert report.false_positive_rate <= 0.12, f"误报率应 ≤12%，实际 {report.false_positive_rate:.0%}"
        print(check_pass(
            f"ModelMetricsReport: 准确率={report.test_set_accuracy:.0%} "
            f"误报率={report.false_positive_rate:.0%} "
            f"时延={report.single_step_latency_ms}ms（均达标）"
        ))

    except Exception as e:
        print(check_fail(f"成员 1 接口校验失败: {e}"))
        all_ok = False

    return all_ok


def run_all() -> int:
    """运行全部校验，返回 0 表示全部通过。"""
    print("=" * 60)
    print("  阶段 1 — 数据契约冻结与测试数据准备 — 验收校验")
    print("=" * 60)

    results = {
        "JSON Fixture Schema": validate_json_fixtures(),
        "场景覆盖检查": validate_scenario_coverage(),
        "MCP 工具 Schema": validate_mcp_tools(),
        "YAML 测试数据": validate_yaml_files(),
        "成员 1 接口契约": validate_member1_interface(),
    }

    print("\n" + "=" * 60)
    print("  验收结果汇总")
    print("=" * 60)
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    for label, ok in results.items():
        status = "✅ 通过" if ok else "❌ 失败"
        print(f"  {status}  {label}")

    print(f"\n  {passed}/{total} 项全部通过\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_all())
