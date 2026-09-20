"""阶段 3 工具层校验脚本。

校验内容：
1. 全部 15 个工具可导入
2. 改造工具的返回值结构（status/data/error/audit）
3. DB 工具只读限制（DROP 被拒绝）
4. 新建工具确定性逻辑正确性
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

# 导入路径设置
AGENT1_ROOT = Path(__file__).resolve().parent.parent
PRECODE_ROOT = AGENT1_ROOT.parent
sys.path.insert(0, str(PRECODE_ROOT))
sys.path.insert(0, str(AGENT1_ROOT))

# 建立 app -> agent1 映射（解决 from app.xxx 导入路径问题）
import agent1  # noqa: E402
sys.modules["app"] = agent1

# 为 app.api.context 建立模拟模块（工具导入时需要 get_session_context）
_api_mod = types.ModuleType("app.api")
_api_mod.__package__ = "app.api"
sys.modules["app.api"] = _api_mod

_ctx_mod = types.ModuleType("app.api.context")
_ctx_mod.get_session_context = lambda: "."
sys.modules["app.api.context"] = _ctx_mod

# app.utils.word_converter 模拟（pdf_tools 需要）
_utils_mod = types.ModuleType("app.utils")
_utils_mod.__package__ = "app.utils"
sys.modules["app.utils"] = _utils_mod
_wc_mod = types.ModuleType("app.utils.word_converter")
_wc_mod.convert_markdown_to_pdf = lambda src, dst: None
sys.modules["app.utils.word_converter"] = _wc_mod

# app.api.monitor 模拟（旧工具引用）
_mon_mod = types.ModuleType("app.api.monitor")
_mon_mod.monitor = types.SimpleNamespace()
_mon_mod.monitor.report_tool = lambda **kw: None
_mon_mod.monitor.report_assistant = lambda *a, **kw: None
sys.modules["app.api.monitor"] = _mon_mod


def _import_tool(module_name: str, tool_name: str):
    """直接加载工具模块并返回指定工具函数。"""
    file_path = AGENT1_ROOT / "tools" / f"{module_name.split('.')[-1]}.py"
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    # 设置 __package__ 以支持模块内的 from app.xxx 导入
    mod.__package__ = "tools"
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, tool_name)


def run_checks():
    passed = 0
    failed = 0

    def check(label, condition, detail=""):
        nonlocal passed, failed
        if condition:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label}  {detail}")
            failed += 1

    # ========================================================================
    # 1. 改造工具导入
    # ========================================================================
    print("\n=== 1. 改造工具导入 ===")
    try:
        list_tables = _import_tool("tools.db_tools", "list_sql_tables")
        get_data = _import_tool("tools.db_tools", "get_table_data")
        exec_sql = _import_tool("tools.db_tools", "execute_sql_query")
        check("db_tools: 3 工具全部导入", True)
    except Exception as e:
        check("db_tools 导入", False, str(e)[:80])

    try:
        get_ast = _import_tool("tools.ragflow_tools", "get_assistant_list")
        create_ask = _import_tool("tools.ragflow_tools", "create_ask_delete")
        check("ragflow_tools: 2 工具全部导入", True)
    except Exception as e:
        check("ragflow_tools 导入", False, str(e)[:80])

    try:
        isearch = _import_tool("tools.tavily_tool", "internet_search")
        check("tavily_tool: internet_search 导入", True)
    except Exception as e:
        check("tavily_tool 导入", False, str(e)[:80])

    try:
        gen_md = _import_tool("tools.markdown_tools", "generate_markdown")
        check("markdown_tools: generate_markdown 导入", True)
    except Exception as e:
        check("markdown_tools 导入", False, str(e)[:80])

    try:
        conv_pdf = _import_tool("tools.pdf_tools", "convert_md_to_pdf")
        check("pdf_tools: convert_md_to_pdf 导入", True)
    except Exception as e:
        check("pdf_tools 导入", False, str(e)[:80])

    try:
        read_f = _import_tool("tools.upload_file_read_tool", "read_file_content")
        check("upload_file_read_tool: read_file_content 导入", True)
    except Exception as e:
        check("upload_file_read_tool 导入", False, str(e)[:80])

    # ========================================================================
    # 2. 新建工具导入
    # ========================================================================
    print("\n=== 2. 新建工具导入 ===")
    new_tools = [
        ("prediction_model_tool", "run_prediction"),
        ("data_quality_tool", "check_data_quality"),
        ("plan_scoring_tool", "score_plans"),
        ("resource_accounting_tool", "calculate_gaps"),
        ("compliance_check_tool", "check_compliance"),
        ("permission_check_tool", "check_permission"),
        ("notification_tool", "resolve_recipients"),
        ("knowledge_candidate_tool", "stage_candidate"),
        ("execution_feedback_tool", "query_feedback"),
    ]
    for mod_name, tool_name in new_tools:
        try:
            _import_tool(mod_name, tool_name)
            check(f"{mod_name} 导入", True)
        except Exception as e:
            check(f"{mod_name} 导入", False, str(e)[:80])

    # ========================================================================
    # 3. DB 只读限制
    # ========================================================================
    print("\n=== 3. DB 只读限制 ===")
    exec_sql = _import_tool("tools.db_tools", "execute_sql_query")

    # DROP 应被拒绝
    r = exec_sql.invoke({"query": "DROP TABLE test"})
    check("DROP TABLE 被拒绝", r["status"] == "rejected",
          f"status={r['status']}, error={r.get('error','')[:40]}")

    # DELETE 应被拒绝
    r = exec_sql.invoke({"query": "DELETE FROM test WHERE id=1"})
    check("DELETE 被拒绝", r["status"] == "rejected")

    # INSERT 应被拒绝
    r = exec_sql.invoke({"query": "INSERT INTO test VALUES (1)"})
    check("INSERT 被拒绝", r["status"] == "rejected")

    # UPDATE 应被拒绝
    r = exec_sql.invoke({"query": "UPDATE test SET x=1 WHERE id=1"})
    check("UPDATE 被拒绝", r["status"] == "rejected")

    # SELECT 应通过
    r = exec_sql.invoke({"query": "SELECT * FROM test WHERE id=1"})
    check("SELECT 被允许（离线模式降级为 error 但非 rejected）",
          r["status"] != "rejected",
          f"status={r['status']}")

    # 空查询应被拒绝
    r = exec_sql.invoke({"query": ""})
    check("空 SQL 被拒绝", r["status"] != "success")

    # ========================================================================
    # 4. 确定性工具逻辑
    # ========================================================================
    print("\n=== 4. 确定性工具逻辑 ===")

    # 4a. 数据质量检查
    cq = _import_tool("tools.data_quality_tool", "check_data_quality")
    records = [
        {"event_id": "e1", "timestamp": "2026-07-24T08:00:00", "energy_j": 1000},
        {"event_id": "e2", "timestamp": "2026-07-24T08:10:00", "energy_j": 2000},
        {"event_id": "e3", "timestamp": "2026-07-24T08:20:00", "energy_j": 1500},
    ]
    r = cq.invoke({"records": records, "start_time": "2026-07-24T08:00:00",
                    "end_time": "2026-07-24T09:00:00", "expected_frequency_hz": 0.01})
    check("数据质量：返回 success", r["status"] == "success")
    check("数据质量：completeness > 0", r["data"]["completeness"] > 0)

    # 空记录
    r = cq.invoke({"records": [], "start_time": "2026-07-24T08:00:00",
                    "end_time": "2026-07-24T09:00:00"})
    check("数据质量：空记录 → error", r["status"] == "error")

    # 4b. 权限校验
    cp = _import_tool("tools.permission_check_tool", "check_permission")
    r = cp.invoke({"user_role": "矿总工程师", "requested_action": "approve_plan"})
    check("权限：矿总工程师可审批", r["data"]["passed"] is True)

    r = cp.invoke({"user_role": "操作工", "requested_action": "approve_plan"})
    check("权限：操作工不可审批", r["data"]["passed"] is False)

    # 4c. 通知解析
    nr = _import_tool("tools.notification_tool", "resolve_recipients")
    r = nr.invoke({"risk_level": "red"})
    check("通知：red 等级返回 6 个收件人", len(r["data"]["recipients"]) == 6)

    r = nr.invoke({"risk_level": "normal"})
    check("通知：normal 等级返回 0 个收件人", len(r["data"]["recipients"]) == 0)

    # 4d. 知识候选暂存
    sc = _import_tool("tools.knowledge_candidate_tool", "stage_candidate")
    r = sc.invoke({"candidates": [
        {"entity": "溶洞塌陷", "entity_type": "地质构造", "relation": "诱发",
         "evidence": "微震源从-350m跳变至-280m", "confidence": 0.85}
    ], "reflection_id": "r-001", "workflow_run_id": "w-001"})
    check("知识候选：暂存 1 个候选", r["data"]["candidates_staged"] == 1)
    check("知识候选：pending_review", r["data"]["candidates"][0]["review_status"] == "pending_review")

    # 4e. 执行回执
    ef = _import_tool("tools.execution_feedback_tool", "query_feedback")
    r = ef.invoke({"actions": [
        {"sequence": 1, "action": "撤人", "status": "completed"},
        {"sequence": 2, "action": "加固支护", "status": "in_progress"},
        {"sequence": 3, "action": "注浆", "status": "failed"},
    ]})
    check("执行回执：completion_rate 1/3 = 0.333",
          abs(r["data"]["completion_rate"] - 0.333) < 0.01)
    check("执行回执：overall_status in_progress（完成率<0.5）",
          r["data"]["overall_status"] == "in_progress")

    # 4f. 方案评分
    sp = _import_tool("tools.plan_scoring_tool", "score_plans")
    r = sp.invoke({"plans": [
        {"plan_id": "p1", "name": "方案A", "compliance_score": 0.9, "resource_feasibility": 0.8, "historical_success_rate": 0.7, "estimated_minutes": 30},
        {"plan_id": "p2", "name": "方案B", "compliance_score": 0.5, "resource_feasibility": 0.9, "historical_success_rate": 0.6, "estimated_minutes": 90},
    ]})
    check("方案评分：2 个方案排序", len(r["data"]["ranked_plans"]) == 2)
    check("方案评分：A > B", r["data"]["ranked_plans"][0]["plan_id"] == "p1")

    # 4g. 合规检查
    cc = _import_tool("tools.compliance_check_tool", "check_compliance")
    r = cc.invoke({"actions": [
        {"sequence": 1, "action": "停产并撤出人员", "requires_approval": True},
        {"sequence": 2, "action": "加密支护", "requires_approval": False},
    ], "context": {"risk_level": "red"}})
    check("合规：返回 success", r["status"] == "success")
    check("合规：compliant 为 True（已审批）", r["data"]["compliant"] is True)

    # 4h. 资源核算
    cg = _import_tool("tools.resource_accounting_tool", "calculate_gaps")
    r = cg.invoke({
        "requirements": {"materials": [{"name": "锚杆", "count": 200, "unit": "根"}]},
        "available_resources": {"materials": [{"name": "锚杆", "count": 150, "unit": "根", "status": "available"}]},
    })
    check("资源核算：锚杆缺口 50 根", r["data"]["feasibility"] == "feasible_with_adjustment")

    # ========================================================================
    # 5. 返回值结构校验
    # ========================================================================
    print("\n=== 5. 返回值结构校验 ===")
    # 测试几个代表性工具的结构字段
    exec_sql = _import_tool("tools.db_tools", "execute_sql_query")
    r = exec_sql.invoke({"query": "DELETE FROM x"})
    check("DB 返回值含 status", "status" in r)
    check("DB 返回值含 audit", "audit" in r and "timestamp" in r["audit"])

    isearch = _import_tool("tools.tavily_tool", "internet_search")
    r = isearch.invoke({"query": "煤矿安全规程"})
    check("Tavily 返回值含 source_type", "source_type" in r)

    get_ast = _import_tool("tools.ragflow_tools", "get_assistant_list")
    r = get_ast.invoke({})
    check("RAGFlow 返回值含 source_type", "source_type" in r)

    # ========================================================================
    print(f"\n{'='*50}")
    print(f"  验收结果：{passed} PASS / {failed} FAIL")
    print(f"{'='*50}")
    return failed == 0


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)
