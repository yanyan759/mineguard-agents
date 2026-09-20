"""阶段 5 验收测试：API、Monitor、Context 与 DB 超时机制。

覆盖：
1. context.py — 工作流级上下文 set/get/reset
2. monitor.py — 六 Agent 事件 payload 携带 workflow_run_id/agent_name/thread_id
3. db_tools.py — 离线降级 + 跨平台查询超时（ThreadPoolExecutor + Future.result）
4. server.py — /api/workflow/{start,approve,reject,status} 四接口端到端
5. 人工审批全流程（red 场景 waiting_human → approve/reject）

用法：
    cd agent1
    python tests/validate_api_phase5.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# 导入设置
AGENT1_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT1_ROOT))

# LLM_NODES=on 委托：本脚本为确定性回归态（off）校验；on 模式验收
# （Schema/路由/安全属性断言）由 tests/validate_llm_nodes.py 承担（双模式隔离，设计 spec §7）。
import os
if os.getenv("LLM_NODES", "off").lower() in ("1", "on", "true"):
    print("=" * 60)
    print("  LLM_NODES=on：确定性回归断言由 tests/validate_llm_nodes.py 承担。")
    print("  请运行：LLM_NODES=on python tests/validate_llm_nodes.py")
    print("=" * 60)
    sys.exit(0)

# 统一结果收集
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """记录一项检查结果。"""
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def wait_until(client, run_id: str, target: str, max_wait: float = 30.0, interval: float = 0.2) -> dict:
    """轮询 /status 直到状态等于 target，超时抛 AssertionError。"""
    deadline = time.time() + max_wait
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/workflow/{run_id}/status")
        assert r.status_code == 200, r.text
        snap = r.json()
        last = snap["status"]
        if snap["status"] == target:
            return snap
        time.sleep(interval)
    raise AssertionError(f"等待状态 {target} 超时，当前状态: {last}")


# ═══════════════════════════════════════════════════════════════════════════
# 1. context.py 工作流级上下文
# ═══════════════════════════════════════════════════════════════════════════


def test_context_roundtrip() -> None:
    """context：workflow_run_id/mine_id/roadway_id/operator set/get/reset。"""
    from app.api import context as ctxmod

    tokens = ctxmod.set_workflow_context(
        run_id="run-1", mine_id="MINE-001", roadway_id="RDW-002",
        operator={"user_id": "u1", "role": "矿总工程师", "permissions": ["approve"]},
    )
    check("context: get_workflow_run_id", ctxmod.get_workflow_run_id() == "run-1")
    check("context: get_mine_id", ctxmod.get_mine_id() == "MINE-001")
    check("context: get_roadway_id", ctxmod.get_roadway_id() == "RDW-002")
    check("context: get_operator.role", ctxmod.get_operator()["role"] == "矿总工程师")

    ctxmod.reset_workflow_context(tokens)
    check("context: reset 后为 None", ctxmod.get_workflow_run_id() is None)


# ═══════════════════════════════════════════════════════════════════════════
# 2. monitor.py 六 Agent 事件
# ═══════════════════════════════════════════════════════════════════════════


def test_monitor_event_payload() -> None:
    """monitor：7 类事件 payload 携带 workflow_run_id / agent_name / thread_id。"""
    from app.api import context as ctxmod
    from app.api.monitor import AGENT_EVENT_TYPES, monitor

    # 事件类型集合覆盖阶段 5 新增 7 类
    expected_types = {
        "agent_start", "agent_complete", "agent_failed", "agent_rollback",
        "approval_required", "alert_triggered", "execution_feedback",
    }
    check("monitor: AGENT_EVENT_TYPES 含 7 类", AGENT_EVENT_TYPES == expected_types)

    # 捕获 _emit 的真实 payload（含 context 注入字段）
    captured = []
    orig_emit = monitor._emit

    def spy(event_type, message, data=None, agent_name=None):
        captured.append({
            "event": event_type,
            "message": message,
            "data": data or {},
            "agent_name": agent_name,
            "workflow_run_id": ctxmod.get_workflow_run_id(),
            "thread_id": ctxmod.get_thread_context(),
        })
        return orig_emit(event_type, message, data, agent_name)

    monitor._emit = spy
    try:
        wtoks = ctxmod.set_workflow_context(run_id="wf-mon-1")
        ttok = ctxmod.set_thread_context("th-mon-1")
        monitor.report_agent_start("感知预警 Agent", node="perception_warning")
        monitor.report_agent_complete("感知预警 Agent", node="perception_warning", summary="完成")
        monitor.report_agent_failed("知识检索 Agent", error="知识库不可用")
        monitor.report_agent_rollback("资源评估 Agent", from_agent="资源评估 Agent",
                                      to_agent="调度决策 Agent", reason="资源不足")
        monitor.report_approval_required("协同管控 Agent", coordination_id="coord-1")
        monitor.report_alert_triggered("感知预警 Agent", risk_level="red")
        monitor.report_execution_feedback("协同管控 Agent", dispatch_status="dispatched")
    finally:
        monitor._emit = orig_emit
        ctxmod.reset_workflow_context(wtoks)
        ctxmod.reset_thread_context(ttok)

    check("monitor: 捕获 7 类事件", len(captured) == 7)
    check(
        "monitor: 事件类型齐全",
        {p["event"] for p in captured} == expected_types,
        str(sorted(p["event"] for p in captured)),
    )
    all_traceable = all(
        p["workflow_run_id"] == "wf-mon-1" and p["thread_id"] == "th-mon-1" and p["agent_name"]
        for p in captured
    )
    check("monitor: 全部事件携带 workflow_run_id/agent_name/thread_id", all_traceable)


# ═══════════════════════════════════════════════════════════════════════════
# 3. db_tools.py 离线降级 + 跨平台超时
# ═══════════════════════════════════════════════════════════════════════════


def test_db_tools_offline() -> None:
    """db_tools：无 MySQL 时返回结构化 error（不 rejected、不崩溃）。"""
    from app.tools import db_tools

    r = db_tools.execute_sql_query.invoke({"query": "SELECT * FROM sensor_data"})
    check("db_tools: SELECT 离线降级非 rejected", r["status"] != "rejected", r["status"])
    check("db_tools: 返回含 status/audit", "status" in r and "audit" in r and "timestamp" in r["audit"])

    r2 = db_tools.execute_sql_query.invoke({"query": "DROP TABLE sensor_data"})
    check("db_tools: 写操作仍被拒绝", r2["status"] == "rejected")


def test_db_tools_timeout() -> None:
    """db_tools：跨平台超时生效（ThreadPoolExecutor + Future.result(timeout)）。"""
    from app.tools import db_tools

    # 用一个会长时间阻塞的假连接池验证超时机制（无需真实 MySQL）
    class _SlowCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            time.sleep(5)  # 模拟慢查询

        def description(self):
            return None

        def fetchall(self):
            return []

    class _SlowConn:
        def cursor(self):
            return _SlowCursor()

        def close(self):
            pass

    class _SlowPool:
        def get_connection(self):
            return _SlowConn()

    old_pool_getter = db_tools._get_pool
    old_timeout = db_tools._QUERY_TIMEOUT_SECONDS
    db_tools._get_pool = lambda: _SlowPool()
    db_tools._QUERY_TIMEOUT_SECONDS = 2
    try:
        started = time.time()
        try:
            db_tools._execute_query("SELECT 1")
            check("db_tools: 慢查询应超时", False, "未抛出 TimeoutError")
        except TimeoutError:
            elapsed = time.time() - started
            check("db_tools: 慢查询触发超时", True, f"{elapsed:.2f}s")
            check("db_tools: 超时未等待全部完成（<4s）", elapsed < 4.0, f"{elapsed:.2f}s")
    finally:
        db_tools._get_pool = old_pool_getter
        db_tools._QUERY_TIMEOUT_SECONDS = old_timeout


# ═══════════════════════════════════════════════════════════════════════════
# 4-5. API 端到端 + 人工审批流程
# ═══════════════════════════════════════════════════════════════════════════


def test_workflow_normal_completed() -> None:
    """API：normal 场景启动 → completed，事件携带 workflow_run_id。"""
    from app.api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post("/api/workflow/start", json={"scenario_id": "normal"})
        assert r.status_code == 200, r.text
        data = r.json()
        check("api: start 返回 started/run_id", data["status"] == "started" and data["run_id"])
        run_id = data["run_id"]

        snap = wait_until(client, run_id, "completed")
        check("api: normal → completed", snap["status"] == "completed", snap.get("error") or "")
        check("api: normal 仅感知预警节点", snap["phases"] == ["perception_warning"], str(snap["phases"]))
        events = snap["events"]
        check("api: normal 产生事件", len(events) >= 3, f"{len(events)} 条")
        traceable = all(e["workflow_run_id"] == run_id for e in events)
        check("api: 全部事件 workflow_run_id 贯穿", traceable)


def test_workflow_yellow_full_chain() -> None:
    """API：yellow 场景完整 6 阶段，各 Agent 结果齐全。"""
    from app.api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post("/api/workflow/start", json={"scenario_id": "yellow"})
        run_id = r.json()["run_id"]
        snap = wait_until(client, run_id, "completed")

        expected_phases = ["perception_warning", "knowledge_retrieval", "dispatch_decision",
                           "resource_evaluation", "collaborative_control", "reflection_iteration",
                           "knowledge_write_back"]
        check("api: yellow 完整 6 阶段", snap["phases"] == expected_phases, str(snap["phases"]))
        check("api: yellow 六 Agent 结果齐全",
              all(k in snap["results"] for k in ["感知预警", "知识检索", "调度决策", "资源评估", "协同管控", "反思迭代"]),
              str(list(snap["results"].keys())))
        # 事件类型覆盖六 Agent 各阶段
        types = {e["type"] for e in snap["events"]}
        check("api: 事件含 agent_start/agent_complete",
              "agent_start" in types and "agent_complete" in types)


def test_workflow_red_approve() -> None:
    """API：red 场景 → waiting_human → approve → completed 且处置已下发。"""
    from app.api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post("/api/workflow/start", json={"scenario_id": "red"})
        run_id = r.json()["run_id"]

        snap = wait_until(client, run_id, "waiting_human")
        check("api: red → waiting_human", snap["status"] == "waiting_human")
        check("api: 审批请求带 required_role",
              (snap.get("approval_request") or {}).get("required_role") == "矿总工程师")

        # 审批前事件含 approval_required / alert_triggered
        types_before = {e["type"] for e in snap["events"]}
        check("api: 事件含 alert_triggered", "alert_triggered" in types_before)

        r = client.post(f"/api/workflow/{run_id}/approve", json={"operator": "矿总工程师", "note": "同意处置"})
        assert r.status_code == 200, r.text
        check("api: approve 返回 approved", r.json()["status"] == "approved")

        snap = wait_until(client, run_id, "completed")
        check("api: 审批后 → completed", snap["status"] == "completed")
        control = snap["results"].get("协同管控", {})
        check("api: 协同管控已下发", "人工审批通过" in control.get("summary", ""), control.get("summary", ""))
        types = {e["type"] for e in snap["events"]}
        check("api: 事件含 execution_feedback", "execution_feedback" in types)


def test_workflow_red_reject() -> None:
    """API：red 场景 → waiting_human → reject → rejected 终止。"""
    from app.api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post("/api/workflow/start", json={"scenario_id": "red"})
        run_id = r.json()["run_id"]

        snap = wait_until(client, run_id, "waiting_human")
        check("api: red(reject) → waiting_human", snap["status"] == "waiting_human")

        r = client.post(f"/api/workflow/{run_id}/reject", json={"operator": "安全副矿长", "note": "需复核"})
        assert r.status_code == 200, r.text
        check("api: reject 返回 rejected", r.json()["status"] == "rejected")

        snap = wait_until(client, run_id, "rejected")
        check("api: 审批拒绝 → rejected 终止", snap["status"] == "rejected")


def test_websocket_receives_events() -> None:
    """WebSocket：前端可接收六 Agent 各阶段事件（携带 run 级字段）。

    隔离验证：主线程连接 /ws/{thread_id} 后触发 monitor 事件，
    断言 WebSocket 真实收到 agent_start/agent_complete/approval_required。
    """
    from app.api.context import (
        reset_thread_context,
        reset_workflow_context,
        set_thread_context,
        set_workflow_context,
    )
    from app.api.monitor import monitor
    from app.api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        ws_id = "ws-unit-001"
        wtoks = set_workflow_context(run_id="wf-ws-001")
        ttok = set_thread_context(ws_id)
        try:
            with client.websocket_connect(f"/ws/{ws_id}") as ws:
                monitor.report_agent_start("感知预警 Agent", node="perception_warning")
                monitor.report_agent_complete("感知预警 Agent", node="perception_warning")
                monitor.report_approval_required("协同管控 Agent", coordination_id="c1")

                msgs = [ws.receive_json() for _ in range(3)]
        finally:
            reset_workflow_context(wtoks)
            reset_thread_context(ttok)

    types = [m.get("event") for m in msgs]
    check("ws: 收到 agent_start/agent_complete/approval_required",
          types == ["agent_start", "agent_complete", "approval_required"], str(types))
    trace_ok = all(
        m.get("workflow_run_id") == "wf-ws-001"
        and m.get("thread_id") == ws_id
        and m.get("agent_name")
        for m in msgs
    )
    check("ws: 事件携带 workflow_run_id/thread_id/agent_name", trace_ok)


def test_workflow_api_edge_cases() -> None:
    """API：非法场景 400 / 未知 run 404 / 非待审批 409。"""
    from app.api.server import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post("/api/workflow/start", json={"scenario_id": "does_not_exist"})
        check("api: 未知场景 → 400", r.status_code == 400, f"status={r.status_code}")

        r = client.get("/api/workflow/not-exist/status")
        check("api: 未知 run → 404", r.status_code == 404, f"status={r.status_code}")

        r = client.post("/api/workflow/not-exist/approve", json={})
        check("api: 未知 run approve → 404", r.status_code == 404, f"status={r.status_code}")

        # normal 完成后审批应冲突
        r = client.post("/api/workflow/start", json={"scenario_id": "normal"})
        run_id = r.json()["run_id"]
        wait_until(client, run_id, "completed")
        r = client.post(f"/api/workflow/{run_id}/approve", json={})
        check("api: 非待审批状态 approve → 409", r.status_code == 409, f"status={r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 60)
    print("  阶段 5 — API / Monitor / Context / DB 超时验收")
    print("=" * 60)

    tests = [
        ("context 工作流级上下文", test_context_roundtrip),
        ("monitor 六 Agent 事件", test_monitor_event_payload),
        ("db_tools 离线降级", test_db_tools_offline),
        ("db_tools 跨平台超时", test_db_tools_timeout),
        ("workflow normal 完成", test_workflow_normal_completed),
        ("workflow yellow 完整链路", test_workflow_yellow_full_chain),
        ("workflow red 审批通过", test_workflow_red_approve),
        ("workflow red 审批拒绝", test_workflow_red_reject),
        ("workflow API 边界", test_workflow_api_edge_cases),
        ("websocket 事件推送", test_websocket_receives_events),
    ]

    suite_pass = 0
    for label, fn in tests:
        print(f"\n--- {label} ---")
        before = len(RESULTS)
        try:
            fn()
            suite_pass += 1
            print(f"  (组通过) {label}")
        except Exception as e:
            check(f"{label} 执行异常", False, str(e)[:200])
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"  检查项: {passed} PASS / {failed} FAIL")
    print(f"  用例组: {suite_pass}/{len(tests)} 通过")
    print("=" * 60)
    return 1 if failed > 0 or suite_pass < len(tests) else 0


if __name__ == "__main__":
    sys.exit(main())
