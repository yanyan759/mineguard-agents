"""
RAGFlow 知识库查询工具模块

提供煤矿顶板灾变领域知识库访问：
- get_assistant_list：查询 RAGFlow 中 3 个煤矿检索助手（自动发现真实 chat_id），未连接时返回内置映射
- create_ask_delete：向指定助手创建临时会话并提问（v0.26.4 Chat API），离线降级为模拟答案
- 返回值统一为结构化字典 + source_type 标识

支持通过环境变量连接 RAGFlow 知识库（默认 API 端口 9380），3 个知识库
（coal_mine_safety_regulations / coal_mine_accident_cases / coal_mine_support_schemes）
与 3 个检索助手由 deploy/ragflow/scripts/ragflow_import.py 创建。
本模块通过 RAGFLOW_API_URL / RAGFLOW_API_KEY（agent1/.env）连接真实实例；
未配置时回退离线模式（模拟答案并明确标注）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

# 知识检索会话超时：RAGFlow 侧跑 Ollama deepseek-r1 推理（含知识库检索），放宽到 180s
_RAGFLOW_TIMEOUT = 180

# 煤矿领域 RAGFlow 助手映射：key → 助手名 + 关联知识库（与 ragflow_import.py 的 ASSISTANTS 对齐）
_COAL_MINE_ASSISTANTS = {
    "safety_regulation": {
        "name": "煤矿安全规程检索助手",
        "description": "检索《煤矿安全规程》《冲击地压防治技术规范》等法规标准条款",
        "knowledge_base": "coal_mine_safety_regulations",
    },
    "accident_case": {
        "name": "顶板事故案例检索助手",
        "description": "检索历史顶板事故案例（位置/诱因/前兆/处置/后果）",
        "knowledge_base": "coal_mine_accident_cases",
    },
    "support_scheme": {
        "name": "支护方案检索助手",
        "description": "检索历史支护方案和专家研判记录",
        "knowledge_base": "coal_mine_support_schemes",
    },
}


def _build_result(
    status: str,
    data: Any = None,
    error: str | None = None,
    source_type: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """构建统一结构化返回格式。"""
    return {
        "status": status,
        "data": data,
        "error": error,
        "source_type": source_type,
        "audit": {
            "tool": "ragflow_query",
            "source_type": source_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        },
    }


def _ragflow_api() -> tuple[str, str] | None:
    """读取 RAGFlow 连接配置；未配置 API Key 返回 None（离线降级）。"""
    api_key = os.getenv("RAGFLOW_API_KEY")
    if not api_key:
        return None
    base_url = (os.getenv("RAGFLOW_API_URL") or "http://localhost:9380").rstrip("/")
    return base_url, api_key


def _list_real_chats(api: tuple[str, str]) -> list[dict] | None:
    """调用 RAGFlow 获取全部对话助手；调用失败返回 None（调用方回退内置映射）。"""
    base_url, api_key = api
    try:
        resp = requests.get(
            f"{base_url}/api/v1/chats",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        # v0.26.4：GET /chats 返回 data.chats 列表，data 可能为 null，用 or [] 兜底
        return resp.json().get("data", {}).get("chats", []) or []
    except Exception:
        return None


@tool
def get_assistant_list() -> dict:
    """查询 RAGFlow 中当前可用的煤矿安全知识库助手。

    返回每个助手的名称、描述和关联知识库信息。
    已连接真实 RAGFlow 时自动发现助手（mode=ragflow_online），否则返回内置映射（offline_fallback）。
    调用后可根据助手描述选择最匹配的助手进行提问。
    """
    api = _ragflow_api()
    chats = _list_real_chats(api) if api else None
    mode = "ragflow_online" if chats is not None else "offline_fallback"
    # 在线：assistant_id 换成 RAGFlow 真实 chat_id；离线：内置 key
    by_name = {c.get("name"): c for c in chats} if chats is not None else {}
    assistants = []
    for key, info in _COAL_MINE_ASSISTANTS.items():
        real = by_name.get(info["name"])
        assistants.append(
            {
                "assistant_id": real["id"] if real else key,
                "name": info["name"],
                "description": info["description"],
                "knowledge_base": info["knowledge_base"],
                "ragflow_chat_id": real["id"] if real else None,
            }
        )
    return _build_result(
        "success",
        data={"assistants": assistants, "count": len(assistants)},
        source_type="ragflow",
        metadata={"mode": mode},
    )


def _ragflow_ask(api: tuple[str, str], assistant_name: str, question: str) -> dict:
    """调用 RAGFlow Chat API 检索指定助手，返回 {answer, reference}。

    v0.26.4 流程：按名解析 chat_id → 创建临时会话 → POST /chat/completions（非流式）→ 删除会话。
    """
    base_url, api_key = api
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 1. 按助手名解析真实 chat_id
    chats = _list_real_chats(api) or []
    chat = next((c for c in chats if c.get("name") == assistant_name), None)
    if not chat:
        raise RuntimeError(f"RAGFlow 中未找到助手「{assistant_name}」")
    chat_id = chat["id"]

    # 2. 创建临时会话
    resp = requests.post(
        f"{base_url}/api/v1/chats/{chat_id}/sessions",
        json={"name": "煤矿知识检索会话"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    session_id = resp.json()["data"]["id"]

    # 3. 提问（非流式，等待完整答案）
    try:
        resp = requests.post(
            f"{base_url}/api/v1/chat/completions",
            json={
                "chat_id": chat_id,
                "session_id": session_id,
                "question": question,
                "stream": False,
            },
            headers=headers,
            timeout=_RAGFLOW_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code", 0) != 0:
            raise RuntimeError(payload.get("message", "completions 返回错误"))
        return payload.get("data") or {}
    finally:
        # 4. 删除临时会话（create-ask-delete 模式），清理失败不影响结果
        try:
            requests.delete(
                f"{base_url}/api/v1/chats/{chat_id}/sessions",
                json={"ids": [session_id]},
                headers=headers,
                timeout=30,
            )
        except Exception:
            pass


@tool
def create_ask_delete(assistant_name: str, question: str) -> dict:
    """向指定 RAGFlow 助手创建临时会话并提问。

    调用前需通过 get_assistant_list 确认可用助手。
    问题应围绕顶板灾变知识检索需求，优先查询安全规程/事故案例/支护方案。
    已连接真实 RAGFlow 时返回知识库检索答案（含引用来源），否则返回明确标注的模拟答案。

    :param assistant_name: 助手名称
    :param question: 检索问题，需包含煤矿领域关键词
    """
    # 查找匹配的助手
    matched = None
    for key, info in _COAL_MINE_ASSISTANTS.items():
        if info["name"] == assistant_name or key == assistant_name:
            matched = (key, info)
            break

    if not matched:
        return _build_result(
            "error",
            error=f"未找到助手 '{assistant_name}'",
            source_type="ragflow",
        )

    key, info = matched
    api = _ragflow_api()
    if api is None:
        # 离线降级：返回模拟答案（明确标注非真实检索）
        return _build_result(
            "success",
            data={
                "assistant": assistant_name,
                "question": question,
                "answer": (
                    f"[离线模式] 已向「{assistant_name}」提问：{question}\n"
                    f"知识库：{info['knowledge_base']}\n"
                    f"参考 fixtures/ 中的 accident_cases.yaml / regulations.yaml 获取模拟数据"
                ),
                "session_deleted": True,
            },
            source_type="ragflow",
            metadata={
                "assistant_key": key,
                "knowledge_base": info["knowledge_base"],
                "mode": "offline_fallback",
            },
        )

    # 在线检索：创建临时会话 → 提问 → 删除会话
    try:
        answer = _ragflow_ask(api, info["name"], question)
        return _build_result(
            "success",
            data={
                "assistant": assistant_name,
                "question": question,
                "answer": answer.get("answer", ""),
                "references": answer.get("reference", []),
                "session_deleted": True,
            },
            source_type="ragflow",
            metadata={
                "assistant_key": key,
                "knowledge_base": info["knowledge_base"],
                "mode": "ragflow_online",
            },
        )
    except Exception as e:
        # 在线失败返回明确错误（不静默降级为模拟答案，避免误导调度决策）
        # data 仍为字典：答案字段标注失败原因，供校验/上层节点安全读取
        return _build_result(
            "error",
            data={
                "assistant": assistant_name,
                "question": question,
                "answer": f"[RAGFlow 检索失败] {e}",
                "session_deleted": True,
            },
            error=f"RAGFlow 检索失败：{e}",
            source_type="ragflow",
            metadata={
                "assistant_key": key,
                "knowledge_base": info["knowledge_base"],
                "mode": "ragflow_online_error",
            },
        )
