"""审计缺口结构性补齐统一验收脚本（离线）。

针对「审计结论为 partial / 结构缺失」的跨领域硬缺口，逐项验证**结构已就位**。
本脚本不依赖 MySQL/Neo4j/LLM 在线（离线即可跑通），只验收结构与代码路径；
真实环境联调由对应阶段脚本负责（validate_mysql_phase7 / validate_neo4j_phase8
/ validate_llm_nodes 等）。

覆盖 9 大缺口：
G1 第三数据域：支护方案/专家研判（support_plans.yaml + schema 字段 + Neo4j SupportPlan）
G2 真实资料接入确定性轨道（deploy/ragflow/docs/real + _index_real_docs）
G3 MySQL 主链消费（资源评估读表 + 协同管控写工单 + 权限读表）
G4 权限矩阵落库（role_permissions DDL + seed + 工具优先读表）
G5 UIE 富化运行时（enrich_candidates + 写回节点接入）
G6 工作流取消（runner.cancel + API 端点）
G7 成员 1 MOA-Transformer 融合（prediction_result 字段 + 感知节点增强）
G8 知识候选治理（pending_review + 审核门唯一入库通道）
G9 知识写回完整 SPO（write_knowledge_to_graph 存储层校验）

用法：
    cd agent1
    python tests/validate_gap_closure.py
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项检查结果。"""
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n【{title}】")


def _has_src(rel_path: str, *needles: str) -> tuple[bool, str]:
    """读取文件并检查是否包含全部指定片段（结构就位判定）。"""
    path = AGENT1_ROOT / rel_path
    if not path.exists():
        return False, f"{rel_path} 不存在"
    src = path.read_text(encoding="utf-8")
    missing = [n for n in needles if n not in src]
    if missing:
        return False, f"{rel_path} 缺少片段: {missing}"
    return True, f"{rel_path} 含 {len(needles)} 个关键片段"


# ═══════════════════════════════════════════════════════════════════════════
# G1 第三数据域：支护方案/专家研判
# ═══════════════════════════════════════════════════════════════════════════


def test_g1_support_plans() -> None:
    """支护方案独立知识源 + schema 字段 + 知识后端消费 + Neo4j 摄取。"""
    import yaml

    yaml_path = AGENT1_ROOT / "tests" / "fixtures" / "support_plans.yaml"
    ok_file = yaml_path.exists()
    check("G1 support_plans.yaml 存在", ok_file, str(yaml_path))
    if ok_file:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        plans = data.get("support_plans") or []
        check("G1 含 ≥3 支护方案", len(plans) >= 3, f"{len(plans)} 条")
        required = {"scheme_id", "scheme_name", "applicable_conditions",
                    "actions", "limitations", "expert_review", "source"}
        bad = [p.get("scheme_id") for p in plans if not required.issubset(p)]
        check("G1 方案字段完整（含 applicable_conditions/expert_review）",
              not bad, f"缺字段方案: {bad}")

    # schema 字段
    from app.schemas.workflow import SupportRecommendation

    fields = set(SupportRecommendation.model_fields)
    check("G1 SupportRecommendation 含新字段",
          {"applicable_conditions", "expert_review", "limitations"} <= fields,
          f"{sorted(fields)}")

    # 知识后端（YAML 轨）消费
    from app.agent.knowledge_sources import _yaml_backend
    from app.schemas.workflow import RiskLevel

    data = _yaml_backend(RiskLevel.RED)
    recs = data.support_recommendations
    check("G1 _yaml_backend 返回支护推荐",
          len(recs) >= 1 and all(
              r["applicable_conditions"] and r["expert_review"] for r in recs),
          f"{len(recs)} 条")

    # Neo4j 摄取脚本包含 SupportPlan 节点
    ok, detail = _has_src(
        "deploy/neo4j/scripts/seed_from_yaml.py", "SupportPlan", "APPLIES_TO",
    )
    check("G1 Neo4j seed 摄取 SupportPlan 节点", ok, detail)


