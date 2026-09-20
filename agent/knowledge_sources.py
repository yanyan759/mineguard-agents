"""知识检索数据源适配器。

配置驱动双轨（设计 §3）：
- NEO4J_URI 未配置 → YAML 后端（现状行为，开发默认）
- NEO4J_URI 已配置   → Neo4j 后端（Cypher 图谱检索）

失败表面（设计 §7.3）：
- 编译期：create_workflow() 调 probe_neo4j()，不可达抛 RuntimeError（对齐 Redis 先例）
- 运行期：Neo4j 驱动异常包装为 KnowledgeSourceError，由知识检索节点转 status=failed 结果

接口（seam）：retrieve_knowledge() 返回数据层 RetrievalData，节点统一构造 Pydantic。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.schemas.workflow import RiskLevel

# Fixture 数据路径（YAML 后端）
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# 第三数据域：支护方案/专家研判独立知识源（不再从事故案例 actions 派生）
_SUPPORT_PLANS_FILENAME = "support_plans.yaml"

# 真实数据域：21 份真实事故案例（real_accident_cases.yaml，与仿真 accident_cases.yaml 分离）。
# REAL_CASES=on 时三后端（YAML/Neo4j 传统/Neo4j 向量）将真实案例并入相似案例池，
# 保持仿真基线（默认 off，12 项 Neo4j 验收不破）；on 时由 validate_real_data.py 验收。
_REAL_CASES_FILENAME = "real_accident_cases.yaml"


def _real_cases_enabled() -> bool:
    """真实数据域开关：REAL_CASES=on 启用（默认关闭，仿真基线行为不变）。"""
    return os.getenv("REAL_CASES") == "on"

# 可选资料库根目录。默认指向被 .gitignore 忽略的本地目录，不随源码发布。
# 通过 MINEGUARD_CORPUS_DIR 显式配置后，才会扫描外部资料并写入证据索引。
_DEFAULT_OPTIONAL_CORPUS_DIR = Path(__file__).resolve().parent.parent / "updated" / "optional_corpus"
_REAL_DOCS_DIR = Path(os.getenv("MINEGUARD_CORPUS_DIR", str(_DEFAULT_OPTIONAL_CORPUS_DIR)))
# 数据域 → 真实资料子目录映射
_REAL_DOCS_DOMAINS = {
    "accident_cases": "coal_mine_accident_cases",
    "regulations": "coal_mine_safety_regulations",
    "support_plans": "coal_mine_support_schemes",
}

# 风险等级排序（对齐 workflow_nodes 原有 _RISK_ORDER，NORMAL=0 ... RED=4）
_RISK_ORDER = {RiskLevel.NORMAL: 0, RiskLevel.BLUE: 1, RiskLevel.YELLOW: 2,
               RiskLevel.ORANGE: 3, RiskLevel.RED: 4}


@dataclass
class RetrievalData:
    """数据层检索结果（节点据此构造 KnowledgeRetrievalResult）。"""
    similar_cases: list[dict] = field(default_factory=list)
    regulations: list[dict] = field(default_factory=list)
    support_recommendations: list[dict] = field(default_factory=list)
    missing_topics: list[str] = field(default_factory=list)
    source_type: str = "fixture_yaml"
    evidence_description: str = ""
    # 真实资料库索引（RAGFlow docs/real 的结构感知，空目录时为空列表）
    real_docs_index: list[dict] = field(default_factory=list)


def _index_real_docs() -> list[dict]:
    """扫描外部资料库的三个域并返回文件索引元数据。

    真实资料由部署方在仓库外准备并导入 RAGFlow；本索引只提供域、文件数和文件名
    的结构感知，不解析正文，也不改变 fixture 检索语义。目录不存在或只有 README
    占位说明时返回空列表，默认源码包因此可以脱离真实语料运行。
    """
    if not _REAL_DOCS_DIR.exists():
        return []
    index = []
    for domain, dirname in _REAL_DOCS_DOMAINS.items():
        d = _REAL_DOCS_DIR / dirname
        if not d.exists():
            continue
        files = sorted(
            p.name for p in d.iterdir()
            if p.is_file() and p.name not in {"README.md", ".gitkeep"}
        )
        if files:
            index.append({
                "domain": domain,
                "dir": dirname,
                "file_count": len(files),
                "files": files[:20],  # 索引截断，完整清单见目录
            })
    return index


class KnowledgeSourceError(RuntimeError):
    """Neo4j 图谱数据源运行期故障（节点捕获后返回 status=failed，不静默回退）。"""


def _load_yaml(filename: str) -> dict:
    return yaml.safe_load((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))


def _risk_order(level: RiskLevel) -> int:
    return _RISK_ORDER.get(level, 0)


def _compute_missing(similar_count: int, support_count: int) -> list[str]:
    """覆盖语义（对齐 validate_phase7 断言），两后端共用。"""
    missing = []
    if similar_count < 3:
        missing.append("高相似度历史案例不足")
    if support_count == 0:
        missing.append("支护方案推荐为空")
    return missing


def _yaml_case_rows(cases_data: dict, risk_level: RiskLevel) -> list[dict]:
    """从 YAML 案例数据提取匹配案例（同等级或更高等级，YAML/seq 序 LIMIT 5）。

    独立为 helper 供仿真/真实 YAML 共用；真实域经 _merge_similar 追加在仿真之后。
    """
    similar = []
    for c in cases_data.get("cases", []):
        c_risk = RiskLevel(c.get("risk_level", "normal"))
        # 同等级或更高等级的案例
        if _risk_order(c_risk) >= _risk_order(risk_level):
            similar.append({
                "case_id": c["case_id"],
                "similarity": 0.7 if c_risk == risk_level else 0.4,
                "causes": c.get("causes", []),
                "actions": c.get("actions", []),
                "outcome": c.get("outcome", ""),
                "source": c.get("source", ""),
            })
        if len(similar) >= 5:
            break
    return similar


def _merge_similar(sim_rows: list[dict], real_rows: list[dict]) -> list[dict]:
    """合并仿真+真实案例池：真实在仿真后，总 LIMIT 5。

    YAML/Neo4j 传统/Neo4j 向量三后端共用，保证 REAL_CASES=on 时双轨全序一致。
    仿真优先的语义：高危场景（red/orange）仿真不足 5 条时真实案例自然补足；
    常规场景（yellow 仿真已满 5 条）结果与仿真模式一致（向后兼容）。
    """
    return (list(sim_rows) + list(real_rows))[:5]


def _yaml_backend(risk_level: RiskLevel) -> RetrievalData:
    """YAML 后端：按当前知识检索契约读取本地资料。

    支护方案推荐改读独立数据源 support_plans.yaml（第三数据域：导师历年支护方案
    + 专家研判），不再从事故案例 actions 派生；真实资料索引并入 evidence。
    """
    cases_data = _load_yaml("accident_cases.yaml")
    regs_data = _load_yaml("regulations.yaml")
    plans_data = _load_yaml(_SUPPORT_PLANS_FILENAME)

    similar = _yaml_case_rows(cases_data, risk_level)
    # 真实数据域（REAL_CASES=on）：真实案例追加在仿真之后（保高危场景覆盖）
    if _real_cases_enabled():
        configured_path = os.getenv("MINEGUARD_REAL_CASES_PATH", "").strip()
        real_path = Path(configured_path) if configured_path else None
        if real_path is not None and real_path.is_file():
            real_data = yaml.safe_load(real_path.read_text(encoding="utf-8")) or {}
            similar = _merge_similar(similar, _yaml_case_rows(real_data, risk_level))

    regulations = [{
        "document": r["document"],
        "clause": r.get("clause", ""),
        "requirement": r["requirement"],
        "mandatory": r.get("mandatory", True),
        "source": r.get("source", ""),
    } for r in regs_data.get("regulations", [])]

    # 支护方案推荐：从独立数据源 support_plans.yaml 读取（第三数据域），
    # 含适用条件/局限/专家研判，字段对齐 SupportRecommendation Pydantic。
    support_recs = []
    for p in plans_data.get("support_plans", [])[:3]:
        support_recs.append({
            "scheme_id": p.get("scheme_id", ""),
            "scheme_name": p.get("scheme_name", ""),
            "applicable_conditions": p.get("applicable_conditions", []),
            "actions": p.get("actions", []),
            "limitations": p.get("limitations", []),
            "expert_review": p.get("expert_review", ""),
            "source": p.get("source", ""),
        })

    evidence_src = "accident_cases.yaml + regulations.yaml + support_plans.yaml"
    if _real_cases_enabled():
        evidence_src += " + real_accident_cases.yaml（真实案例域）"
    return RetrievalData(
        similar_cases=similar,
        regulations=regulations,
        support_recommendations=support_recs,
        missing_topics=_compute_missing(len(similar), len(support_recs)),
        source_type="fixture_yaml",
        evidence_description=evidence_src,
        real_docs_index=_index_real_docs(),
    )


# ── 3 条 Cypher（设计 §6.1，复刻 YAML 语义）──
# Q1：severity 过滤 = YAML `_risk_order(c_risk) >= _risk_order(risk_level)`，seq 保序 LIMIT 5
_Q1_SIMILAR = """
MATCH (c:AccidentCase)-[:HAS_RISK_LEVEL]->(l:RiskLevel)
WHERE l.severity >= $curSeverity
RETURN c.case_id AS case_id, c.causes AS causes, c.actions AS actions,
       c.outcome AS outcome, c.source AS source,
       CASE WHEN l.level = $curLevel THEN 0.7 ELSE 0.4 END AS similarity
