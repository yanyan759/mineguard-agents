"""Neo4j 灾变知识图谱种子摄取（YAML 权威源 → 图）。

从 tests/fixtures/accident_cases.yaml + regulations.yaml 建图。
全部 MERGE 幂等（case_id / regulation_id / level / type 为自然键），seq 保 YAML 序。

用法：
    cd agent1
    python deploy/neo4j/scripts/seed_from_yaml.py                  # env/默认参数
    python deploy/neo4j/scripts/seed_from_yaml.py --reset          # 清空重建

环境变量：NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
（默认 bolt://localhost:7687 / neo4j / agent1_dev）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

AGENT1_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# 本脚本以脚本方式运行（python deploy/neo4j/scripts/seed_from_yaml.py）时，
# sys.path[0] 是脚本所在目录而非 agent1/；--uie 恢复步骤要 import
# tools.import_uie_csv / tools.review_candidates（后者又依赖 app.agent.knowledge_sources），
# 必须把 agent1/ 显式挂到 sys.path 上（根因修复，否则 --uie 必抛 ModuleNotFoundError）。
sys.path.insert(0, str(AGENT1_ROOT))
CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "accident_cases.yaml"
REGS_YAML = AGENT1_ROOT / "tests" / "fixtures" / "regulations.yaml"
PLANS_YAML = AGENT1_ROOT / "tests" / "fixtures" / "support_plans.yaml"
# 真实数据域：21 份真实事故案例（real_accident_cases.yaml，与仿真 accident_cases.yaml 分离）。
# 由 --real 摄取到独立 RealAccidentCase 标签，不触碰 AccidentCase 仿真基线（保 12 项验收不破）。
REAL_CASES_YAML = AGENT1_ROOT / "tests" / "fixtures" / "real_accident_cases.yaml"

# 对齐 workflow_nodes._RISK_ORDER（NORMAL=0 ... RED=4）
_RISK_ORDER = {"normal": 0, "blue": 1, "yellow": 2, "orange": 3, "red": 4}
_SOURCE_TYPES = ["safety_regulation", "rock_burst", "support_standard", "emergency_plan"]

_SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT accident_case_id IF NOT EXISTS FOR (c:AccidentCase) REQUIRE c.case_id IS UNIQUE",
    "CREATE CONSTRAINT regulation_id IF NOT EXISTS FOR (r:Regulation) REQUIRE r.regulation_id IS UNIQUE",
    "CREATE CONSTRAINT support_plan_id IF NOT EXISTS FOR (s:SupportPlan) REQUIRE s.scheme_id IS UNIQUE",
    "CREATE CONSTRAINT risk_level_level IF NOT EXISTS FOR (l:RiskLevel) REQUIRE l.level IS UNIQUE",
    "CREATE CONSTRAINT source_type_type IF NOT EXISTS FOR (s:SourceType) REQUIRE s.type IS UNIQUE",
    "CREATE INDEX accident_case_risk IF NOT EXISTS FOR (c:AccidentCase) ON (c.risk_level)",
    "CREATE INDEX accident_case_seq IF NOT EXISTS FOR (c:AccidentCase) ON (c.seq)",
    "CREATE INDEX support_plan_seq IF NOT EXISTS FOR (s:SupportPlan) ON (s.seq)",
]

# --real 时补建的真实案例约束/索引（RealAccidentCase 独立标签，Q1R 专用）
_REAL_SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT real_accident_case_id IF NOT EXISTS FOR (c:RealAccidentCase) REQUIRE c.case_id IS UNIQUE",
    "CREATE INDEX real_accident_case_risk IF NOT EXISTS FOR (c:RealAccidentCase) ON (c.risk_level)",
    "CREATE INDEX real_accident_case_seq IF NOT EXISTS FOR (c:RealAccidentCase) ON (c.seq)",
]


def _get_driver(uri: str, user: str, password: str):
    from neo4j import GraphDatabase  # 延迟导入
    return GraphDatabase.driver(uri, auth=(user, password))


def _seed(session, real: bool = False) -> None:
    # ① 预建全部 5 级 RiskLevel（含 severity）+ 4 个 SourceType
    #    —— 缺失会导致 Q1 `severity >=` 过滤判空（0 行），必须预建
    for level, severity in _RISK_ORDER.items():
        session.run(
            "MERGE (l:RiskLevel {level: $level}) SET l.severity = $severity",
            level=level, severity=severity,
        ).consume()
    for st in _SOURCE_TYPES:
        session.run("MERGE (s:SourceType {type: $type})", type=st).consume()

    # ② 事故案例 + HAS_RISK_LEVEL（seq = YAML 索引，保序）
    cases = yaml.safe_load(CASES_YAML.read_text(encoding="utf-8")).get("cases", [])
    for idx, c in enumerate(cases):
        props = {
            "location": c.get("location"),
            "occurred_at": c.get("occurred_at"),
            "risk_level": c.get("risk_level"),
            "causes": c.get("causes", []),
            "precursors": c.get("precursors", []),
            "actions": c.get("actions", []),
            "outcome": c.get("outcome"),
            "lessons": c.get("lessons", []),
            "source": c.get("source"),
        }
        session.run(
            "MERGE (c:AccidentCase {case_id: $case_id}) SET c += $props, c.seq = $seq",
            case_id=c["case_id"], props=props, seq=idx,
        ).consume()
        session.run(
            "MATCH (c:AccidentCase {case_id: $case_id}) "
            "MATCH (l:RiskLevel {level: $level}) "
            "MERGE (c)-[:HAS_RISK_LEVEL]->(l)",
            case_id=c["case_id"], level=c.get("risk_level", "normal"),
        ).consume()

    # ②' 真实事故案例（--real）→ RealAccidentCase 独立标签 + HAS_RISK_LEVEL。
    #     与仿真 AccidentCase 完全分离：case_id real-* 前缀不冲突，Q1 检索不受影响；
    #     metadata（source_url/local_file/published_at）一并入图作为真实来源凭证。
    if real:
        real_cases = yaml.safe_load(REAL_CASES_YAML.read_text(encoding="utf-8")).get("cases", [])
        for idx, c in enumerate(real_cases):
            props = {
                "location": c.get("location"),
                "occurred_at": c.get("occurred_at"),
                "risk_level": c.get("risk_level"),
                "causes": c.get("causes", []),
                "precursors": c.get("precursors", []),
                "actions": c.get("actions", []),
                "outcome": c.get("outcome"),
                "lessons": c.get("lessons", []),
                "source": c.get("source"),
            }
            # metadata 展开为独立标量属性（Neo4j 属性不支持 Map 值），
            # source_url/local_file/published_at 作为真实来源凭证入图。
            md = c.get("metadata") or {}
            props["source_url"] = md.get("source_url")
            props["local_file"] = md.get("local_file")
            props["published_at"] = md.get("published_at")
            session.run(
                "MERGE (c:RealAccidentCase {case_id: $case_id}) SET c += $props, c.seq = $seq",
                case_id=c["case_id"], props=props, seq=idx,
            ).consume()
            session.run(
                "MATCH (c:RealAccidentCase {case_id: $case_id}) "
                "MATCH (l:RiskLevel {level: $level}) "
                "MERGE (c)-[:HAS_RISK_LEVEL]->(l)",
                case_id=c["case_id"], level=c.get("risk_level", "normal"),
            ).consume()

    # ③ 规程 + CLASSIFIED_AS
    regs = yaml.safe_load(REGS_YAML.read_text(encoding="utf-8")).get("regulations", [])
    for idx, r in enumerate(regs):
        props = {
            "document": r.get("document"),
            "clause": r.get("clause"),
            "requirement": r.get("requirement"),
            "mandatory": r.get("mandatory", True),
            "applicable_conditions": r.get("applicable_conditions", []),
            "source_type": r.get("source_type"),
            "source": r.get("source"),
        }
        session.run(
            "MERGE (r:Regulation {regulation_id: $regulation_id}) SET r += $props, r.seq = $seq",
            regulation_id=r["regulation_id"], props=props, seq=idx,
        ).consume()
        session.run(
            "MATCH (r:Regulation {regulation_id: $rid}) "
            "MATCH (s:SourceType {type: $type}) "
            "MERGE (r)-[:CLASSIFIED_AS]->(s)",
            rid=r["regulation_id"], type=r.get("source_type"),
        ).consume()

    # ④ 支护方案/专家研判（第三数据域，独立 SupportPlan 节点）+ APPLIES_TO SourceType
    #    —— Q3_SUPPORT 专用查询源，support_plans.yaml 权威摄取
    plans = yaml.safe_load(PLANS_YAML.read_text(encoding="utf-8")).get("support_plans", [])
    for idx, p in enumerate(plans):
        props = {
            "scheme_name": p.get("scheme_name"),
            "applicable_conditions": p.get("applicable_conditions", []),
            "actions": p.get("actions", []),
            "limitations": p.get("limitations", []),
            "expert_review": p.get("expert_review", ""),
            "source_type": p.get("source_type"),
            "source": p.get("source"),
        }
        session.run(
            "MERGE (s:SupportPlan {scheme_id: $scheme_id}) SET s += $props, s.seq = $seq",
            scheme_id=p["scheme_id"], props=props, seq=idx,
        ).consume()
        session.run(
            "MATCH (s:SupportPlan {scheme_id: $sid}) "
            "MATCH (st:SourceType {type: $type}) "
            "MERGE (s)-[:APPLIES_TO]->(st)",
            sid=p["scheme_id"], type=p.get("source_type"),
        ).consume()


def _restore_uie_knowledge() -> None:
    """重建 UIE 知识实体域（225 条 approved 三元组 → KnowledgeEntity）。

    供 --uie / --reset 后恢复：--reset 只清空重建仿真+真实案例域，UIE 标注知识
    （452 条 CSV 中 225 条完整 SPO，人工审核 approved）是独立摄入步骤，必须单独恢复，
    否则 validate_uie_phase8 写回链路虽通过但图谱缺失标注知识实体。
    """
    from tools.import_uie_csv import load_csv, to_candidates
    from tools.review_candidates import apply_review, write_approved

    csv_path = AGENT1_ROOT.parent / "任务成果" / "02_UIE标注数据" / "标注_合并_2026-08-17.csv"
    if not csv_path.exists():
        print("[uie] 标注 CSV 缺失，跳过 UIE 恢复（不中断）：%s" % csv_path)
        return
    rows = load_csv(csv_path)
    candidates, _ = to_candidates(rows)
    complete = [c for c in candidates if c["entity"] and c["relation"] and c["object"]]
    reviewed = [
        apply_review(c, reviewer="标注人工审核", decision="approve",
                     auto_fill={"evidence": c.get("evidence", ""),
                                "confidence": c.get("confidence", 0.5)})
        for c in complete
    ]
    res = write_approved(reviewed)
    written = (res.get("data") or {}).get("written", 0)
    print(f"[uie] 恢复 {written} 条 approved 三元组（KnowledgeEntity）")


def main() -> int:
    parser = argparse.ArgumentParser(description="Neo4j 灾变知识图谱种子摄取（YAML 权威源）")
    parser.add_argument("--uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--password", default=os.getenv("NEO4J_PASSWORD", "agent1_dev"))
    parser.add_argument("--reset", action="store_true", help="先清空全图再重建")
    parser.add_argument("--real", action="store_true",
                        help="同时摄取真实事故案例域（real_accident_cases.yaml → RealAccidentCase）")
    parser.add_argument("--uie", action="store_true",
                        help="摄取后恢复 UIE 标注知识实体域（225 条 approved 三元组）")
    args = parser.parse_args()

    driver = _get_driver(args.uri, args.user, args.password)
    try:
        with driver.session() as session:
            for stmt in _SCHEMA_STATEMENTS:
                session.run(stmt).consume()
            if args.real:
                for stmt in _REAL_SCHEMA_STATEMENTS:
                    session.run(stmt).consume()
            if args.reset:
                session.run("MATCH (n) DETACH DELETE n").consume()
                print("[reset] 全图已清空")
            _seed(session, real=args.real)
            suffix = " + 真实案例域" if args.real else ""
            print(f"[ok] 摄取完成（MERGE 幂等{suffix}）")
            if args.uie:
                _restore_uie_knowledge()
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
