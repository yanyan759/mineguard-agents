"""
Tavily 互联网搜索工具模块

提供煤矿安全领域的互联网检索适配：
- 搜索关键词自动注入煤矿领域前缀
- 返回结果带 source_type="web" 来源标识
- 结构化返回 {status, data, error, audit}
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()


def _build_result(
    status: str,
    data: Any = None,
    error: str | None = None,
    query: str | None = None,
) -> dict:
    return {
        "status": status,
        "data": data,
        "error": error,
        "source_type": "web",
        "audit": {
            "tool": "internet_search",
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def _inject_coal_prefix(query: str) -> str:
    """为搜索查询自动注入煤矿领域前缀，提升检索精准度。"""
    coal_keywords = ["煤矿", "顶板", "煤", "矿", "coal", "mine", "roof", "支护"]
    if any(kw in query.lower() for kw in coal_keywords):
        return query
    return f"煤矿 顶板安全 {query}"


@tool
def internet_search(query: str) -> dict:
    """从互联网检索煤矿安全、顶板灾变相关的公开信息。

    用于补充内部知识库未涵盖的公开法规、标准和行业资料。
    搜索结果标记为"公开资料"，权威性低于内部规程和专家意见。

    :param query: 搜索关键词
    """
    if not query or not query.strip():
        return _build_result("rejected", error="搜索关键词不能为空")

    enriched_query = _inject_coal_prefix(query)
    api_key = os.getenv("TAVILY_API_KEY")

    if not api_key:
        return _build_result(
            "error",
            data=f"[离线模式] 搜索关键词：{enriched_query}（需设置 TAVILY_API_KEY）",
            error="TAVILY_API_KEY 未配置",
            query=enriched_query,
        )

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=enriched_query, search_depth="basic", max_results=5,
        )
        results = response.get("results", [])
        data = {
            "query": enriched_query,
            "results": [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "content": r.get("content", "")[:500]}
                for r in results[:5]
            ],
            "result_count": len(results),
        }
        return _build_result("success", data=data, query=enriched_query)
    except ImportError:
        return _build_result(
            "error",
            data=f"[离线模式] {enriched_query}",
            error="tavily-python 未安装", query=enriched_query,
        )
    except Exception as e:
        return _build_result("error", error=str(e), query=enriched_query)