ORDER BY c.seq
LIMIT 5
"""

# Q1R：真实案例域（RealAccidentCase，--real 摄取）——复刻 Q1 语义（severity 过滤 + seq 保序 LIMIT 5）。
#      REAL_CASES=on 时并入相似案例池（在仿真之后），真实重大/较大事故补足高危场景覆盖。
_Q1_REAL_SIMILAR = """
MATCH (c:RealAccidentCase)-[:HAS_RISK_LEVEL]->(l:RiskLevel)
WHERE l.severity >= $curSeverity
RETURN c.case_id AS case_id, c.causes AS causes, c.actions AS actions,
       c.outcome AS outcome, c.source AS source,
       CASE WHEN l.level = $curLevel THEN 0.7 ELSE 0.4 END AS similarity
ORDER BY c.seq
LIMIT 5
"""

# Q2：全部规程，seq 保序
_Q2_REGULATIONS = """
MATCH (r:Regulation)
RETURN r.document AS document, r.clause AS clause, r.requirement AS requirement,
       r.mandatory AS mandatory, r.source AS source
ORDER BY r.seq
"""

# Q3：支护方案/专家研判 —— 独立 SupportPlan 节点（第三数据域，seed_from_yaml 摄取）
# 先切片（前 3）后过滤（有 actions），与 YAML `support_plans[:3]` + 非空校验一致。
_Q3_SUPPORT = """
MATCH (s:SupportPlan)
WITH s
ORDER BY s.seq
LIMIT 3
WHERE size(s.actions) > 0
RETURN s.scheme_id AS scheme_id, s.scheme_name AS scheme_name,
       s.applicable_conditions AS applicable_conditions,
       s.actions AS actions, s.limitations AS limitations,
       s.expert_review AS expert_review, s.source AS source
