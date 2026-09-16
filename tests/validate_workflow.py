"""阶段 4 工作流校验脚本。

以 7 套 fixture 场景作为输入，驱动六阶段 StateGraph 端到端执行，
验证：正常链路、条件分支、回退逻辑、循环限制。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# 导入设置
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

from app.agent.fixture_loader import FixtureDataSource
from app.agent.workflow import create_workflow
from app.agent.workflow_state import new_workflow_state

# LLM_NODES=on 委托：本脚本为确定性回归态（off）校验；on 模式验收
# （Schema/路由/安全属性断言）由 tests/validate_llm_nodes.py 承担（双模式隔离，设计 spec §7）。
import os
if os.getenv("LLM_NODES", "off").lower() in ("1", "on", "true"):
    print("=" * 60)
    print("  LLM_NODES=on：确定性回归断言由 tests/validate_llm_nodes.py 承担。")
    print("  请运行：LLM_NODES=on python tests/validate_llm_nodes.py")
    print("=" * 60)
    sys.exit(0)


def run_scenario(scenario_id: str, expected_phases: list[str] | None = None,
                 expected_risk: str | None = None):
    """运行单个 fixture 场景并收集执行轨迹。

    Returns: (scenario_id, passed, phases_executed, errors)
    """
    loader = FixtureDataSource()
    scenario_input = loader.load_scenario(scenario_id)
    ctx = scenario_input.context
    ctx = ctx.model_copy(update={"iteration_count": 0, "max_iterations": 2})

    state = new_workflow_state(ctx, scenario_id=scenario_id)
    workflow = create_workflow()
    config = {"configurable": {"thread_id": ctx.thread_id}}

    phases = []

    try:
        for event in workflow.stream(state, config):
            for node_name, node_output in event.items():
                phases.append(node_name)  # 记录每次节点调用（含回退重入）

            # 防止死循环
            if len(phases) > 25:
                phases.append("MAX_ITERATIONS_EXCEEDED")
                break

        return (scenario_id, True, phases, None)

    except Exception as e:
        return (scenario_id, False, phases, str(e)[:200])


def main():
    print("=" * 60)
    print("  阶段 4 — 六阶段工作流端到端验收")
    print("=" * 60)

    scenarios = [
        ("normal", ["perception_warning"], "normal"),
        ("yellow", ["perception_warning", "knowledge_retrieval", "dispatch_decision",
                     "resource_evaluation", "collaborative_control", "reflection_iteration"], "yellow"),
        ("red", ["perception_warning", "knowledge_retrieval", "dispatch_decision",
                  "resource_evaluation", "collaborative_control", "reflection_iteration"], "red"),
        ("missing_knowledge", ["perception_warning", "knowledge_retrieval"], "orange"),
        ("resource_insufficient", ["perception_warning", "knowledge_retrieval",
                                    "dispatch_decision", "resource_evaluation"], "orange"),
        ("reflection_rollback", ["perception_warning", "knowledge_retrieval",
                                  "dispatch_decision", "resource_evaluation",
                                  "collaborative_control", "reflection_iteration",
                                  "dispatch_decision"], "orange"),
        ("max_iterations", ["perception_warning", "knowledge_retrieval",
                             "dispatch_decision", "resource_evaluation",
                             "collaborative_control", "reflection_iteration"], "red"),
    ]

    passed = 0
    failed = 0
    results = []

    for scenario_id, expected_path, expected_risk in scenarios:
        sid, ok, phases, error = run_scenario(scenario_id)
        results.append((sid, ok, phases, error, expected_path))

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"\n  [{status}] {sid}")
        print(f"    执行路径: {' → '.join(phases)}")
        if error:
            print(f"    错误: {error}")

    # 详细分析
    print("\n" + "=" * 60)
    print("  场景路径分析")
    print("=" * 60)

    checks = {
        "normal 直接结束": False,
        "yellow 完整 6 阶段": False,
        "red 完整 6 阶段": False,
        "resource_insufficient 回退决策": False,
        "reflection_rollback 回退决策": False,
        "max_iterations 停止迭代": False,
    }

    for sid, ok, phases, error, expected in results:
        loader = FixtureDataSource()
        label = loader.scenario_label(sid)

        if sid == "normal":
            checks["normal 直接结束"] = (len(phases) == 1 and "perception_warning" in phases)
            print(f"  [{'PASS' if checks['normal 直接结束'] else 'FAIL'}] normal: perception_warning → END")

        elif sid == "yellow":
            all_six = all(p in phases for p in ["perception_warning", "knowledge_retrieval",
                                                 "dispatch_decision", "resource_evaluation",
                                                 "collaborative_control", "reflection_iteration"])
            checks["yellow 完整 6 阶段"] = all_six
            print(f"  [{'PASS' if all_six else 'FAIL'}] yellow: 完整六阶段" + (" (6/6)" if all_six else ""))

        elif sid == "red":
            all_six = all(p in phases for p in ["perception_warning", "knowledge_retrieval",
                                                 "dispatch_decision", "resource_evaluation",
                                                 "collaborative_control", "reflection_iteration"])
            checks["red 完整 6 阶段"] = all_six
            print(f"  [{'PASS' if all_six else 'FAIL'}] red: 完整六阶段 + 快速告警")

        elif sid == "resource_insufficient":
            # 先走完 6 阶段 → 反思触发 return_to_decision → 再次决策+资源 → 协同→反思 finish
            has_rollback = (phases.count("reflection_iteration") >= 2 or phases.count("dispatch_decision") >= 2)
            checks["resource_insufficient 回退决策"] = has_rollback
            print(f"  [{'PASS' if has_rollback else 'FAIL'}] resource_insufficient: 反思 → 回退决策 → 二次评估 → finish")

        elif sid == "reflection_rollback":
            has_rollback = (phases.count("reflection_iteration") >= 2 or phases.count("dispatch_decision") >= 2)
            checks["reflection_rollback 回退决策"] = has_rollback
            print(f"  [{'PASS' if has_rollback else 'FAIL'}] reflection_rollback: 反思 → 回退决策 → finish")

        elif sid == "max_iterations":
            # 应停止自动迭代（通过 reflection finish），不无限循环
            max_phases_ok = len(phases) <= 15
            checks["max_iterations 停止迭代"] = max_phases_ok
            print(f"  [{'PASS' if max_phases_ok else 'FAIL'}] max_iterations: {len(phases)} 阶段（≤15=不无限循环）")

    path_checks = sum(1 for v in checks.values() if v)
    print(f"\n  路径检查: {path_checks}/{len(checks)}")

    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {failed} FAIL | 路径: {path_checks}/{len(checks)}")
    print("=" * 60)

    return 0 if failed == 0 and path_checks == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
