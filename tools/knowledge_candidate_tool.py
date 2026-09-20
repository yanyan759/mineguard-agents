"""
知识沉淀候选暂存工具

将反思 Agent 输出的知识候选暂存为结构化 JSONL，等待人工审核后入库。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "knowledge_candidate",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@tool
def stage_candidate(
    candidates: list[dict],
    reflection_id: str = "",
    workflow_run_id: str = "",
) -> dict:
    """将反思 Agent 生成的知识候选暂存到审核队列。

    知识候选使用统一字段：
    - entity: 实体名称
    - entity_type: 实体类型（地质构造/支护设备/顶板灾变/监测指标/处置规程/岗位角色/巷道分区）
    - relation: 关系类型（诱发/支护/防控/监测/调度/上报/传导）
    - evidence: 证据片段
    - source: 来源标识
    - confidence: 置信度 0-1
    - review_status: 固定为 "pending_review"（需人工审核）

    :param candidates: 知识候选列表
    :param reflection_id: 关联反思 ID
    :param workflow_run_id: 关联工作流运行 ID
    """
    if not candidates:
        return _build_result("error", error="知识候选列表不能为空")

    staged = []
    for i, c in enumerate(candidates):
        candidate_entry = {
            "candidate_id": f"kc-{workflow_run_id}-{reflection_id}-{i:03d}",
            "entity": c.get("entity", ""),
            "entity_type": c.get("entity_type", "unknown"),
            "relation": c.get("relation", ""),
            "evidence": c.get("evidence", ""),
            "source": c.get("source", "reflection_agent"),
            "confidence": float(c.get("confidence", 0.5)),
            "review_status": "pending_review",
            "reflection_id": reflection_id,
            "workflow_run_id": workflow_run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        staged.append(candidate_entry)

    # 输出到审核队列，人工确认后再写入知识库
    import json
    output = json.dumps(staged, ensure_ascii=False, indent=2)

    return _build_result(
        "success",
        data={
            "candidates_staged": len(staged),
            "candidates": staged,
            "note": "知识候选已暂存为审核队列，需人工审核后入库。"
                    "后续阶段通过 Neo4j 自动写入时，将 review_status 改为 'approved' 后触发。",
            "json_output": output[:2000],  # 截断，完整内容由文件工具输出
        },
    )