# ═══════════════════════════════════════════════════════════════════════════
# G2 真实资料接入确定性轨道
# ═══════════════════════════════════════════════════════════════════════════


def test_g2_real_docs_index() -> None:
    """真实资料目录存在 + _index_real_docs 索引返回。"""
    real_dir = AGENT1_ROOT / "deploy" / "ragflow" / "docs" / "real"
    ok_dir = real_dir.is_dir()
    check("G2 real 资料目录存在", ok_dir, str(real_dir))
    if ok_dir:
        for sub in ("coal_mine_accident_cases", "coal_mine_safety_regulations", "coal_mine_support_schemes"):
            sub_path = real_dir / sub
            files = list(sub_path.rglob("*")) if sub_path.is_dir() else []
            files = [f for f in files if f.is_file()]
            check(f"G2 {sub} 有资料", len(files) >= 1, f"{len(files)} 份")

    from app.agent.knowledge_sources import _index_real_docs

    index = _index_real_docs()
    domains = {d["domain"]: d.get("file_count", 0) for d in index}
    check("G2 _index_real_docs 返回三域索引",
          {"accident_cases", "regulations", "support_plans"} <= set(domains),
          f"{domains}")


# ═══════════════════════════════════════════════════════════════════════════
# G3 MySQL 主链消费（资源评估读表 + 协同管控写工单）
# ═══════════════════════════════════════════════════════════════════════════


def test_g3_mysql_consumption() -> None:
    """mysql_bridge 三接口 + 两节点接入 + 表 DDL 就位。"""
    from app.services import mysql_bridge

    for fn in ("load_available_resources", "create_work_orders", "load_role_permissions"):
        check(f"G3 mysql_bridge.{fn} 存在", callable(getattr(mysql_bridge, fn, None)))

    # 离线降级：未配置 MySQL 时返回 None / error，不抛异常
    avail = mysql_bridge.load_available_resources()
    check("G3 load_available_resources 离线安全",
          avail is None or isinstance(avail, dict), f"{type(avail).__name__}")
    wo = mysql_bridge.create_work_orders(
        [{"action": "加密支护", "owner_role": "支护工程师", "deadline_minutes": 30}]
    )
    check("G3 create_work_orders 离线安全",
          wo.get("status") in ("success", "error"), f"status={wo.get('status')}")

    ok, detail = _has_src(
        "agent/workflow_nodes.py",
        "load_available_resources", "_merge_mysql_availability", "create_work_orders",
    )
    check("G3 资源评估读 MySQL + 协同管控写工单", ok, detail)

    ok, detail = _has_src(
        "deploy/mysql/sql/01_create_tables.sql",
        "CREATE TABLE IF NOT EXISTS work_orders",
        "CREATE TABLE IF NOT EXISTS role_permissions",
    )
    check("G3 work_orders/role_permissions DDL 就位", ok, detail)

    ok, detail = _has_src("tools/permission_check_tool.py", "permission_source", "_resolve_role_permissions")
    check("G3 权限工具含 permission_source", ok, detail)


# ═══════════════════════════════════════════════════════════════════════════
# G4 权限矩阵落库
# ═══════════════════════════════════════════════════════════════════════════


def test_g4_permission_matrix() -> None:
    """role_permissions 种子 + 工具回退链。"""
    ok, detail = _has_src(
        "deploy/mysql/sql/02_seed_data.sql", "role_permissions", "矿总工程师",
    )
    check("G4 role_permissions 种子数据", ok, detail)

    # 内置矩阵回退：MySQL 不可达时仍能判定（离线）
    from app.tools.permission_check_tool import _ROLE_PERMISSIONS, check_permission

    check("G4 内置权限矩阵 6 角色",
          len(_ROLE_PERMISSIONS) >= 6, f"{len(_ROLE_PERMISSIONS)} 角色")
    r = check_permission.invoke({"user_role": "监测值班员", "requested_action": "acknowledge_alert"})
    check("G4 离线回退仍可校验",
          r["data"]["passed"] and r["data"]["permission_source"] in ("mysql", "builtin"),
          f"source={r['data']['permission_source']}")


