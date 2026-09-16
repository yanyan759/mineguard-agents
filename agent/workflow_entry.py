"""六 Agent 工作流的同步入口。

浏览器和外部调用通过 ``POST /api/workflow/start`` 启动异步任务；本模块提供
命令行与测试使用的同步入口。两条路径都执行同一套 LangGraph StateGraph。
"""

from __future__ import annotations


def run_workflow(scenario_id: str = "yellow"):
    """加载指定演示场景并同步执行六阶段工作流。"""
    from app.agent.fixture_loader import FixtureDataSource
    from app.agent.workflow import create_workflow
    from app.agent.workflow_state import new_workflow_state

    loader = FixtureDataSource()
    scenario_input = loader.load_scenario(scenario_id)
    context = scenario_input.context.model_copy(
        update={"iteration_count": 0, "max_iterations": 2}
    )

    state = new_workflow_state(context, scenario_id=scenario_id)
    workflow = create_workflow()
    config = {"configurable": {"thread_id": context.thread_id}}

    print(f"[Workflow] 启动场景: {scenario_id} - {loader.scenario_label(scenario_id)}")
    for event in workflow.stream(state, config):
        for node_name, node_output in event.items():
            messages = node_output.get("messages", [])
            if messages and isinstance(messages, list) and messages[-1].get("content"):
                print(f"  [{node_name}] {messages[-1]['content']}")

    final = workflow.get_state(config)
    result = final.values.get("reflection_result") if final.values else None
    if result:
        print(f"[Workflow] 完成: {result.summary}")
    return final


if __name__ == "__main__":
    run_workflow("yellow")
