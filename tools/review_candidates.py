"""知识候选人工审核 CLI（JSONL → 审核 → 写回知识图谱）。

读取 stage_candidate 产出的审核队列，逐条展示，人工 approved/rejected，
approved 且完整 SPO 的条目经 write_knowledge_to_graph 入库（子步③审核门兜底）。

核心决策逻辑（apply_review / write_approved）为纯函数，供验收脚本直接驱动；
交互输入仅在 main() 中。

用法：
    cd agent1
    python tools/review_candidates.py candidates.jsonl --reviewer 张三
    python tools/review_candidates.py candidates.jsonl --reviewer 张三 --auto-extract
    python tools/review_candidates.py candidates.jsonl --reviewer 张三 --auto-extract --auto-approve
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.agent.knowledge_sources import write_knowledge_to_graph

try:  # 可选依赖：未安装不影响纯审核
    from tools.uie_extractor import EntityRelationExtractor
except ImportError:  # pragma: no cover
    EntityRelationExtractor = None


def load_queue(path: Path) -> list[dict]:
    """读取审核队列 JSONL。"""
    entries = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def apply_review(
    entry: dict,
    *,
    reviewer: str,
    decision: str,            # approve / reject / skip
    rejection_reason: str = "",
    auto_fill: dict | None = None,  # UIE 抽取补 object（调用方预填）
) -> dict:
    """纯函数：把单条候选按决策打标（供 CLI 与验收测试共用）。"""
    out = dict(entry)
    if auto_fill:
        out.update(auto_fill)  # object / object_type / confidence / evidence
    if decision == "reject":
        out["review_status"] = "rejected"
        out["rejection_reason"] = rejection_reason or "未说明原因"
    elif decision == "approve":
        out["review_status"] = "approved"
    else:
        out["review_status"] = "skipped"
    out["reviewed_by"] = reviewer
    out["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    return out


def write_approved(entries: list[dict]) -> dict:
    """approved 且完整 SPO 的条目写回 Neo4j（子步③审核门兜底校验）。"""
    approved = [e for e in entries if e.get("review_status") == "approved"]
    if not approved:
        return {"status": "success", "written": 0, "rejected": [], "note": "无 approved 候选"}
    return write_knowledge_to_graph(approved)


def review_all(
    entries: list[dict],
    *,
    reviewer: str,
    decisions: list[str] | None = None,  # 与 entries 等长；None 表示逐条交互输入
    auto_extract: bool = False,
) -> tuple[list[dict], dict]:
    """批量审核：返回 (打标后条目, 写回结果)。decisions[i] 缺失时为 "auto" → 交互输入。"""
    extractor = None
    if auto_extract and EntityRelationExtractor is not None:
        extractor = EntityRelationExtractor()

    reviewed = []
    for i, entry in enumerate(entries):
        out = dict(entry)
        auto_fill = None
        if extractor is not None and not out.get("object"):
            try:
                auto_fill = extractor.extract_object(out)
            except Exception as exc:  # noqa: BLE001 — 抽取失败降级人工补填，不污染队列
                print(f"  [uiw] 候选 {out.get('candidate_id')} 抽取失败（保留 pending_review）: {exc}")
        decision = decisions[i] if decisions and i < len(decisions) else "auto"
        if decision == "auto":
            decision = _interact(out, reviewer)
        reviewed.append(apply_review(out, reviewer=reviewer, decision=decision, auto_fill=auto_fill))

    result = write_approved(reviewed)
    approved_n = sum(1 for e in reviewed if e.get("review_status") == "approved")
    rejected_n = sum(1 for e in reviewed if e.get("review_status") == "rejected")
    skipped_n = sum(1 for e in reviewed if e.get("review_status") == "skipped")
    summary = {
        "total": len(reviewed), "approved": approved_n,
        "rejected": rejected_n, "skipped": skipped_n,
        "written": result.get("data", {}).get("written", 0),
        "write_status": result.get("status"),
    }
    return reviewed, summary


def _interact(entry: dict, reviewer: str) -> str:
    """交互式单条审核（仅 CLI 使用）。"""
    print("─" * 60)
    print(f"候选 {entry.get('candidate_id')}:")
    print(f"  entity    = {entry.get('entity')}")
    print(f"  entity_type= {entry.get('entity_type')}")
    print(f"  relation  = {entry.get('relation')}")
    print(f"  evidence  = {entry.get('evidence')}")
    print(f"  object    = {entry.get('object', '')!r}  object_type={entry.get('object_type', '')!r}")
    action = input("  [a]pprove / [r]eject / [s]kip > ").strip().lower()
    if action.startswith("r"):
        return "reject"
    if action.startswith("a"):
        return "approve"
    return "skip"


def main() -> int:
    parser = argparse.ArgumentParser(description="知识候选人工审核（JSONL → 写回 Neo4j）")
    parser.add_argument("queue", type=Path, help="stage_candidate 产出的审核队列 JSONL")
    parser.add_argument("--reviewer", required=True, help="审核人姓名（必填）")
    parser.add_argument("--auto-extract", action="store_true", help="用 LLM 抽取预填 object")
    parser.add_argument("--auto-approve", action="store_true", help="全部自动 approved（非交互，慎用）")
    args = parser.parse_args()

    if not args.queue.exists():
        print(f"[error] 审核队列不存在: {args.queue}")
        return 1
    entries = load_queue(args.queue)
    if not entries:
        print("[info] 队列为空")
        return 0
    # --auto-approve 时批量 approved（非交互）；否则逐条交互
    decisions = ["approve"] * len(entries) if args.auto_approve else None
    reviewed, summary = review_all(
        entries, reviewer=args.reviewer, decisions=decisions,
        auto_extract=args.auto_extract,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
