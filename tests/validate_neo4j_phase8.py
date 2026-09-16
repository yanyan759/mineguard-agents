"""阶段 8 Neo4j 灾变知识图谱集成验收脚本。

运行：cd agent1 && python tests/validate_neo4j_phase8.py
前置：Neo4j 容器已启动（agent1/deploy/neo4j/docker-compose.yaml，端口 7687，密码 agent1_dev）

12 个用例：
  1. Neo4j 容器可达（RETURN 1 + 约束/索引存在）
  2. 种子摄取幂等（seed×2 计数不变；RiskLevel severity 就位；case_id 集合 = YAML；--reset 重建一致）
  3. 配置驱动双轨（设 NEO4J_URI → 图谱后端；未设 → YAML 后端；similar_cases / support_recs / regulations / missing_topics 全序一致）
  4. 检索契约不变（图谱模式经节点产出的 KnowledgeRetrievalResult：actions/source 非空、similarity∈[0,1]、regulations=10、red→2 相似、evidence=neo4j_kg）
  5. 覆盖语义（missing_knowledge 场景图谱模式 → confidence≤0.7、status=partial、missing_topics 非空）
  6. 全量回归（bootstrap + validate_workflow 7 场景，均以图谱模式子进程运行）
  7. 失败表面（NEO4J_URI 配置但不可达：编译期 probe 抛 RuntimeError；运行期节点返回 status=failed，不静默回退 YAML）
  8. 审核门+写回落图（子步③：approved 完整 SPO 落图+属性齐备；pending/rejected/缺 object 拒写；MERGE 幂等）
  9. 反思候选钩子（子步③：反思节点产出 pending_review 候选、缺 object、带来源、不自动审核）
  10. 双轨写回（子步③：YAML 模式 skipped 候选保留；图谱模式 7/7 枚举白名单拒写非法类型）
  11. 图嵌入摄取幂等（子步②：embed_cases×2 幂等 + 向量索引存在 + 5 条 × 1024 维，前置 Ollama bge-m3）
  12. 向量相似度语义（子步②：EMBEDDING_PROVIDER 时 Q1' 生效，相似度∈(0,1] + 结果 ⊆ red 池）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# 导入设置：agent1/ 在 sys.path 上才能解析 from app.xxx
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# 本脚本为图谱模式验收，默认注入 NEO4J_URI（已配置则沿用）
NEO4J_URI = os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")

CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "accident_cases.yaml"

import yaml  # noqa: E402

from app.agent.knowledge_sources import KnowledgeSourceError  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# 辅助：missing_knowledge 场景 → 感知 → 知识检索 节点链（图谱模式）
# ═══════════════════════════════════════════════════════════════════════════


def _run_missing_knowledge():
    """跑感知预警 + 知识检索两节点，返回 KnowledgeRetrievalResult（图谱模式）。"""
    from app.agent.fixture_loader import FixtureDataSource
    from app.agent.workflow_nodes import (
        node_knowledge_retrieval,
        node_perception_warning,
    )
    from app.agent.workflow_state import new_workflow_state

    loader = FixtureDataSource()
    scenario = loader.load_scenario("missing_knowledge")
    ctx = scenario.context.model_copy(
        update={"iteration_count": 0, "max_iterations": 2}
    )
    state = new_workflow_state(ctx, scenario_id="missing_knowledge")
    state.update(node_perception_warning(state))
    out = node_knowledge_retrieval(state)
    return out["knowledge_result"]


# ═══════════════════════════════════════════════════════════════════════════
# 用例 1：Neo4j 容器可达
# ═══════════════════════════════════════════════════════════════════════════


def test_neo4j_reachable() -> str:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "agent1_dev")),
    )
    try:
        with driver.session() as session:
            row = session.run("RETURN 1").single()
            assert row[0] == 1, f"RETURN 1 失败: {NEO4J_URI}"
            constraints = {c["name"] for c in session.run("SHOW CONSTRAINTS")}
            need = {"accident_case_id", "regulation_id", "risk_level_level", "source_type_type"}
            assert need <= constraints, f"约束缺失: {need - constraints}"
            indexes = {i["name"] for i in session.run("SHOW INDEXES")}
            need_idx = {"accident_case_risk", "accident_case_seq"}
            assert need_idx <= indexes, f"索引缺失: {need_idx - indexes}"
    finally:
        driver.close()
    return "RETURN 1 + 4 约束 + 2 索引就绪"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 2：种子摄取幂等
# ═══════════════════════════════════════════════════════════════════════════


def test_seed_idempotent() -> str:
    from neo4j import GraphDatabase

    env = os.environ.copy()
    env["NEO4J_URI"] = NEO4J_URI
    script = AGENT1_ROOT / "deploy" / "neo4j" / "scripts" / "seed_from_yaml.py"

    def run_seed(*extra: str) -> None:
        r = subprocess.run(
            [sys.executable, str(script), *extra], cwd=AGENT1_ROOT, env=env,
            capture_output=True, text=True, timeout=180,
        )
        assert r.returncode == 0, f"seed 失败: {r.stderr[-500:]}"

    run_seed()  # 第一次摄取
    run_seed()  # 第二次摄取（MERGE 幂等断言）

    driver = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "agent1_dev"))
    try:
        with driver.session() as session:
            def count(label: str) -> int:
                return session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]

            assert count("AccidentCase") == 5, f"AccidentCase 应 5，实际 {count('AccidentCase')}"
            assert count("Regulation") == 10, f"Regulation 应 10，实际 {count('Regulation')}"
            assert count("RiskLevel") == 5, f"RiskLevel 应 5，实际 {count('RiskLevel')}"
            assert count("SourceType") == 4, f"SourceType 应 4，实际 {count('SourceType')}"

            h = session.run("MATCH (:AccidentCase)-[r:HAS_RISK_LEVEL]->(:RiskLevel) RETURN count(r) AS c").single()["c"]
            cls = session.run("MATCH (:Regulation)-[r:CLASSIFIED_AS]->(:SourceType) RETURN count(r) AS c").single()["c"]
            assert h == 5 and cls == 10, f"关系数不符: HAS_RISK_LEVEL={h}, CLASSIFIED_AS={cls}"

            # severity 必须就位（Q1 `severity >=` 过滤依赖）
            sev = {
                row["level"]: row["severity"]
                for row in session.run("MATCH (l:RiskLevel) RETURN l.level AS level, l.severity AS severity")
            }
            assert sev == {"normal": 0, "blue": 1, "yellow": 2, "orange": 3, "red": 4}, f"severity 缺失: {sev}"

            # case_id 集合必须 = YAML 权威源（派生副本漂移检测）
            yaml_ids = {c["case_id"] for c in yaml.safe_load(CASES_YAML.read_text(encoding="utf-8"))["cases"]}
            graph_ids = {row["case_id"] for row in session.run("MATCH (c:AccidentCase) RETURN c.case_id AS case_id")}
            assert graph_ids == yaml_ids, f"case_id 漂移: 图谱独有={graph_ids - yaml_ids}, YAML 独有={yaml_ids - graph_ids}"
    finally:
        driver.close()

    # --reset 清空重建一致性
    run_seed("--reset")
    driver2 = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "agent1_dev"))
    try:
        with driver2.session() as session:
            n = session.run("MATCH (c:AccidentCase) RETURN count(c) AS c").single()["c"]
            assert n == 5, f"--reset 后 AccidentCase 应 5，实际 {n}"
    finally:
        driver2.close()

    # ── 完整图谱态恢复（--reset 只重建仿真基线，须再灌真实案例 + UIE 知识实体）──
    # 之前版本此处在 --reset 后直接结束，会把 seed --real 已灌入的真实案例与
    # import_uie_csv 已写回的 UIE 知识实体覆盖掉，导致 validate_real_data R1-R10
    # 失败 / 图谱缺失标注知识实体。
    # 修复：--reset 验证后立即重新摄取真实案例 + 恢复 UIE 标注域 + 重嵌真实向量，
    # 使验收结束时的图谱保持「仿真基线 + 真实案例 + UIE 知识」完整激活态。
    run_seed("--reset", "--real", "--uie")
    embed_script = AGENT1_ROOT / "deploy" / "neo4j" / "scripts" / "embed_cases.py"
    r = subprocess.run(
        [sys.executable, str(embed_script), "--real"], cwd=AGENT1_ROOT, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, f"embed --real 失败: {r.stderr[-500:]}"
    driver3 = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "agent1_dev"))
    try:
        with driver3.session() as session:
            real_n = session.run(
                "MATCH (c:RealAccidentCase) RETURN count(c) AS c").single()["c"]
            assert real_n == 21, f"真实案例应 21，实际 {real_n}"
            uie_n = session.run(
                "MATCH (n:KnowledgeEntity) RETURN count(n) AS c").single()["c"]
            assert uie_n >= 170, f"UIE 知识实体应 ≥170，实际 {uie_n}"
    finally:
        driver3.close()
    return "seed×2 幂等 + severity 就位 + case_id 集合 = YAML + --reset 重建一致 + 完整图谱态恢复"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 3：配置驱动双轨（无感替换等价性）
# ═══════════════════════════════════════════════════════════════════════════


def test_config_driven_dualtrack() -> str:
    from app.agent.knowledge_sources import retrieve_knowledge
    from app.schemas.workflow import RiskLevel

    saved = os.environ.get("NEO4J_URI")
    try:
        # 未配置 → YAML 后端
        os.environ.pop("NEO4J_URI", None)
        yaml_data = retrieve_knowledge(
            risk_level=RiskLevel.RED, warning_id="w-test", factors=[], mine_context=None
        )
        assert yaml_data.source_type == "fixture_yaml", "未设 NEO4J_URI 应回退 YAML 后端"

        # 已配置 → Neo4j 图谱后端
        os.environ["NEO4J_URI"] = NEO4J_URI
        graph_data = retrieve_knowledge(
            risk_level=RiskLevel.RED, warning_id="w-test", factors=[], mine_context=None
        )
        assert graph_data.source_type == "neo4j_kg", "设 NEO4J_URI 应走图谱后端"

        # 全序一致（case_id / similarity / actions；scheme_id 序列；regulations；missing_topics）
        ysim = [(c["case_id"], c["similarity"], c["actions"]) for c in yaml_data.similar_cases]
        gsim = [(c["case_id"], c["similarity"], c["actions"]) for c in graph_data.similar_cases]
        assert ysim == gsim, f"similar_cases 不一致:\nYAML={ysim}\nKG={gsim}"

        ysup = [s["scheme_id"] for s in yaml_data.support_recommendations]
        gsup = [s["scheme_id"] for s in graph_data.support_recommendations]
        assert ysup == gsup, f"support_recommendations 不一致:\nYAML={ysup}\nKG={gsup}"

        assert yaml_data.regulations == graph_data.regulations, "regulations 不一致"
        assert yaml_data.missing_topics == graph_data.missing_topics, "missing_topics 不一致"
    finally:
        if saved is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved
    return "双轨等价：similar_cases + support_recs + regulations + missing_topics 全序一致"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 4：检索契约不变（图谱模式经节点产出）
# ═══════════════════════════════════════════════════════════════════════════


def test_retrieval_contract() -> str:
    kr = _run_missing_knowledge()

    # red 当前 → 仅 2 条相似（severity>=4 案例数，实证）
    assert len(kr.similar_cases) == 2, f"red 应 2 条相似，实际 {len(kr.similar_cases)}"
    assert all(s.actions for s in kr.similar_cases), "similar_cases 的 actions 不应为空"
    assert all(s.source for s in kr.similar_cases), "similar_cases 的 source（来源）不应为空（安全约束）"
    assert all(0.0 <= s.similarity <= 1.0 for s in kr.similar_cases), "similarity 应在 [0,1]"
    assert len(kr.regulations) == 10, f"regulations 应 10，实际 {len(kr.regulations)}"
    assert all(reg.source for reg in kr.regulations), "regulations 的 source 不应为空"
    assert kr.evidence and kr.evidence[0].source_type == "neo4j_kg", "evidence 来源应标注 neo4j_kg"
    return "red→2 相似 / regulations=10 / actions·source 非空 / similarity∈[0,1] / evidence=neo4j_kg"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 5：覆盖语义（missing_knowledge 图谱模式）
# ═══════════════════════════════════════════════════════════════════════════


def test_coverage_semantics() -> str:
    kr = _run_missing_knowledge()

    assert (kr.confidence or 0) <= 0.7, f"覆盖不足时 confidence 应 ≤0.7，实际 {kr.confidence}"
    assert kr.status.value == "partial", f"覆盖不足时 status 应 partial，实际 {kr.status.value}"
    assert len(kr.missing_topics) > 0, "missing_topics 不应为空"
    assert "高相似度历史案例不足" in kr.missing_topics, "missing_topics 应含『高相似度历史案例不足』"
    return "missing_knowledge：confidence≤0.7 / status=partial / missing 含『高相似度历史案例不足』"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 6：全量回归（图谱模式子进程）
# ═══════════════════════════════════════════════════════════════════════════


def test_full_regression() -> str:
    env = os.environ.copy()
    env["NEO4J_URI"] = NEO4J_URI

    # 6a. bootstrap 全链路（导入 + 规则引擎 + LLM）
    r1 = subprocess.run(
        [sys.executable, "bootstrap.py"], cwd=AGENT1_ROOT, env=env,
        capture_output=True, text=True, timeout=180,
    )
    assert r1.returncode == 0, f"bootstrap 失败: {r1.stderr[-500:]}"

    # 6b. validate_workflow 7 场景（图谱模式，无感替换端到端）
    r2 = subprocess.run(
        [sys.executable, "tests/validate_workflow.py"], cwd=AGENT1_ROOT, env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert r2.returncode == 0, f"validate_workflow 失败: {r2.stderr[-500:]}"
    return "bootstrap + validate_workflow(7 场景) 图谱模式全过"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 7：失败表面（NEO4J_URI 配置但不可达）
# ═══════════════════════════════════════════════════════════════════════════


def test_failure_surface() -> str:
    from app.agent.knowledge_sources import probe_neo4j, retrieve_knowledge
    from app.schemas.workflow import RiskLevel

    saved = os.environ.get("NEO4J_URI")
    try:
        os.environ["NEO4J_URI"] = "bolt://localhost:16999"  # 不可达端口（ECONNREFUSED 快速失败）

        # 7a. 编译期探测 → RuntimeError（fail-loudly，对齐 get_checkpointer 先例）
        try:
            probe_neo4j()
        except RuntimeError as exc:
            assert "Neo4j" in str(exc), f"RuntimeError 消息应含 Neo4j: {exc}"
        else:
            raise AssertionError("NEO4J_URI 配置但不可达时应抛 RuntimeError")

        # 7b. 检索入口 → KnowledgeSourceError（运行期异常包装）
        try:
            retrieve_knowledge(risk_level=RiskLevel.RED, warning_id="w", factors=[], mine_context=None)
        except KnowledgeSourceError:
            pass
        else:
            raise AssertionError("不可达时应抛 KnowledgeSourceError")

        # 7c. 节点捕获 → status=failed 合法结果（不静默回退 YAML）
        kr = _run_missing_knowledge()
        assert kr.status.value == "failed", f"期望 failed，实际 {kr.status.value}"
        assert kr.errors and "Neo4j" in kr.errors[0], f"errors 应含 Neo4j: {kr.errors}"
        assert kr.evidence and kr.evidence[0].source_type == "neo4j_kg"
        assert kr.similar_cases == [], "失败时应无相似案例"
    finally:
        if saved is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved
    return "编译期 RuntimeError / 运行期 KnowledgeSourceError / 节点 status=failed，不静默回退"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 11：图嵌入摄取幂等 + 向量索引（子步②，前置 Ollama bge-m3）
# ═══════════════════════════════════════════════════════════════════════════


def _vector_env() -> dict:
    env = os.environ.copy()
    env["EMBEDDING_PROVIDER"] = "ollama"
    env["EMBEDDING_BASE_URL"] = "http://localhost:11434"
    env["EMBEDDING_MODEL"] = "bge-m3:567m"
    return env


def test_embedding_pipeline() -> str:
    """子步② 用例 11：embed_cases 幂等 + 向量索引存在 + 5 条 × 1024 维。"""
    from neo4j import GraphDatabase

    env = _vector_env()
    for _ in range(2):  # 连续跑两次：第二次验证幂等（脚本内部跳过已有嵌入）
        r = subprocess.run(
            [sys.executable, "deploy/neo4j/scripts/embed_cases.py"],
            cwd=AGENT1_ROOT, env=env, capture_output=True, text=True, timeout=240,
        )
        assert r.returncode == 0, f"embed_cases 失败: {r.stderr[-400:]}"

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "agent1_dev")),
    )
    try:
        with driver.session() as session:
            idx = session.run(
                "SHOW VECTOR INDEXES YIELD name WHERE name = 'accident_case_embedding' "
                "RETURN count(*) AS n"
            ).single()["n"]
            assert idx == 1, "向量索引 accident_case_embedding 应存在"
            emb = session.run(
                "MATCH (c:AccidentCase) WHERE c.embedding IS NOT NULL RETURN count(*) AS n"
            ).single()["n"]
            assert emb == 5, f"应有 5 条嵌入，实际 {emb}"
            dims = session.run(
                "MATCH (c:AccidentCase) WHERE c.embedding IS NOT NULL "
                "RETURN size(c.embedding) AS d LIMIT 1"
            ).single()["d"]
            assert dims == 1024, f"嵌入维度应 1024，实际 {dims}"
    finally:
        driver.close()
    return "嵌入幂等（×2）+ 向量索引存在 + 5 条 × 1024 维"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 12：向量相似度语义（子步②，EMBEDDING_PROVIDER 配置时 Q1' 生效）
# ═══════════════════════════════════════════════════════════════════════════


def test_vector_similarity() -> str:
    """子步② 用例 12：向量相似度 ∈(0,1] + 结果 ⊆ severity 池 + 得分非硬编码。"""
    from app.agent.knowledge_sources import retrieve_knowledge
    from app.schemas.workflow import RiskLevel

    saved_uri = os.environ.get("NEO4J_URI")
    saved_emb = os.environ.get("EMBEDDING_PROVIDER")
    try:
        os.environ["NEO4J_URI"] = NEO4J_URI
        os.environ["EMBEDDING_PROVIDER"] = "ollama"
        data = retrieve_knowledge(
            risk_level=RiskLevel.RED, warning_id="w-vec",
            factors=["微震能量异常", "顶板断裂"], mine_context=None,
        )
        sims = data.similar_cases
        assert len(sims) > 0, "向量模式应返回相似案例"
        assert all(0.0 < s["similarity"] <= 1.0 for s in sims), "相似度应 ∈ (0,1]"
        # 结果 ⊆ severity>=4（red）池：不扩大案例池
        yaml_cases = yaml.safe_load(CASES_YAML.read_text(encoding="utf-8")).get("cases", [])
        red_ids = {c["case_id"] for c in yaml_cases if c.get("risk_level") == "red"}
        got_ids = {s["case_id"] for s in sims}
        assert got_ids <= red_ids, f"向量结果越出 red 池: {got_ids - red_ids}"
        # 得分非全部硬编码 0.7（证明走了真实向量）
        scores = {s["similarity"] for s in sims}
        assert not (scores == {0.7}), f"相似度不应全为硬编码 0.7，实际 {scores}"
    finally:
        if saved_uri is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved_uri
        if saved_emb is None:
            os.environ.pop("EMBEDDING_PROVIDER", None)
        else:
            os.environ["EMBEDDING_PROVIDER"] = saved_emb
    return "向量相似度 ∈(0,1] + 结果 ⊆ red 池 + 得分非硬编码"


# ═══════════════════════════════════════════════════════════════════════════
# 辅助：写回验收（子步③，设计 §8）
# ═══════════════════════════════════════════════════════════════════════════


def _approved_entry(seed: int, review_status: str = "approved", **overrides) -> dict:
    """构造人工审核后的结构化候选（§8.1 允许的『人工结构化候选』路径）。

    approved 由人工审核产生——验收脚本模拟审核界面输出，节点绝不自动审核。
    """
    entry = {
        "candidate_id": f"kc-test-{seed}",
        "entity": f"WB-TEST-致灾因子-{seed}",
        "entity_type": "顶板灾变",
        "relation": "诱发",
        "object": f"WB-TEST-对象-{seed}",
        "object_type": "监测指标",
        "evidence": "验收脚本构造的测试证据片段",
        "source": "validate_neo4j_phase8",
        "confidence": 0.6,
        "review_status": review_status,
        "reviewed_by": "tester",
        "reviewed_at": "2026-08-11T00:00:00+00:00",
        "review_comment": "验收脚本人工审核模拟",
    }
    entry.update(overrides)
    return entry


def _writeback_state(candidates: list[dict]) -> dict:
    """构造写回节点输入状态：感知→反思后，将反思候选替换为审核后混合候选。"""
    from app.agent.fixture_loader import FixtureDataSource
    from app.agent.workflow_nodes import (
        node_perception_warning, node_reflection_iteration,
    )
    from app.agent.workflow_state import new_workflow_state

    loader = FixtureDataSource()
    ctx = loader.load_scenario("missing_knowledge").context.model_copy(
        update={"iteration_count": 0, "max_iterations": 2})
    state = new_workflow_state(ctx, scenario_id="missing_knowledge")
    state.update(node_perception_warning(state))
    state.update(node_reflection_iteration(state))
    state["reflection_result"] = state["reflection_result"].model_copy(
        update={"knowledge_candidates": candidates})
    return state


def _cleanup_test_entities() -> None:
    """清理验收写回层测试节点（仅 WB-TEST 前缀，不影响种子图）。"""
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "agent1_dev"))
    try:
        with driver.session() as session:
            session.run(
                "MATCH (e:KnowledgeEntity) WHERE e.name STARTS WITH 'WB-TEST' "
                "DETACH DELETE e"
            ).consume()
    finally:
        driver.close()


# ═══════════════════════════════════════════════════════════════════════════
# 用例 8：审核门状态机 + 写回落图 + 幂等（设计 §8.2/§8.4/§8.5）
# ═══════════════════════════════════════════════════════════════════════════


def test_writeback_gate_and_graph() -> str:
    from app.agent.workflow_nodes import node_knowledge_write_back

    # 4 类候选：approved 完整 SPO / pending_review / rejected / approved 缺 object
    approved = _approved_entry(1)
    pending = _approved_entry(2, review_status="pending_review")
    rejected = _approved_entry(3, review_status="rejected", review_comment="证据不足")
    incomplete = _approved_entry(4, object=None)  # approved 但缺 object → 存储层拒写

    state = _writeback_state([approved, pending, rejected, incomplete])
    out = node_knowledge_write_back(state)
    result = out["knowledge_writeback_result"]

    assert result["status"] == "partial", f"任一候选被拒应 partial，实际 {result['status']}"
    assert result["data"]["written"] == 1, f"应写回 1 条，实际 {result['data']}"
    assert len(result["data"]["skipped"]) == 1, "pending_review 应保留审核队列"
    assert len(result["data"]["rejected"]) == 2, f"应 2 拒写（rejected 审核拒绝 + 缺 object 存储拒写），实际 {result['data']['rejected']}"
    reasons = {r["reason"] for r in result["data"]["rejected"]}
    assert any("证据不足" in r for r in reasons), "rejected 候选应记录拒绝原因"
    assert any("SPO" in r for r in reasons), "缺 object 候选应由存储层拒写"

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "agent1_dev"))
    try:
        with driver.session() as session:
            # 已写三元组 + 属性齐备（安全约束：来源/审核人/置信度/证据）
            row = session.run(
                "MATCH (s:KnowledgeEntity {name:'WB-TEST-致灾因子-1'})-[r:诱发]->"
                "(o:KnowledgeEntity {name:'WB-TEST-对象-1'}) "
                "RETURN r.review_status AS rs, r.reviewed_by AS rb, "
                "r.confidence AS conf, r.evidence_text AS ev, o.entity_type AS ot"
            ).single()
            assert row is not None, "approved 三元组应落图"
            assert row["rs"] == "approved" and row["rb"] == "tester"
            assert row["conf"] == 0.6 and row["ev"] and row["ot"] == "监测指标"
            # 幂等：再跑一次节点（MERGE 去重）
            node_knowledge_write_back(state)
            cnt = session.run(
                "MATCH (s:KnowledgeEntity {name:'WB-TEST-致灾因子-1'})-[r:诱发]->"
                "(o:KnowledgeEntity {name:'WB-TEST-对象-1'}) RETURN count(r) AS c"
            ).single()["c"]
            assert cnt == 1, f"幂等失效，重复写回 {cnt} 条"
            # pending/rejected/缺 object 均不得落图
            n = session.run(
                "MATCH (e:KnowledgeEntity) WHERE e.name IN "
                "['WB-TEST-致灾因子-2','WB-TEST-致灾因子-3','WB-TEST-致灾因子-4'] "
                "RETURN count(e) AS c"
            ).single()["c"]
            assert n == 0, f"非 approved/缺 SPO 候选不应落图: {n}"
    finally:
        driver.close()
    _cleanup_test_entities()
    return "approved 落图+属性齐备 / pending·rejected·缺SPO 拒写 / MERGE 幂等"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 9：反思产出→候选池钩子（治理断言：不自动审核）
# ═══════════════════════════════════════════════════════════════════════════


def test_reflection_candidate_hook() -> str:
    from app.agent.fixture_loader import FixtureDataSource
    from app.agent.workflow_nodes import node_perception_warning, node_reflection_iteration
    from app.agent.workflow_state import new_workflow_state

    loader = FixtureDataSource()
    ctx = loader.load_scenario("missing_knowledge").context.model_copy(
        update={"iteration_count": 0, "max_iterations": 2})
    state = new_workflow_state(ctx, scenario_id="missing_knowledge")
    state.update(node_perception_warning(state))
    state.update(node_reflection_iteration(state))
    candidates = state["reflection_result"].knowledge_candidates

    assert candidates, "反思节点应产出知识候选（钩子生效）"
    assert all(c["review_status"] == "pending_review" for c in candidates), "反思不得自动审核"
    assert all("object" not in c for c in candidates), "候选缺 object（非完整 SPO，待 UIE/人工补齐）"
    assert all(c.get("entity") and c.get("source") for c in candidates), "候选须带来源（安全约束）"
    return f"反思产出 {len(candidates)} 条 pending_review 候选 + 缺 object + 带来源 + 不自动审核"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 10：双轨写回（YAML 模式 skip + 图谱模式 7/7 白名单拒写）
# ═══════════════════════════════════════════════════════════════════════════


def test_writeback_dualtrack() -> str:
    from app.agent.knowledge_sources import write_knowledge_to_graph

    saved = os.environ.get("NEO4J_URI")
    try:
        # 10a. YAML 模式：未设 NEO4J_URI → skipped，候选保留审核队列，不落图
        os.environ.pop("NEO4J_URI", None)
        r1 = write_knowledge_to_graph([_approved_entry(5)])
        assert r1["status"] == "skipped", f"YAML 模式应 skipped，实际 {r1['status']}"
        assert r1["data"]["written"] == 0

        # 10b. 图谱模式：7/7 枚举白名单拒写非法类型/关系（防注入 + 域约束）
        os.environ["NEO4J_URI"] = NEO4J_URI
        bad = _approved_entry(6, entity_type="非法类型", relation="未知关系")
        r2 = write_knowledge_to_graph([bad])
        assert r2["status"] == "partial" and r2["data"]["written"] == 0
        assert len(r2["data"]["rejected"]) == 1 and "枚举" in r2["data"]["rejected"][0]["reason"]
    finally:
        if saved is None:
            os.environ.pop("NEO4J_URI", None)
        else:
            os.environ["NEO4J_URI"] = saved
    return "YAML 模式 skip（候选保留） + 图谱模式 7/7 枚举白名单拒写"


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 60)
    print("  阶段 8 — Neo4j 灾变知识图谱集成验收")
    print(f"  NEO4J_URI = {NEO4J_URI}")
    print("=" * 60)

    cases = [
        ("1. Neo4j 容器可达", test_neo4j_reachable),
        ("2. 种子摄取幂等", test_seed_idempotent),
        ("3. 配置驱动双轨", test_config_driven_dualtrack),
        ("4. 检索契约不变", test_retrieval_contract),
        ("5. 覆盖语义", test_coverage_semantics),
        ("6. 全量回归", test_full_regression),
        ("7. 失败表面", test_failure_surface),
        ("11. 图嵌入摄取幂等", test_embedding_pipeline),
        ("12. 向量相似度语义", test_vector_similarity),
        ("8. 审核门+写回落图", test_writeback_gate_and_graph),
        ("9. 反思候选钩子", test_reflection_candidate_hook),
        ("10. 双轨写回", test_writeback_dualtrack),
    ]

    passed = 0
    failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            passed += 1
            print(f"  [PASS] {name}  —  {detail}")
        except Exception as exc:  # noqa: BLE001 — 验收脚本聚合展示
            failed += 1
            print(f"  [FAIL] {name}  —  {type(exc).__name__}: {str(exc)[:200]}")

    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
