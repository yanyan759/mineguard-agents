"""MCP 客户端桥接。

用 langchain-mcp-adapters 长驻连接本地 stdio MCP server（mcp_server.server），
把 9 个 MCP 工具转为 LangChain BaseTool，供六 Agent 子图（create_agent）按白名单
挂载做 LLM tool-calling。驱动层 async 化后，Agent 子图与 MCP client 均为
async-native，在 FastAPI 事件循环上协同。

关键约定（spec §5）：
- 白名单见 AGENT_TOOL_WHITELIST：每个 Agent 只挂载其职责相关的 MCP 工具
- 工具名去重：MCP 工具转出后可能与 tools/ 现有工具重名，get_agent_tools 按
  白名单挑选并以 MCP 工具为准（canonical 工具面），tools/ 保留不删
- PermissionGate 仅服务外部直连；工作流内变更工具经 langgraph 自带工具调用，
  返回 needs_approval 信封由调用方转人工审批（spec §5.3）
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache

from langchain_mcp_adapters.client import MultiServerMCPClient

# 本地 stdio server 入口：cd agent1 && python -m mcp_server.server
_MCP_STDIO_CMD = [sys.executable, "-m", "mcp_server.server"]

# Agent → MCP 工具白名单（spec §5.2；映射依据六 Agent 职责与 9 工具语义）
AGENT_TOOL_WHITELIST: dict[str, list[str]] = {
    "perception_warning": ["query_sensor_history"],
    "knowledge_retrieval": ["query_roof_risk_case", "query_safety_regulation"],
    "dispatch_decision": ["query_roof_risk_case", "query_safety_regulation", "query_disposal_result"],
    "resource_evaluation": ["query_available_workers", "query_emergency_equipment"],
    "collaborative_control": ["create_disposal_work_order", "create_warning_record", "run_digital_twin_simulation"],
    "reflection_iteration": ["query_disposal_result"],
}

# 长驻 client 单例：避免每次调用重复起 stdio 子进程
_client: MultiServerMCPClient | None = None


def _get_mcp_client() -> MultiServerMCPClient:
    """返回长驻 MultiServerMCPClient（惰性初始化单例）。"""
    global _client
    if _client is None:
        _client = MultiServerMCPClient(
            {
                "coal-mine": {
                    "command": _MCP_STDIO_CMD[0],
                    "args": _MCP_STDIO_CMD[1:],
                    "transport": "stdio",
                }
            }
        )
    return _client


def get_agent_tool_names(agent_key: str) -> list[str]:
    """返回某 Agent 白名单对应的 MCP 工具名列表。"""
    return AGENT_TOOL_WHITELIST.get(agent_key, [])


async def get_agent_tools(agent_key: str) -> list:
    """返回某 Agent 白名单对应的 LangChain BaseTool 列表（async 获取）。

    :param agent_key: Agent key（perception_warning / knowledge_retrieval / ...）
    :return: 按白名单过滤后的 BaseTool 列表；MCP server 不可达时返回 []（调用方回退确定性）
    """
    try:
        client = _get_mcp_client()
        all_tools = await client.get_tools(server_name="coal-mine")
    except Exception as e:
        print(f"[MCPBridge] 获取 MCP 工具失败（agent={agent_key}）: {e}")
        return []
    whitelist = set(get_agent_tool_names(agent_key))
    return [t for t in all_tools if t.name in whitelist]
