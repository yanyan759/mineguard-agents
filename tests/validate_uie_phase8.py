"""阶段 8 UIE 生产化验收脚本。

运行：cd agent1 && python tests/validate_uie_phase8.py
前置：图谱写回用例需 Neo4j 容器（NEO4J_URI 默认注入，同 validate_neo4j_phase8）。

5 个用例：
  1. EntityRelationExtractor 结构（缺 key 抛 UIEExtractionError）
  2. JSON 宽松解析（代码围栏/前导 json）
  3. apply_review 决策打标（approve/reject/skip + reviewer/reviewed_at）
  4. write_approved 链路（图谱模式：approved 完整 SPO 落图；缺 object/缺审核人拒写）
  5. extract_object 端到端（真实 LLM，DeepSeek V4；失败仅提示不判 FAIL）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")

# ═══════════════════════════════════════════════════════════════════════════
# 用例 1：结构校验（不触网）
# ═══════════════════════════════════════════════════════════════════════════


def test_extractor_structure() -> str:
    from tools.uie_extractor import ENTITY_TYPES, EntityRelationExtractor, UIEExtractionError
    assert len(ENTITY_TYPES) == 7, "实体类型应 7 类"
    import os as _os
    # 构造器 __init__ 自动加载 .env（含真实 OPENAI_API_KEY），故先置空 key 再构造，
    # 才能触发"缺 key"守卫（load_dotenv override=False 不覆盖已存在的空值）。finally 恢复原值。
    saved_oai, saved_ds = _os.environ.get("OPENAI_API_KEY"), _os.environ.get("DEEPSEEK_API_KEY")
    try:
        _os.environ["OPENAI_API_KEY"] = ""
        _os.environ["DEEPSEEK_API_KEY"] = ""
        try:
            EntityRelationExtractor(api_key="")
            raise AssertionError("缺 key 应抛 UIEExtractionError")
        except UIEExtractionError:
            pass
    finally:
        if saved_oai is None:
            _os.environ.pop("OPENAI_API_KEY", None)
        else:
            _os.environ["OPENAI_API_KEY"] = saved_oai
        if saved_ds is None:
            _os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            _os.environ["DEEPSEEK_API_KEY"] = saved_ds
    return "7 类实体枚举 + 缺 key 抛 UIEExtractionError"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 2：JSON 宽松解析
# ═══════════════════════════════════════════════════════════════════════════


def test_parse_json() -> str:
    from tools.uie_extractor import _parse_json_object
    assert _parse_json_object('{"object": "锚索"}') == {"object": "锚索"}
    assert _parse_json_object('```json\n{"object": "锚索"}\n```') == {"object": "锚索"}
    assert _parse_json_object('```\n{"object": "锚索"}\n```') == {"object": "锚索"}
    return "宽松解析：裸 JSON / json 围栏 / 裸围栏"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 3：apply_review 决策打标（纯函数）
# ═══════════════════════════════════════════════════════════════════════════


def test_apply_review() -> str:
    from tools.review_candidates import apply_review
    base = {
        "candidate_id": "kc-uie-000", "entity": "锚索", "entity_type": "支护设备",
        "relation": "支护", "review_status": "pending_review",
    }
    a = apply_review(base, reviewer="张三", decision="approve",
                     auto_fill={"object": "顶板", "object_type": "地质构造"})
    assert a["review_status"] == "approved" and a["reviewed_by"] == "张三"
    assert a["object"] == "顶板" and a["reviewed_at"]
    r = apply_review(base, reviewer="李四", decision="reject", rejection_reason="证据不足")
    assert r["review_status"] == "rejected" and r["rejection_reason"] == "证据不足"
    s = apply_review(base, reviewer="王五", decision="skip")
    assert s["review_status"] == "skipped"
    return "approve/reject/skip 打标 + reviewer/reviewed_at 齐备"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 4：write_approved 链路（图谱模式）
# ═══════════════════════════════════════════════════════════════════════════


def _cleanup_test_graph(driver) -> None:
    with driver.session() as session:
        # 仅清理测试命名空间节点（UIE-TEST-1 前缀，含测试专属 object 节点）。
        # 修复前为 "MATCH (s)-[r]->(o) WHERE s.name='UIE-TEST-1' DETACH DELETE s,r,o"：
        # o 曾指向真实图谱实体（如"顶板"），每次运行连带删除真实节点及其全部关系
        # （图谱 175→174 节点 / 150→140 关系）。现改为按前缀清理，绝不触碰真实实体。
        session.run(
            "MATCH (n:KnowledgeEntity) WHERE n.name STARTS WITH 'UIE-TEST-1' "
            "DETACH DELETE n"
        ).consume()


def test_write_approved() -> str:
    from neo4j import GraphDatabase
    from tools.review_candidates import apply_review, write_approved

    uid = f"{int(datetime.now(timezone.utc).timestamp())}"
    obj = f"UIE-TEST-1-OBJ-{uid}"  # 测试专属 object 命名（UIE-TEST-1 前缀），杜绝写回碰撞真实实体
    base = {
        "candidate_id": f"uie-{uid}",
        "entity": "UIE-TEST-1", "entity_type": "支护设备",
        "relation": "支护", "evidence": "UIE 验收测试证据",
        "source": "uie_test",
    }
    # ① approved + 完整 SPO → 落图
    good = apply_review(base, reviewer="张三", decision="approve",
                        auto_fill={"object": obj, "object_type": "地质构造", "confidence": 0.8})
    res = write_approved([good])
    assert res["status"] == "success", f"approved 完整 SPO 应落图，实际 {res}"
    # ② 缺审核人 → 存储层拒写（write_knowledge_to_graph 校验）
    bad_reviewer = apply_review(base, reviewer="", decision="approve",
                                auto_fill={"object": "顶板", "object_type": "地质构造"})
    res2 = write_approved([bad_reviewer])
    assert res2["status"] != "success", "缺审核人应拒写"
    # ③ 缺 object → 存储层拒写（非完整 SPO）
    bad_spo = apply_review(base, reviewer="张三", decision="approve", auto_fill={"object": ""})
    res3 = write_approved([bad_spo])
    assert res3["status"] != "success", "缺 object 应拒写"
    # ④ 图谱可见 approved 三元组 + 清理
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "agent1_dev")),
    )
    try:
        row = driver.session().run(
            "MATCH (s:KnowledgeEntity {name: 'UIE-TEST-1'})-[r]->(o:KnowledgeEntity) "
            "RETURN r.review_status AS rs, r.reviewed_by AS rb LIMIT 1"
        ).single()
        assert row is not None and row["rs"] == "approved" and row["rb"] == "张三", \
            "图谱应含 approved 三元组"
    finally:
        _cleanup_test_graph(driver)
        driver.close()
    return "approved 完整 SPO 落图 + 缺审核人/缺 object 拒写 + 图谱核验 + 清理"


# ═══════════════════════════════════════════════════════════════════════════
# 用例 5：extract_object 端到端（真实 LLM；不可达仅提示不判 FAIL）
# ═══════════════════════════════════════════════════════════════════════════


def test_extract_end_to_end() -> str:
    import os as _os
    try:  # 与抽取器一致：自动加载 .env（含 OPENAI_API_KEY），使 SKIP 判断与运行一致
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv())
    except ImportError:
        pass
    if not _os.getenv("OPENAI_API_KEY"):
        return "SKIP: 未配置 OPENAI_API_KEY（端到端跳过）"
    from tools.uie_extractor import EntityRelationExtractor, UIEExtractionError
    try:
        ex = EntityRelationExtractor()
        info = ex.extract_object({
            "entity": "微震事件", "relation": "诱发",
            "evidence": "监测显示东翼 201 工作面切眼外侧微震能量持续异常，"
                        "累计能量达 120000J，顶板出现断裂前兆。",
        })
    except UIEExtractionError as exc:
        return f"NOTE: 抽取端到端不可达（不判 FAIL）: {exc}"
    assert info["object"], f"应抽取到 object，实际 {info}"
    assert info["object_type"] in ["地质构造", "顶板灾变", "监测指标", "支护设备", "处置规程", "岗位角色", "巷道分区"], info
    return f"抽取端到端：object={info['object']} type={info['object_type']} conf={info['confidence']}"


# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("阶段 8 UIE 生产化验收")
    print("=" * 60)
    cases = [
        ("1. 抽取器结构", test_extractor_structure),
        ("2. JSON 宽松解析", test_parse_json),
        ("3. 审核决策打标", test_apply_review),
        ("4. 写回链路", test_write_approved),
        ("5. 抽取端到端", test_extract_end_to_end),
    ]
    passed = failed = 0
    for name, fn in cases:
        try:
            detail = fn()
            if detail.startswith("SKIP") or detail.startswith("NOTE"):
                print(f"  [--] {name}  —  {detail}")
            else:
                passed += 1
                print(f"  [PASS] {name}  —  {detail}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {name}  —  {type(exc).__name__}: {str(exc)[:200]}")
    print("\n" + "=" * 60)
    print(f"  结果: {passed} PASS / {failed} FAIL")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
