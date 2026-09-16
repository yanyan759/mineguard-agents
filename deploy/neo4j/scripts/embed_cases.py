"""AccidentCase 图嵌入摄取（YAML 权威源 → bge-m3 向量属性）。

对每个事故案例的致灾因子+处置动作+结果文本生成 1024 维 bge-m3 嵌入，
MERGE 到 AccidentCase.embedding 属性，并幂等建原生向量索引。

用法：
    cd agent1
    python deploy/neo4j/scripts/embed_cases.py            # 幂等（已有嵌入跳过）
    python deploy/neo4j/scripts/embed_cases.py --force    # 全部重嵌
    python deploy/neo4j/scripts/embed_cases.py --dry-run  # 仅打印将嵌入案例，不写图

环境变量：NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD（图）+ EMBEDDING_BASE_URL/EMBEDDING_MODEL/EMBEDDING_DIM（嵌入）
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

import yaml

AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "accident_cases.yaml"
# 真实数据域：21 份真实案例（--real 摄取到 RealAccidentCase 独立标签 + 独立向量索引，
# 不触碰 AccidentCase 仿真基线的 5 条嵌入与 accident_case_embedding 索引）
REAL_CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "real_accident_cases.yaml"

EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "bge-m3:567m")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# 原生向量索引（Neo4j 5.13+；5.26 内建，无需 APOC）
_VECTOR_INDEX = (
    "CREATE VECTOR INDEX accident_case_embedding IF NOT EXISTS "
    "FOR (c:AccidentCase) ON (c.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}"
)
# 真实数据域向量索引（RealAccidentCase 独立，与仿真索引分离）
_VECTOR_INDEX_REAL = (
    "CREATE VECTOR INDEX real_accident_case_embedding IF NOT EXISTS "
    "FOR (c:RealAccidentCase) ON (c.embedding) "
    "OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}"
)


def case_text(c: dict) -> str:
    """案例 → 嵌入文本：致灾因子 + 处置动作 + 结果。"""
    parts = [*c.get("causes", []), *c.get("actions", [])]
    if c.get("outcome"):
        parts.append(c["outcome"])
    return "。".join(str(p) for p in parts if p) or c["case_id"]


def _embed(texts: list[str]) -> list[list[float]]:
    """批量调 Ollama /api/embed 生成嵌入。"""
    payload = json.dumps({"model": EMBEDDING_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        f"{EMBEDDING_BASE_URL}/api/embed", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        raise SystemExit(f"嵌入服务不可达（{EMBEDDING_BASE_URL}）: {exc}") from exc
    embs = data.get("embeddings", [])
    if not embs or len(embs[0]) != EMBEDDING_DIM:
        raise SystemExit(f"嵌入维度异常：期望 {EMBEDDING_DIM}，实际 {len(embs[0]) if embs else 0}")
    return embs


def main() -> int:
    parser = argparse.ArgumentParser(description="AccidentCase 图嵌入摄取（bge-m3）")
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", "agent1_dev"))
    parser.add_argument("--force", action="store_true", help="全部重嵌（忽略已有 embedding）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将嵌入案例，不写图")
    parser.add_argument("--real", action="store_true",
                        help="嵌入真实案例域（real_accident_cases.yaml → RealAccidentCase + 独立向量索引）")
    args = parser.parse_args()

    # --real 切换真实数据域（独立 YAML/标签/向量索引），否则走仿真基线
    yaml_path = REAL_CASES_YAML if args.real else CASES_YAML
    label = "RealAccidentCase" if args.real else "AccidentCase"
    vector_index = _VECTOR_INDEX_REAL if args.real else _VECTOR_INDEX
    domain = "真实" if args.real else "仿真"

    cases = yaml.safe_load(yaml_path.read_text(encoding="utf-8")).get("cases", [])
    if args.dry_run:
        for c in cases:
            print(f"  [dry] {c['case_id']} <- {case_text(c)[:40]}...")
        print(f"[dry] 将嵌入 {len(cases)} 个{domain}案例")
        return 0

    from neo4j import GraphDatabase  # 延迟导入：--dry-run 无 Neo4j 也能跑
    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        with driver.session() as session:
            session.run(vector_index).consume()  # 幂等建向量索引
            todo = []
            for c in cases:
                if args.force:
                    todo.append(c)
                    continue
                row = session.run(
                    f"MATCH (c:{label} {{case_id: $cid}}) RETURN c.embedding AS emb",
                    cid=c["case_id"],
                ).single()
                if row is None or row["emb"] is None:
                    todo.append(c)
            if not todo:
                print(f"[skip] {domain}案例全部已有嵌入（--force 强制重嵌）")
            else:
                embs = _embed([case_text(c) for c in todo])
                for c, emb in zip(todo, embs):
                    session.run(
                        f"MATCH (c:{label} {{case_id: $cid}}) SET c.embedding = $emb",
                        cid=c["case_id"], emb=emb,
                    ).consume()
                print(f"[ok] 写入 {len(todo)} 条{domain}案例嵌入（{EMBEDDING_DIM} 维）")
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
