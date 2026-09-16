"""六 Agent 可选 LLM 增强子图工厂。

每个 Agent 一个 LLM 子图：
  react_agent（create_react_agent 工具调用循环，挂载 MCP 白名单工具）
    → extract_result（llm_service.llm_call_structured json_mode 结构化提取 result_schema）

子图输入 task_payload 字符串（由 subgraph_input 从 WorkflowState 打包），
输出 {result: result_schema, model_mode: "llm"}。提取失败抛 LLMError，
由上层节点兜底回确定性并标 model_mode="deterministic_fallback"。

关键设计决策（兼容性实测 2026-08-16）：
- langgraph 1.2.10 中 create_agent 已更名 create_react_agent，且无
  result_structured_outputs 参数；DeepSeek 仅支持 json_object response_format，
  故结构化输出不走 create_react_agent 的 response_format，改由 extract 节点复用
  llm_service.llm_call_structured（json_mode）完成 Pydantic 校验与重试。
- 节点层保持同步函数（tests/validate_neo4j_phase8.py 直接同步调用 node_*），
  LLM 子图经 run_llm_subgraph 提交到本模块的常驻事件循环线程执行。MCP 连接与
  react_agent 均与事件循环绑定，若每次调用新建 loop 会造成 MCP 会话跨 loop 复用
  失败（tool-calling 必炸）；常驻单循环可保证工具面与推理全链路同 loop 存活。
- MCP server 不可达时 get_agent_tools 返回 []，子图退化为纯推理循环，仍可产出
  LLM 增强结果；安全红线：任何异常都不阻断处置，由节点兜底确定性。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import threading
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

from app.agent.llm import model
from app.agent.llm_service import LLMError, llm_call_structured
from app.agent.prompts import sub_agents_content
from app.agent.workflow_state import WorkflowState
from app.mcp_server.client_bridge import get_agent_tools

# 参与 LLM 子图增强的节点（感知预警保持规则定级，解释层单独处理，不在此列）
SUPPORTED_LLM_NODES = frozenset({
    "knowledge_retrieval",
    "dispatch_decision",
    "resource_evaluation",
    "collaborative_control",
    "reflection_iteration",
})

# 子图缓存：同一 Agent 只构建一次（含 MCP 工具拉取），后续复用
_SUBGRAPH_CACHE: dict[str, Any] = {}

# extract 节点系统提示词：把 Agent 推理内容转换为指定 JSON 结构
_EXTRACT_SYSTEM_PROMPT = (
    "你是煤矿顶板灾变处置系统的结构化结果转换器。"
    "根据输入的 Agent 推理内容，提取关键结论并输出为指定 JSON 结构。"
    "严格遵守字段声明：字段声明为数组（字符串数组/对象数组）的必须输出 JSON 数组，"
    "绝不能把数组输出成单个字符串；枚举字段只能取声明列出的值；必填字段不得为 null；"
    "datetime 输出 ISO-8601 字符串。只输出 JSON，不要解释。"
)


def is_llm_node_enabled(agent_key: str) -> bool:
    """LLM_NODES 开关：on/1/true 启用该节点 LLM 增强，否则走确定性逻辑。"""
    return os.getenv("LLM_NODES", "off").lower() in ("1", "on", "true")


class AgentSubgraphState(TypedDict, total=False):
    """LLM 子图内部状态。"""

    messages: Annotated[list, add_messages]  # react_agent 的推理/工具调用消息流
    task_payload: str                        # 输入：打包好的上下文 JSON 字符串
    result: Any                              # 输出：result_schema 实例
    model_mode: str                          # "llm"


# ═══════════════════════════════════════════════════════════════════════════
# 提示词适配
# ═══════════════════════════════════════════════════════════════════════════


def _tool_lines(tool_names: list[str]) -> str:
    """工具清单文案（空则提示未挂载，子图退化为纯推理）。"""
    if not tool_names:
        return "- （MCP 工具未挂载，基于给定上下文直接输出结论）"
    return "\n".join(f"- {name}" for name in tool_names)


def _adapt_prompt(agent_key: str, tool_names: list[str]) -> str:
    """复用 prompts.yml 该 Agent system_prompt，把【你的工具】段替换为 MCP 白名单。

    system_prompt 为 YAML block scalar 字符串，段以【…】标题分隔。定位【你的工具】
    标题后，将正文替换为工具清单，其余段（职责/领域知识/输出要求/安全约束）原样保留。
    """
    sys_prompt = sub_agents_content[agent_key]["system_prompt"]
    header = "【你的工具】"
    idx = sys_prompt.find(header)
    if idx < 0:
        # 无工具段：追加一段工具说明
        return sys_prompt + f"\n{header}\n{_tool_lines(tool_names)}"
    content_start = idx + len(header)
    # 正文终点：下一个【…】标题（作为单独行）或文本末尾
    m = re.search(r"\n【[^】\n]*】", sys_prompt[content_start:])
    content_end = content_start + m.start() if m else len(sys_prompt)
    return (
        sys_prompt[:content_start]
        + f"\n{_tool_lines(tool_names)}"
        + sys_prompt[content_end:]
    )


# ═══════════════════════════════════════════════════════════════════════════
# 子图节点
# ═══════════════════════════════════════════════════════════════════════════


def _last_text_content(messages: list) -> str:
    """取消息流最后一段非空文本（Agent 的最终推理结论）。"""
    for msg in reversed(messages or []):
        content = getattr(msg, "content", None)
        if not content:
            continue
        if isinstance(content, str):
            if content.strip():
                return content
        elif isinstance(content, list):
            # content 可能是多模态片段列表，仅取 text 片段
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "".join(parts).strip()
            if text:
                return text
    return ""


def _make_react_node(agent: Any) -> Any:
    """react_agent 节点：把 task_payload 作为 human 消息喂给 create_react_agent。"""

    async def react_node(state: AgentSubgraphState) -> dict:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=state["task_payload"])]}
        )
        return {"messages": result["messages"]}

    return react_node


def _make_extract_node(result_schema: type) -> Any:
    """extract 节点：把 react_agent 最终文本用 json_mode 结构化到 result_schema。

    提取失败抛 LLMError，由上层节点兜底回确定性（绝不产出非法 schema 结果）。
    """

    async def extract_node(state: AgentSubgraphState) -> dict:
        content = _last_text_content(state.get("messages", []))
        if not content:
            raise LLMError(f"{result_schema.__name__}: Agent 未产出最终文本")
        # max_retries=2：重试会把 pydantic 校验错误（具体字段）附加进提示词引导
        # 自纠，可显著降低大 schema 一次性输出失配的概率（如数组字段被输出成字符串）
        res = llm_call_structured(
            result_schema, _EXTRACT_SYSTEM_PROMPT, content, max_retries=2
        )
        if res["status"] != "ok":
            raise LLMError(res.get("error", f"{result_schema.__name__} 结构化提取失败"))
        return {"result": res["data"], "model_mode": "llm"}

    return extract_node


def _build_agent_subgraph_sync(agent_key: str, result_schema: type, tools: list) -> Any:
    """同步构建子图（供测试免拉起 MCP 子进程；构建不触发任何网络调用）。"""
    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=_adapt_prompt(agent_key, [getattr(t, "name", "") for t in tools]),
        name=f"{agent_key}_llm_agent",
    )
    builder = StateGraph(AgentSubgraphState)
    builder.add_node("react_agent", _make_react_node(agent))
    builder.add_node("extract_result", _make_extract_node(result_schema))
    builder.add_edge(START, "react_agent")
    builder.add_edge("react_agent", "extract_result")
    builder.add_edge("extract_result", END)
    return builder.compile()


async def build_agent_subgraph(agent_key: str, result_schema: type) -> Any:
    """构建（并缓存）某 Agent 的 LLM 子图。

    :param agent_key: Agent key（SUPPORTED_LLM_NODES 之一）
    :param result_schema: 该 Agent 的结果 Pydantic Schema（如 DispatchDecisionResult）
    :return: CompiledStateGraph；await 其 ainvoke({"task_payload": ...}) 得
             {result: result_schema, model_mode: "llm"}。
    MCP server 不可达时工具列表为空，子图退化为纯推理循环。
    """
    cached = _SUBGRAPH_CACHE.get(agent_key)
    if cached is not None:
        return cached
    tools = await get_agent_tools(agent_key)  # async 拉取 MCP 白名单工具
    graph = _build_agent_subgraph_sync(agent_key, result_schema, tools)
    _SUBGRAPH_CACHE[agent_key] = graph
    return graph


# ═══════════════════════════════════════════════════════════════════════════
# WorkflowState → 子图输入 / 常驻循环同步入口
# ═══════════════════════════════════════════════════════════════════════════


def _to_jsonable(value: Any) -> Any:
    """Pydantic 模型 / 嵌套结构 → JSON 可序列化 dict。"""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def subgraph_input(state: WorkflowState, agent_key: str) -> str:
    """把 WorkflowState 中该 Agent 需要的字段打包成 JSON 提示词载荷。

    只取该 Agent 上下文 + 前置结果（避免 messages 全量塞入提示词）；
    序列化 Pydantic → dict，default=str 兜底 datetime 等不可序列化值。
    """
    keys = {
        "knowledge_retrieval": ["perception_result", "mine_context"],
        "dispatch_decision": ["perception_result", "knowledge_result", "operational_context"],
        "resource_evaluation": ["decision_result", "evaluation_constraints"],
        "collaborative_control": ["decision_result", "perception_result", "approval_context", "recipient_context"],
        "reflection_iteration": ["perception_result", "decision_result", "resource_result", "control_result", "execution_observations"],
    }
    payload: dict[str, Any] = {"context": _to_jsonable(state.get("context"))}
    # 将实际输入及原文交给增强节点，避免任务目标在调用子图时丢失。
    if state.get("custom_task"):
        payload["monitoring_input"] = state["custom_task"]
        payload["user_query"] = state.get("user_query")
    for key in keys.get(agent_key, []):
        payload[key] = _to_jsonable(state.get(key))
    return json.dumps(payload, ensure_ascii=False, default=str)


# 常驻子图事件循环：MCP 连接与 react_agent 均绑定此循环，全链路同 loop 存活
_SUBGRAPH_LOOP: asyncio.AbstractEventLoop | None = None
_SUBGRAPH_LOOP_THREAD: threading.Thread | None = None


def _loop_runner(loop: asyncio.AbstractEventLoop) -> None:
    """子图线程入口：绑定并永久运行事件循环。"""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _ensure_subgraph_loop() -> asyncio.AbstractEventLoop:
    """惰性启动常驻子图事件循环线程（daemon，进程退出自动回收）。"""
    global _SUBGRAPH_LOOP, _SUBGRAPH_LOOP_THREAD
    if _SUBGRAPH_LOOP is None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=_loop_runner,
            args=(loop,),
            name="llm-subgraph-loop",
            daemon=True,
        )
        thread.start()
        _SUBGRAPH_LOOP = loop
        _SUBGRAPH_LOOP_THREAD = thread
    return _SUBGRAPH_LOOP


def run_llm_subgraph(
    agent_key: str,
    result_schema: type,
    state: WorkflowState,
    *,
    timeout_s: float = 180.0,
) -> dict:
    """同步入口：在常驻子图事件循环上运行 LLM 子图并返回 {result, model_mode}。

    同步节点（validate_neo4j 直接调用 / LangGraph async 驱动下的 worker 线程）经
    run_coroutine_threadsafe 提交到此循环并阻塞等待结果。超时抛 TimeoutError，
    由上层节点 try/except 兜底确定性（任何异常都不阻断处置链路）。
    """
    loop = _ensure_subgraph_loop()

    async def _go() -> dict:
        sub = await build_agent_subgraph(agent_key, result_schema)
        return await sub.ainvoke({"task_payload": subgraph_input(state, agent_key)})

    future = asyncio.run_coroutine_threadsafe(_go(), loop)
    try:
        return future.result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise
