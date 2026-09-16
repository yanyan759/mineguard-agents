"""真实数据入系统验收脚本（真实数据域 = tests/fixtures/real_accident_cases.yaml，21 份）。

验证 21 份真实事故案例（real-* case_id，官方 URL 凭证）进入 Neo4j 图谱 + 知识检索链路，
同时守护仿真基线（AccidentCase==5 不破，12 项 Neo4j 验收独立于本脚本）。

运行：cd agent1 && python tests/validate_real_data.py
前置：Neo4j 容器（seed --real 已执行）；向量用例前置 Ollama bge-m3（embed --real 已执行）

10 个用例：
  R1. 真实数据域 YAML 完整性（21 份 + real-* 前缀 + 字段齐全 + source_url 凭证）
  R2. 等级/年份分布契约（red≥2 / orange≥4 / yellow≥14 / blue≥1）
  R3. seed --real 摄取幂等（×2 计数不变；RealAccidentCase==21；case_id 集合 = real YAML）
  R4. 仿真基线守护（AccidentCase==5、Regulation==10、RiskLevel==5 不受真实域影响）
  R5. 真实数据凭证入图（每份含 source_url + published_at）
  R6. REAL_CASES=on 双轨全序一致（YAML vs Neo4j similar_cases/support/regulations/missing）
  R7. 图谱模式真实案例检索（red 预警 → 返回 real-2017-001 担水沟 / real-2017-002 红阳三矿）
  R8. 向量模式真实案例检索（EMBEDDING_PROVIDER + REAL_CASES=on → real 向量相似命中）
  R9. 真实案例图嵌入幂等（embed --real×2 计数不变；21 条 × 1024 维；向量索引存在）
  R10. 摄取清单.json 与 real YAML case_id 集合一致（21 份）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# agent1/ 在 sys.path 上才能解析 from app.xxx
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# 本脚本验收真实数据域，默认注入 REAL_CASES=on + NEO4J_URI（图谱模式）
os.environ.setdefault("REAL_CASES", "on")
NEO4J_URI = os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")

REAL_CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "real_accident_cases.yaml"
SIM_CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "accident_cases.yaml"
INGEST_MANIFEST = AGENT1_ROOT.parent / "任务成果" / "05_图谱激活输入" / "摄取清单.json"

import yaml  # noqa: E402


def _driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        NEO4J_URI,
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "agent1_dev")),
    )


# ═══════════════════════════════════════════════════════════════════════════
# R1：真实数据域 YAML 完整性
# ═══════════════════════════════════════════════════════════════════════════


def test_real_yaml_integrity() -> str:
    raw = yaml.safe_load(REAL_CASES_YAML.read_text(encoding="utf-8"))
    cases = raw["cases"]
    req = {"case_id", "location", "occurred_at", "risk_level", "causes",
           "precursors", "actions", "outcome", "lessons", "source", "metadata"}
    assert len(cases) == 21, f"真实案例应 21 份，实际 {len(cases)}"
    for c in cases:
        assert c["case_id"].startswith("real-"), f"case_id 应 real-* 前缀: {c['case_id']}"
        miss = req - set(c)
        assert not miss, f"{c['case_id']} 缺 {miss}"
        assert c["metadata"].get("source_url"), f"{c['case_id']} 缺 source_url（真实性凭证）"
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids)), "case_id 重复"
    return "real_accident_cases.yaml：21 份 / real-* 前缀 / 字段齐全 / source_url 凭证"


# ═══════════════════════════════════════════════════════════════════════════
# R2：等级/年份分布契约
# ═══════════════════════════════════════════════════════════════════════════


def test_risk_distribution() -> str:
    raw = yaml.safe_load(REAL_CASES_YAML.read_text(encoding="utf-8"))
    cases = raw["cases"]
    from collections import Counter
    levels = Counter(c["risk_level"] for c in cases)
    years = Counter(c["case_id"][5:9] for c in cases)
    assert levels.get("red", 0) >= 2, f"red 应 ≥2，实际 {levels.get('red', 0)}"
    assert levels.get("orange", 0) >= 4, f"orange 应 ≥4，实际 {levels.get('orange', 0)}"
    assert levels.get("yellow", 0) >= 14, f"yellow 应 ≥14，实际 {levels.get('yellow', 0)}"
    assert levels.get("blue", 0) >= 1, f"blue 应 ≥1，实际 {levels.get('blue', 0)}"
    # 近五年（2021-2025）覆盖 ≥15 份
    recent = sum(v for k, v in years.items() if k >= "2021")
    assert recent >= 15, f"近五年案例应 ≥15，实际 {recent}"
    return f"等级 {dict(levels)} / 年份 {dict(years)}（近五年 {recent}）"


# ═══════════════════════════════════════════════════════════════════════════
# R3：seed --real 摄取幂等
# ═══════════════════════════════════════════════════════════════════════════


def test_seed_real_idempotent() -> str:
    env = os.environ.copy()
    script = AGENT1_ROOT / "deploy" / "neo4j" / "scripts" / "seed_from_yaml.py"

    def run_seed() -> None:
        r = subprocess.run(
            [sys.executable, str(script), "--real"], cwd=AGENT1_ROOT, env=env,
            capture_output=True, text=True, timeout=180,
        )
        assert r.returncode == 0, f"seed --real 失败: {r.stderr[-400:]}"

    run_seed()  # 第一次
    run_seed()  # 第二次（MERGE 幂等断言）

    d = _driver()
    try:
        with d.session() as s:
            n = s.run("MATCH (c:RealAccidentCase) RETURN count(c) AS c").single()["c"]
            assert n == 21, f"RealAccidentCase 应 21，实际 {n}"
            h = s.run(
                "MATCH (:RealAccidentCase)-[:HAS_RISK_LEVEL]->(:RiskLevel) RETURN count(*) AS c"
            ).single()["c"]
            assert h == 21, f"HAS_RISK_LEVEL 应 21，实际 {h}"
            yaml_ids = {c["case_id"] for c in yaml.safe_load(REAL_CASES_YAML.read_text(encoding="utf-8"))["cases"]}
            graph_ids = {r["case_id"] for r in s.run("MATCH (c:RealAccidentCase) RETURN c.case_id AS case_id")}
            assert graph_ids == yaml_ids, f"真实案例 case_id 漂移: {graph_ids ^ yaml_ids}"
    finally:
        d.close()
    return "seed --real×2 幂等：RealAccidentCase=21 / HAS_RISK_LEVEL=21 / case_id 集合 = real YAML"


# ═══════════════════════════════════════════════════════════════════════════
# R4：仿真基线守护（真实数据域不破坏 5 份仿真基线）
# ═══════════════════════════════════════════════════════════════════════════


def test_simulation_baseline_preserved() -> str:
    d = _driver()
    try:
        with d.session() as s:
            sim = s.run("MATCH (c:AccidentCase) RETURN count(c) AS c").single()["c"]
            assert sim == 5, f"仿真 AccidentCase 应 5（基线），实际 {sim}"
            reg = s.run("MATCH (r:Regulation) RETURN count(r) AS c").single()["c"]
            assert reg == 10, f"Regulation 应 10，实际 {reg}"
            sev = s.run("MATCH (l:RiskLevel) RETURN count(l) AS c").single()["c"]
            assert sev == 5, f"RiskLevel 应 5，实际 {sev}"
    finally:
        d.close()
    return "仿真基线守护：AccidentCase=5 / Regulation=10 / RiskLevel=5（真实域不触碰）"


# ═══════════════════════════════════════════════════════════════════════════
# R5：真实数据凭证入图（source_url / published_at）
# ═══════════════════════════════════════════════════════════════════════════


def test_real_case_evidence() -> str:
    d = _driver()
    try:
        with d.session() as s:
            missing = s.run(
                "MATCH (c:RealAccidentCase) WHERE c.source_url IS NULL OR c.published_at IS NULL "
                "RETURN c.case_id AS id"
            ).data()
            assert not missing, f"缺凭证案例: {missing}"
            n_url = s.run(
                "MATCH (c:RealAccidentCase) WHERE c.source_url STARTS WITH 'http' RETURN count(c) AS c"
            ).single()["c"]
            assert n_url == 21, f"source_url 应 21 份含 http，实际 {n_url}"
    finally:
        d.close()
    return "21 份真实案例全部带 source_url + published_at 凭证入图"


# ═══════════════════════════════════════════════════════════════════════════
# R6：REAL_CASES=on 双轨全序一致（YAML vs Neo4j）
# ═══════════════════════════════════════════════════════════════════════════


def test_real_dualtrack_consistent() -> str:
    from app.agent.knowledge_sources import retrieve_knowledge
    from app.schemas.workflow import RiskLevel

    saved_uri = os.environ.get("NEO4J_URI")
    try:
        # YAML 后端（REAL_CASES=on 已注入）
        os.environ.pop("NEO4J_URI", None)
        ydata = retrieve_knowledge(risk_level=RiskLevel.RED, warning_id="w-r", factors=[], mine_context=None)
        assert any(c["case_id"].startswith("real-") for c in ydata.similar_cases), "YAML 后端应含真实案例"

        # Neo4j 图谱后端
        os.environ["NEO4J_URI"] = NEO4J_URI
        gdata = retrieve_knowledge(risk_level=RiskLevel.RED, warning_id="w-r", factors=[], mine_context=None)
        assert any(c["case_id"].startswith("real-") for c in gdata.similar_cases), "图谱后端应含真实案例"

        # 全序一致（case_id / similarity / actions）
        ysim = [(c["case_id"], c["similarity"], c["actions"]) for c in ydata.similar_cases]
        gsim = [(c["case_id"], c["similarity"], c["actions"]) for c in gdata.similar_cases]
        assert ysim == gsim, f"REAL_CASES=on 双轨 similar_cases 不一致:\nYAML={ysim}\nKG={gsim}"
        assert ydata.regulations == gdata.regulations, "regulations 不一致"
        assert ydata.missing_topics == gdata.missing_topics, "missing_topics 不一致"
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
    return "REAL_CASES=on：YAML/Neo4j 双轨 similar+regulations+missing 全序一致，均含真实案例"


# ═══════════════════════════════════════════════════════════════════════════
# R7：图谱模式真实案例检索（red 预警 → 重大事故真实案例）
# ═══════════════════════════════════════════════════════════════════════════


def test_real_retrieval_red() -> str:
    from app.agent.knowledge_sources import retrieve_knowledge
    from app.schemas.workflow import RiskLevel

    saved_uri = os.environ.get("NEO4J_URI")
    try:
        os.environ["NEO4J_URI"] = NEO4J_URI
        data = retrieve_knowledge(risk_level=RiskLevel.RED, warning_id="w-red", factors=[], mine_context=None)
        ids = [c["case_id"] for c in data.similar_cases]
        # red 预警 → 2 仿真 red + 2 真实 red（担水沟/红阳三矿）
        assert "real-2017-001" in ids, f"应含担水沟 real-2017-001，实际 {ids}"
        assert "real-2017-002" in ids, f"应含红阳三矿 real-2017-002，实际 {ids}"
        # 真实案例 source 非空（安全约束：知识结论必须带来源）
        for c in data.similar_cases:
            assert c["source"], f"真实案例 source 不应为空: {c['case_id']}"
        # evidence 描述应标注真实案例域
        assert "真实案例域" in data.evidence_description, "evidence 应标注真实案例域"
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
    return "red 预警检索：命中 real-2017-001 担水沟 + real-2017-002 红阳三矿，source 非空"


# ═══════════════════════════════════════════════════════════════════════════
# R8：向量模式真实案例检索（Ollama bge-m3，前置 embed --real）
# ═══════════════════════════════════════════════════════════════════════════


def test_real_vector_retrieval() -> str:
    import urllib.request

    # 前置检查：Ollama bge-m3 可达
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
            pass
    except Exception:
        return "SKIP（Ollama 不可达，向量用例跳过）"

    from app.agent.knowledge_sources import retrieve_knowledge
    from app.schemas.workflow import RiskLevel

    saved_uri = os.environ.get("NEO4J_URI")
    saved_emb = os.environ.get("EMBEDDING_PROVIDER")
    try:
        os.environ["NEO4J_URI"] = NEO4J_URI
        os.environ["EMBEDDING_PROVIDER"] = "ollama"
        data = retrieve_knowledge(
            risk_level=RiskLevel.RED, warning_id="w-vec-r",
            factors=["冲击地压", "采动应力叠加", "顶板事故"], mine_context=None,
        )
        ids = [c["case_id"] for c in data.similar_cases]
        real_hits = [i for i in ids if i.startswith("real-")]
        assert real_hits, f"向量模式应命中真实案例，实际 {ids}"
        assert all(0.0 < c["similarity"] <= 1.0 for c in data.similar_cases), "相似度应 ∈ (0,1]"
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
        if saved_emb is None:
            os.environ.pop("EMBEDDING_PROVIDER", None)
        else:
            os.environ["EMBEDDING_PROVIDER"] = saved_emb
    return f"向量模式命中真实案例 {real_hits}（bge-m3，相似度∈(0,1]）"


# ═══════════════════════════════════════════════════════════════════════════
# R9：真实案例图嵌入幂等（embed --real×2 + 向量索引 + 21 条 × 1024 维）
# ═══════════════════════════════════════════════════════════════════════════


def test_real_embedding_pipeline() -> str:
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
            pass
    except Exception:
        return "SKIP（Ollama 不可达，嵌入用例跳过）"

    env = os.environ.copy()
    script = AGENT1_ROOT / "deploy" / "neo4j" / "scripts" / "embed_cases.py"
    for _ in range(2):
        r = subprocess.run(
            [sys.executable, str(script), "--real"], cwd=AGENT1_ROOT, env=env,
            capture_output=True, text=True, timeout=240,
        )
        assert r.returncode == 0, f"embed --real 失败: {r.stderr[-400:]}"

    d = _driver()
    try:
        with d.session() as s:
            idx = s.run(
                "SHOW VECTOR INDEXES YIELD name WHERE name = 'real_accident_case_embedding' "
                "RETURN count(*) AS n"
            ).single()["n"]
            assert idx == 1, "向量索引 real_accident_case_embedding 应存在"
            emb = s.run(
                "MATCH (c:RealAccidentCase) WHERE c.embedding IS NOT NULL RETURN count(*) AS n"
            ).single()["n"]
            assert emb == 21, f"应有 21 条真实案例嵌入，实际 {emb}"
            dims = s.run(
                "MATCH (c:RealAccidentCase) WHERE c.embedding IS NOT NULL "
                "RETURN size(c.embedding) AS d LIMIT 1"
            ).single()["d"]
            assert dims == 1024, f"嵌入维度应 1024，实际 {dims}"
    finally:
        d.close()
    return "真实案例嵌入幂等（×2）+ 向量索引存在 + 21 条 × 1024 维"


# ═══════════════════════════════════════════════════════════════════════════
# R10：摄取清单.json 与 real YAML case_id 集合一致（21 份）
# ═══════════════════════════════════════════════════════════════════════════


def test_ingest_manifest_consistent() -> str:
    import json
    if not INGEST_MANIFEST.exists():
        return "SKIP（摄取清单.json 不存在，待更新后验证）"
    manifest = json.loads(INGEST_MANIFEST.read_text(encoding="utf-8"))
    yaml_ids = sorted(c["case_id"] for c in yaml.safe_load(REAL_CASES_YAML.read_text(encoding="utf-8"))["cases"])
    # 摄取清单 case_id 来源：accident_case_files（codex 为文件名列表，real-YYYY-NNN.yaml）
    manifest_ids = sorted(f.rsplit("/", 1)[-1].removesuffix(".yaml")
                          for f in manifest.get("accident_case_files", []))
    assert manifest.get("accident_case_count") == 21, \
        f"摄取清单 accident_case_count 应 21，实际 {manifest.get('accident_case_count')}"
    assert manifest_ids == yaml_ids, f"摄取清单 case_id 与 real YAML 不一致:\n清单独有={set(manifest_ids) - set(yaml_ids)}\nYAML独有={set(yaml_ids) - set(manifest_ids)}"
    exp = manifest.get("expected_after_import", {})
    assert exp.get("RealAccidentCase") == 21, \
        f"摄取清单 expected_after_import.RealAccidentCase 应 21，实际 {exp.get('RealAccidentCase')}"
    return "摄取清单.json：accident_case_count=21 + case_id 集合 = real YAML + expected RealAccidentCase=21"


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 60)
    print("  真实数据入系统验收（real_accident_cases.yaml，21 份）")
    print(f"  REAL_CASES = {os.getenv('REAL_CASES')} / NEO4J_URI = {NEO4J_URI}")
    print("=" * 60)

    cases = [
        ("R1. 真实数据域 YAML 完整性", test_real_yaml_integrity),
        ("R2. 等级/年份分布契约", test_risk_distribution),
        ("R3. seed --real 摄取幂等", test_seed_real_idempotent),
        ("R4. 仿真基线守护", test_simulation_baseline_preserved),
        ("R5. 真实数据凭证入图", test_real_case_evidence),
        ("R6. REAL_CASES=on 双轨全序一致", test_real_dualtrack_consistent),
        ("R7. 图谱模式真实案例检索", test_real_retrieval_red),
        ("R8. 向量模式真实案例检索", test_real_vector_retrieval),
        ("R9. 真实案例图嵌入幂等", test_real_embedding_pipeline),
        ("R10. 摄取清单一致性", test_ingest_manifest_consistent),
    ]

    passed = skipped = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            if detail.startswith("SKIP"):
                skipped += 1
                print(f"  [SKIP] {name}  —  {detail}")
            else:
                passed += 1
                print(f"  [PASS] {name}  —  {detail}")
        except Exception as exc:  # noqa: BLE001 — 验收脚本聚合展示
            failed += 1
            print(f"  [FAIL] {name}  —  {type(exc).__name__}: {str(exc)[:220]}")

    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {skipped} SKIP / {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