"""

# ── 子步② 向量检索（EMBEDDING_PROVIDER 配置时启用）──
# Q1'：向量索引 top-K → 过滤 severity >= 当前 → score DESC LIMIT 5。
#      topK=20 留足过滤后余量；查询文本 = 当前致灾因子（首次真正使用 factors）。
_Q1_SIMILAR_VECTOR = """
CALL db.index.vector.queryNodes('accident_case_embedding', $topK, $queryEmbedding)
YIELD node AS c, score
WHERE EXISTS { MATCH (c)-[:HAS_RISK_LEVEL]->(l:RiskLevel) WHERE l.severity >= $curSeverity }
RETURN c.case_id AS case_id, c.causes AS causes, c.actions AS actions,
       c.outcome AS outcome, c.source AS source,
       toFloat(score) AS similarity
ORDER BY similarity DESC
LIMIT $limit
"""

# Q1R'：真实案例域向量检索（real_accident_case_embedding 索引，embed_cases --real 摄取）——
#      REAL_CASES=on 时并入向量相似池（在仿真之后），复刻 Q1' 语义。
_Q1_SIMILAR_VECTOR_REAL = """
CALL db.index.vector.queryNodes('real_accident_case_embedding', $topK, $queryEmbedding)
YIELD node AS c, score
WHERE EXISTS { MATCH (c)-[:HAS_RISK_LEVEL]->(l:RiskLevel) WHERE l.severity >= $curSeverity }
RETURN c.case_id AS case_id, c.causes AS causes, c.actions AS actions,
       c.outcome AS outcome, c.source AS source,
       toFloat(score) AS similarity