# ═══════════════════════════════════════════════════════════════════════════
# G5 UIE 富化运行时
# ═══════════════════════════════════════════════════════════════════════════


def test_g5_uie_runtime() -> None:
    """enrich_candidates 批量富化 + 写回节点接入。"""
    from app.tools.uie_extractor import enrich_candidates

    candidates = [
        {"candidate_id": "kc-x-000", "entity": "含水层", "entity_type": "地质构造",
         "relation": "诱发", "evidence": "砂岩含水层疏干", "review_status": "pending_review"},
        {"candidate_id": "kc-x-001", "entity": "锚索", "entity_type": "支护设备",
         "relation": "支护", "evidence": "补打锚索", "review_status": "pending_review"},
    ]
    out, summary = enrich_candidates(candidates, enabled=False)
    check("G5 未启用时原样返回（结构保留）",
          out is candidates and summary["skipped"] == 2 and summary["enriched"] == 0,
          f"{summary}")

    # 启用但无 API Key → 降级不抛异常
    try:
        out2, summary2 = enrich_candidates(list(candidates), enabled=True, max_batch=1)
        degraded_ok = summary2.get("failed", 0) >= 0
    except Exception as exc:  # noqa: BLE001
        degraded_ok = False
        summary2 = {"reason": str(exc)}
    check("G5 启用但离线降级不抛异常", degraded_ok, f"{summary2}")

    ok, detail = _has_src("agent/workflow_nodes.py", "enrich_candidates")
    check("G5 写回节点接入 enrich_candidates", ok, detail)


# ═══════════════════════════════════════════════════════════════════════════
# G6 工作流取消
# ═══════════════════════════════════════════════════════════════════════════


def test_g6_cancel() -> None:
    """runner.cancel + API 端点 + 终态守卫。"""
    from app.api.workflow_runner import WorkflowRunRecord, runner

    check("G6 runner.cancel 存在", callable(getattr(runner, "cancel", None)))
    ok, detail = _has_src(
        "api/workflow_runner.py", "workflow_cancelled", "cancel_requested",
    )
    check("G6 取消事件/标记", ok, detail)
    ok, detail = _has_src("api/server.py", '"/api/workflow/{run_id}/cancel"')
    check("G6 /api/workflow/{run_id}/cancel 端点", ok, detail)

    # 不存在的 run 应抛 KeyError（由 API 层转 404）
    try:
        runner._get("no-such-run")
        missing_ok = False
    except KeyError:
        missing_ok = True
    check("G6 未知 run 抛 KeyError", missing_ok)


# ═══════════════════════════════════════════════════════════════════════════
# G7 成员 1 MOA-Transformer 融合
# ═══════════════════════════════════════════════════════════════════════════


def test_g7_prediction_fusion() -> None:
    """prediction_result 字段 + 感知节点增强 + 模型工具真实 HTTP 通道。"""
    import inspect

    import app.agent.workflow_nodes as nodes
    import app.agent.workflow_state as wstate

    check("G7 WorkflowState.prediction_result 字段",
          "prediction_result" in wstate.WorkflowState.__annotations__)

    ok, detail = _has_src(
        "agent/workflow_nodes.py", "_build_prediction_augmentation",
        "run_prediction", "prediction_result",
    )
    check("G7 感知节点融合预测增强", ok, detail)

    # 特征行构造：微震事件聚合（结构可用）
    rows_fn = getattr(nodes, "_microseismic_feature_rows", None)
    check("G7 微震十维特征聚合函数", callable(rows_fn))

    # 未配置 URL → 增强返回 None（不产生预测，不覆盖风险定级）
    src = inspect.getsource(nodes._build_prediction_augmentation)
    check("G7 红线：预测不参与风险定级", "绝不覆盖" in src or "不参与风险定级" in src)

    ok, detail = _has_src("tools/prediction_model_tool.py", "requests.post", "ModelInferenceResponse")
    check("G7 模型工具含真实 HTTP 通道", ok, detail)


