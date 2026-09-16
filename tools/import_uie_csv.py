"""UIE 标注成果接入：标注 CSV → 审核队列 → 写回知识图谱。

把 任务成果/02_UIE标注数据/标注_合并_2026-08-17.csv（452 条人工标注，8 列契约
text/entity/entity_type/relation/object/object_type/confidence/source）摄入为知识候选队列：

1. **object 逐字校验**（标注铁律）：object 必须逐字出现在 text，违规条目拒绝并列出；
2. **完整 SPO 判定**：entity + relation + object 齐备且 entity_type/object_type/relation
   都在 7 类枚举内；
3. **审核门**（治理红线：审核门是唯一入库通道）：完整 SPO 候选经 apply_review(approved)
   打标后由 write_knowledge_to_graph 写回 KnowledgeEntity 图谱（MERGE 幂等）；缺
   entity/relation 的非完整 SPO 条目仅统计保留（等待 LLM 抽取/人工补齐），不写回。

用法：
    cd agent1
    python tools/import_uie_csv.py --csv ../任务成果/02_UIE标注数据/标注_合并_2026-08-17.csv
    python tools/import_uie_csv.py --csv ... --reviewer 张三    # 指定审核人（默认"标注人工审核"）
    python tools/import_uie_csv.py --csv ... --dry-run          # 仅统计与校验，不写图
    python tools/import_uie_csv.py --csv ... --out queue.jsonl # 输出全量候选队列 JSONL 供 review_candidates 复核
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# 图谱写回默认注入（同 validate_uie_phase8；未配置则存储层拒写并在日志说明）
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")

# 与 uie_extractor / knowledge_sources 对齐的枚举（避免重复定义漂移）
ENTITY_TYPES = ["地质构造", "支护设备", "顶板灾变", "监测指标", "处置规程", "岗位角色", "巷道分区"]
RELATION_TYPES = ["诱发", "支护", "防控", "监测", "调度", "上报", "传导"]

DEFAULT_CSV = AGENT1_ROOT.parent / "任务成果" / "02_UIE标注数据" / "标注_合并_2026-08-17.csv"


def _norm(s: str) -> str:
    """去空白规范化（object 逐字校验用：忽略空格/全角空格差异）。"""
    return "".join(s.split())


def load_csv(path: Path) -> list[dict]:
    """读取 8 列标注 CSV（utf-8-sig 兼容 BOM），返回逐行 dict。"""
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            rows.append({
                "row": i + 2,  # 表头占第 1 行，数据从第 2 行起
                "text": (row.get("text") or "").strip(),
                "entity": (row.get("entity") or "").strip(),
                "entity_type": (row.get("entity_type") or "").strip(),
                "relation": (row.get("relation") or "").strip(),
                "object": (row.get("object") or "").strip(),
                "object_type": (row.get("object_type") or "").strip(),
                "confidence": row.get("confidence") or "",
                "source": (row.get("source") or "").strip(),
            })
    return rows


def _validate_object_verbatim(row: dict) -> str | None:
    """object 逐字出现在 text（标注铁律）；违规返回错误信息。"""
    if not row["object"]:
        return "object 为空"
    if _norm(row["object"]) not in _norm(row["text"]):
        return f"object={row['object']!r} 未逐字出现在 text"
    return None


def to_candidates(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """把 CSV 行转为知识候选队列。

    返回 (candidates, rejected_rows)：
    - candidates：全部行转为候选（含完整/非完整 SPO），review_status=pending_review
    - rejected_rows：object 未逐字出现的违规行（标注铁律违反，不产生候选）
    """
    candidates, rejected_rows = [], []
    for r in rows:
        err = _validate_object_verbatim(r)
        if err:
            rejected_rows.append({**r, "reason": err})
            continue
        # object_type 缺失时按实体类型推断不成——保持原样，由审核门判定
        candidates.append({
            "candidate_id": f"uie-csv-{r['row']:04d}",
            "entity": r["entity"],
            "entity_type": r["entity_type"],
            "relation": r["relation"],
            "object": r["object"],
            "object_type": r["object_type"],
            "confidence": float(r["confidence"]) if _is_float(r["confidence"]) else 0.5,
            "evidence": r["text"],          # 标注的 text 即证据原文
            "source": r["source"],
            "review_status": "pending_review",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "csv_row": r["row"],
        })
    return candidates, rejected_rows


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _complete_spo(c: dict) -> bool:
    """完整 SPO：entity+relation+object 齐备且三枚举合法。"""
    return all([
        c.get("entity") and c.get("relation") and c.get("object"),
        c.get("entity_type") in ENTITY_TYPES,
        c.get("object_type") in ENTITY_TYPES,
        c.get("relation") in RELATION_TYPES,
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="UIE 标注 CSV → 审核队列 → 写回 Neo4j")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="8 列标注 CSV 路径")
    parser.add_argument("--reviewer", default="标注人工审核", help="审核人姓名（写回必填）")
    parser.add_argument("--dry-run", action="store_true", help="仅统计与校验，不写图")
    parser.add_argument("--out", type=Path, default=None, help="输出全量候选队列 JSONL（可选）")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"[error] CSV 不存在: {args.csv}")
        return 1

    rows = load_csv(args.csv)
    print(f"[info] CSV 数据行: {len(rows)}  <-  {args.csv.name}")

    candidates, rejected = to_candidates(rows)
    if rejected:
        print(f"[warn] object 未逐字校验失败 {len(rejected)} 行（拒绝入队）:")
        for r in rejected[:5]:
            print(f"   CSV 第 {r['row']} 行: {r['reason']}")

    complete = [c for c in candidates if _complete_spo(c)]
    incomplete = [c for c in candidates if not _complete_spo(c)]
    print(f"[info] 完整 SPO 候选: {len(complete)} / 非完整 SPO: {len(incomplete)} / 违规行: {len(rejected)}")

    # 可选：全量候选队列落盘（供 review_candidates CLI 逐条人工复核）
    if args.out:
        with args.out.open("w", encoding="utf-8") as fh:
            for c in candidates:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"[ok] 候选队列已输出: {args.out}（{len(candidates)} 条）")

    if args.dry_run:
        print("[dry] 未写图（--dry-run）")
        return 0

    if not complete:
        print("[info] 无完整 SPO 候选，跳过写回")
        return 0

    # 审核门：完整 SPO 候选经 apply_review(approved) 打标后统一写回
    from tools.review_candidates import apply_review, write_approved
    reviewed = [
        apply_review(c, reviewer=args.reviewer, decision="approve",
                     auto_fill={"evidence": c.get("evidence", ""), "confidence": c.get("confidence", 0.5)})
        for c in complete
    ]
    res = write_approved(reviewed)
    data = res.get("data") or {}
    written = data.get("written", 0)
    rejected_info = data.get("rejected", [])
    print(f"[ok] 写回 {written} 条 approved 三元组（KnowledgeEntity）")
    if rejected_info:
        reasons = {}
        for r in rejected_info:
            reason = (r.get("reason") or "").split("，")[0]
            reasons[reason] = reasons.get(reason, 0) + 1
        print(f"[warn] 存储层拒写 {len(rejected_info)} 条: {reasons}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