ORDER BY similarity DESC
LIMIT $limit
"""


def _clamp01(x: float) -> float:
    """余弦得分钳制到 [0,1]（bge-m3 对文本余弦非负，兜底负值）。"""
    return max(0.0, min(1.0, x))


def _query_embedding_text(factors: list[str] | None) -> str:
    """查询文本 = 当前致灾因子；为空退化为通用灾害查询。"""
    parts = [f for f in (factors or []) if f]
    return "。".join(parts) if parts else "煤矿顶板灾变 微震异常 顶板断裂 风险"


def _embed_query(text: str) -> list[float]:
    """单文本嵌入（Ollama /api/embed），失败抛 KnowledgeSourceError（不静默回退）。"""
    import json as _json
    import urllib.request as _urllib

    base = os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434")
    model = os.getenv("EMBEDDING_MODEL", "bge-m3:567m")
    dim = int(os.getenv("EMBEDDING_DIM", "1024"))
    payload = _json.dumps({"model": model, "input": [text]}).encode()
    req = _urllib.Request(
        f"{base}/api/embed", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with _urllib.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read().decode())
    except Exception as exc:
        raise KnowledgeSourceError(f"嵌入服务不可达（{base}）: {exc}") from exc
    embs = data.get("embeddings", [])
    if not embs or len(embs[0]) != dim:
        raise KnowledgeSourceError(f"嵌入维度异常：期望 {dim}，实际 {len(embs[0]) if embs else 0}")
    return embs[0]


def _neo4j_backend(risk_level: RiskLevel) -> RetrievalData:
    """Neo4j 后端：3 条 Cypher 复刻 YAML 语义（无感替换）。"""
    from neo4j import GraphDatabase  # 延迟导入：YAML 模式无需 neo4j 包

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "agent1_dev")
    cur_severity = _risk_order(risk_level)
    cur_level = risk_level.value

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session() as session:
                similar_rows = session.run(
                    _Q1_SIMILAR, curSeverity=cur_severity, curLevel=cur_level
                ).data()
                # 真实数据域（REAL_CASES=on）：Q1R 查 RealAccidentCase 并合并（仿真之后）
                if _real_cases_enabled():
                    real_rows = session.run(
                        _Q1_REAL_SIMILAR, curSeverity=cur_severity, curLevel=cur_level
                    ).data()
                    similar_rows = _merge_similar(similar_rows, real_rows)
                reg_rows = session.run(_Q2_REGULATIONS).data()
                support_rows = session.run(_Q3_SUPPORT).data()
        finally:
            driver.close()
    except Exception as exc:
        # 运行期故障 → KnowledgeSourceError，节点捕获后转 status=failed（不静默回退 YAML）
        raise KnowledgeSourceError(f"Neo4j 查询失败（{uri}）: {exc}") from exc

    similar = [{
        "case_id": r["case_id"],
        "similarity": float(r["similarity"]),
        "causes": r.get("causes") or [],
        "actions": r.get("actions") or [],
        "outcome": r.get("outcome") or "",
        "source": r.get("source", ""),
    } for r in similar_rows]

    regulations = [{
        "document": r["document"],
        "clause": r.get("clause") or "",
        "requirement": r["requirement"],
        "mandatory": bool(r["mandatory"]),
        "source": r.get("source", ""),
    } for r in reg_rows]

    support_recs = [{
        "scheme_id": r.get("scheme_id", ""),
        "scheme_name": r.get("scheme_name", ""),
        "applicable_conditions": r.get("applicable_conditions") or [],
        "actions": r.get("actions") or [],
        "limitations": r.get("limitations") or [],
        "expert_review": r.get("expert_review", ""),
        "source": r.get("source", ""),
    } for r in support_rows]

    evidence_note = "（含真实案例域）" if _real_cases_enabled() else ""
    return RetrievalData(
        similar_cases=similar,
        regulations=regulations,
        support_recommendations=support_recs,
        missing_topics=_compute_missing(len(similar), len(support_recs)),
        source_type="neo4j_kg",
        evidence_description=(
            f"Neo4j 知识图谱{evidence_note}：{len(similar)} 案例、{len(regulations)} 规程、{len(support_recs)} 支护方案"
        ),
        real_docs_index=_index_real_docs(),
    )


def _neo4j_vector_backend(
    risk_level: RiskLevel,
    factors: list[str] | None = None,
) -> RetrievalData:
    """Neo4j 向量后端：Q1'（bge-m3 余弦相似度）+ Q2/Q3（与 YAML 语义一致）。"""
    from neo4j import GraphDatabase  # 延迟导入

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "agent1_dev")
    cur_severity = _risk_order(risk_level)
    query_emb = _embed_query(_query_embedding_text(factors))

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session() as session:
                similar_rows = session.run(
                    _Q1_SIMILAR_VECTOR,
                    topK=20, curSeverity=cur_severity,
                    queryEmbedding=query_emb, limit=5,
                ).data()
                # 真实数据域（REAL_CASES=on）：Q1R' 向量查真实案例并合并（仿真之后）
                if _real_cases_enabled():
                    real_rows = session.run(
                        _Q1_SIMILAR_VECTOR_REAL,
                        topK=20, curSeverity=cur_severity,
                        queryEmbedding=query_emb, limit=5,
                    ).data()
                    similar_rows = _merge_similar(similar_rows, real_rows)
                reg_rows = session.run(_Q2_REGULATIONS).data()
                support_rows = session.run(_Q3_SUPPORT).data()
        finally:
            driver.close()
    except KnowledgeSourceError:
        raise
    except Exception as exc:
        raise KnowledgeSourceError(f"Neo4j 向量查询失败（{uri}）: {exc}") from exc

    similar = [{
        "case_id": r["case_id"],
        "similarity": _clamp01(float(r["similarity"])),
        "causes": r.get("causes") or [],
        "actions": r.get("actions") or [],
        "outcome": r.get("outcome") or "",
        "source": r.get("source", ""),
    } for r in similar_rows]

    regulations = [{
        "document": r["document"],
        "clause": r.get("clause", ""),
        "requirement": r["requirement"],
        "mandatory": bool(r["mandatory"]),
        "source": r.get("source", ""),
    } for r in reg_rows]

    support_recs = [{
        "scheme_id": r.get("scheme_id", ""),
        "scheme_name": r.get("scheme_name", ""),
        "applicable_conditions": r.get("applicable_conditions") or [],
        "actions": r.get("actions") or [],
        "limitations": r.get("limitations") or [],
        "expert_review": r.get("expert_review", ""),
        "source": r.get("source", ""),
    } for r in support_rows]

    evidence_note = "（含真实案例域）" if _real_cases_enabled() else ""
    return RetrievalData(
        similar_cases=similar,
        regulations=regulations,
        support_recommendations=support_recs,
        missing_topics=_compute_missing(len(similar), len(support_recs)),
        source_type="neo4j_kg",
        evidence_description=(
            f"Neo4j 知识图谱（bge-m3 向量）{evidence_note}：{len(similar)} 案例、{len(regulations)} 规程、{len(support_recs)} 支护方案"
        ),
        real_docs_index=_index_real_docs(),
    )