# ═══════════════════════════════════════════════════════════════════════════
# G8 知识候选治理
# ═══════════════════════════════════════════════════════════════════════════


def test_g8_candidate_governance() -> None:
    """反思派生候选保持 pending_review + 审核门唯一入库通道。"""
    import app.agent.workflow_nodes as nodes

    candidates = nodes._derive_knowledge_candidates(
        perception=None, outcome=None, risk_before=0.5, risk_after=0.3,
        reflection_id="r-test",
    )
    check("G8 无感知时不产生候选", candidates == [])

    # 用 fixture 真实感知结果构造候选：校验 pending_review + 不自动写库
    from app.agent.fixture_loader import FixtureDataSource

    perception = FixtureDataSource().get_expected_result("yellow")
    candidates = nodes._derive_knowledge_candidates(
        perception, outcome=None, risk_before=0.5, risk_after=0.3,
        reflection_id="r-test",
    )
    ok = all(c["review_status"] == "pending_review" for c in candidates)
    check("G8 候选全部 pending_review", ok and len(candidates) >= 1, f"{len(candidates)} 条")

    action, reason = nodes._review_gate({"review_status": "pending_review"})
    check("G8 审核门 pending → skip", action == "skip", reason)


# ═══════════════════════════════════════════════════════════════════════════
# G9 知识写回完整 SPO
# ═══════════════════════════════════════════════════════════════════════════


def test_g9_writeback_spo() -> None:
    """存储层校验完整 SPO + 双轨降级（未配置 Neo4j 不落图）。"""
    import app.agent.knowledge_sources as ks

    entry = {
        "entity": "含水层", "entity_type": "地质构造",
        "relation": "诱发", "object": "", "object_type": "",
        "review_status": "approved", "reviewed_by": "矿总工程师",
    }
    err = ks._validate_write_entry(entry, 0)
    check("G9 缺 object 拒写", err is not None and "object" in err, f"{err}")

    entry_ok = dict(entry, object="锚索", object_type="支护设备")
    check("G9 完整 SPO 通过校验", ks._validate_write_entry(entry_ok, 0) is None)

    # 未配置 NEO4J_URI → skipped（保留审核队列，不静默降级写盘）
    import os

    had = os.environ.pop("NEO4J_URI", None)
    try:
        out = ks.write_knowledge_to_graph([entry_ok])
    finally:
        if had is not None:
            os.environ["NEO4J_URI"] = had
    check("G9 未配置 Neo4j → skipped 不落图",
          out["status"] == "skipped", f"status={out['status']}: {out['data'].get('note')}")


def main() -> int:
    print("=" * 56)
    print("  审计缺口结构性补齐统一验收（离线）")
    print("=" * 56)

    section("G1 第三数据域：支护方案/专家研判")
    test_g1_support_plans()

    section("G2 真实资料接入确定性轨道")
    test_g2_real_docs_index()

    section("G3 MySQL 主链消费（资源/工单/权限）")
    test_g3_mysql_consumption()

    section("G4 权限矩阵落库")
    test_g4_permission_matrix()

    section("G5 UIE 富化运行时")
    test_g5_uie_runtime()

    section("G6 工作流取消")
    test_g6_cancel()

    section("G7 成员 1 MOA-Transformer 融合")
    test_g7_prediction_fusion()

    section("G8 知识候选治理")
    test_g8_candidate_governance()

    section("G9 知识写回完整 SPO")
    test_g9_writeback_spo()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n\n验证结果: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