def retrieve_knowledge(
    *,
    risk_level: RiskLevel,
    warning_id: str,
    factors: list[str] | None = None,
    mine_context: object | None = None,
) -> RetrievalData:
    """三态双轨：NEO4J_URI+EMBEDDING_PROVIDER→向量路径；NEO4J_URI→传统 Q1；未设→YAML。"""
    if os.getenv("NEO4J_URI"):
        if os.getenv("EMBEDDING_PROVIDER"):
            return _neo4j_vector_backend(risk_level, factors)
        return _neo4j_backend(risk_level)
    return _yaml_backend(risk_level)


def probe_neo4j() -> None:
    """编译期连通探测：NEO4J_URI 配置时 RETURN 1，不可达抛 RuntimeError（对齐 Redis 先例）。"""
    uri = os.getenv("NEO4J_URI")
    if not uri:
        return
    from neo4j import GraphDatabase
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "agent1_dev")
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session() as session:
                session.run("RETURN 1").consume()
        finally:
            driver.close()
    except Exception as exc:
        raise RuntimeError(
            f"Neo4j 连通性探测失败（NEO4J_URI={uri}）。"
            "请确认 agent1/deploy/neo4j 容器已启动并已执行 seed。"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════
# LLM→KG 写回（设计 §8，子步③）
# ═══════════════════════════════════════════════════════════════════════════

# 实体/关系类型枚举（设计 §11 术语表 + §8.3，各 7 类；写回白名单）
_ENTITY_TYPES = ["地质构造", "支护设备", "顶板灾变", "监测指标", "处置规程", "岗位角色", "巷道分区"]
_RELATION_TYPES = ["诱发", "支护", "防控", "监测", "调度", "上报", "传导"]

# 写回层节点约束：KnowledgeEntity 以 (name, entity_type) 为自然键（MERGE 幂等）
_KNOWLEDGE_ENTITY_CONSTRAINT = (
    "CREATE CONSTRAINT knowledge_entity_name_type IF NOT EXISTS "
    "FOR (e:KnowledgeEntity) REQUIRE (e.name, e.entity_type) IS UNIQUE"
)


def writeback_result(status: str, data: dict, error: str | None, note: str) -> dict:
    """统一 {status, data, error, audit} 信封（对齐工具层约定，检索/写回共用）。"""
    return {
        "status": status,
        "data": {**data, "note": note},
        "error": error,
        "audit": {
            "tool": "write_knowledge_to_graph",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def _validate_write_entry(entry: dict, idx: int) -> str | None:
    """存储层防御校验（与节点审核门双重保险，设计 §8.2/§8.4）。

    返回错误信息字符串；合法条目返回 None。
    """
    if entry.get("review_status") == "rejected":
        return "候选已被审核拒绝，永不写回"
    if entry.get("review_status") != "approved":
        return "仅 approved 候选可写回"
    if not entry.get("reviewed_by"):
        return "approved 候选审核人必填"
    if not entry.get("entity") or not entry.get("object"):
        return "候选缺 entity/object，非完整 SPO 三元组"
    if entry.get("entity_type") not in _ENTITY_TYPES:
        return f"entity_type 不在 7 类枚举: {entry.get('entity_type')!r}"
    if entry.get("object_type") not in _ENTITY_TYPES:
        return f"object_type 不在 7 类枚举: {entry.get('object_type')!r}"
    if entry.get("relation") not in _RELATION_TYPES:
        return f"relation 不在 7 类枚举: {entry.get('relation')!r}"
    return None


def _write_triple_cypher(relation: str) -> str:
    """构造写回 Cypher。relation 已通过 _RELATION_TYPES 白名单校验，安全内插。"""
    return f"""
    MERGE (s:KnowledgeEntity {{name: $entity, entity_type: $entity_type}})
    MERGE (o:KnowledgeEntity {{name: $object, entity_type: $object_type}})
    MERGE (s)-[r:{relation}]->(o)
    SET r.candidate_id = $candidate_id,
        r.evidence_text = $evidence, r.source = $source, r.confidence = $confidence,
        r.review_status = 'approved', r.reviewed_by = $reviewed_by,
        r.reviewed_at = $reviewed_at, r.written_at = $written_at
    """


def _write_params(entry: dict) -> dict:
    """写回条目 → Cypher 参数（默认值对齐 stage_candidate 语义）。"""
    return {
        "entity": entry["entity"],
        "entity_type": entry["entity_type"],
        "object": entry["object"],
        "object_type": entry["object_type"],
        "candidate_id": entry.get("candidate_id", ""),
        "evidence": entry.get("evidence", ""),
        "source": entry.get("source", "reflection_agent"),
        "confidence": float(entry.get("confidence", 0.5)),
        "reviewed_by": entry.get("reviewed_by", ""),
        "reviewed_at": entry.get("reviewed_at", ""),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }


def write_knowledge_to_graph(entries: list[dict]) -> dict:
    """已审核（approved）且含完整 SPO 的知识条目写入 Neo4j（设计 §8.4）。

    输入条目结构：{entity, entity_type, relation, object, object_type,
                   evidence, source, confidence, reviewed_by, reviewed_at}
    返回统一 {status, data, error, audit} 信封（对齐工具层约定）。
    """
    if not entries:
        return writeback_result("success", {"written": 0, "rejected": []}, None,
                                 "空条目，无写回")
    uri = os.getenv("NEO4J_URI")
    if not uri:
        # 双轨写回：未配置 Neo4j → 候选保留审核队列，不落图（不静默降级写盘）
        return writeback_result("skipped", {"written": 0, "rejected": []}, None,
                                 "NEO4J_URI 未配置，知识候选保留审核队列，未落图")

    # 存储层防御校验（节点审核门通过后仍须过 SPO/枚举/审核人校验）
    written, rejected = [], []
    for idx, entry in enumerate(entries):
        err = _validate_write_entry(entry, idx)
        if err:
            rejected.append({"candidate_id": entry.get("candidate_id", ""), "reason": err})
        else:
            written.append(entry)

    if not written:
        return writeback_result("partial", {"written": 0, "rejected": rejected}, None,
                                 "无合法条目可写回")

    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "agent1_dev")
    try:
        from neo4j import GraphDatabase  # 延迟导入：写回仅在图谱模式触发
        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session() as session:
                session.run(_KNOWLEDGE_ENTITY_CONSTRAINT).consume()  # 幂等建约束
                for e in written:
                    session.run(_write_triple_cypher(e["relation"]), _write_params(e)).consume()
        finally:
            driver.close()
    except Exception as exc:
        raise KnowledgeSourceError(f"Neo4j 知识写回失败（{uri}）: {exc}") from exc

    return writeback_result("success", {"written": len(written), "rejected": rejected}, None,
                             f"写回 {len(written)} 条 approved 三元组")
