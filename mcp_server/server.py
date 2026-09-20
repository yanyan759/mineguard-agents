"""阶段 8 MCP 生产化：独立 FastMCP Server（stdio 传输）。

注册 schemas/mcp_tools.py 已冻结的 9 个工具（输入/输出 Pydantic 复用），
handler 确定性实现（fixture/规则驱动）。变更类工具（create_*）经 PermissionGate 审批。

启动：cd agent1 && python -m mcp_server.server
传输：stdio（tools/list / tools/call），可被任意 MCP 客户端发现调用。
本地包使用 mcp_server 名称，避免遮蔽 pip 提供的 mcp SDK，`import mcp` 可直接
解析到 SDK。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import os

# 本地包已更名 mcp_server，顶层 import mcp 直接解析到 pip SDK（不再遮蔽）
from mcp.server.fastmcp import FastMCP

from app.schemas.mcp_tools import (
    CreateDisposalWorkOrderInput, CreateDisposalWorkOrderOutput,
    CreateWarningRecordInput, CreateWarningRecordOutput,
    MCP_TOOL_REGISTRY,
    QueryAvailableWorkersInput, QueryAvailableWorkersOutput,
    QueryDisposalResultInput, QueryDisposalResultOutput,
    QueryEmergencyEquipmentInput, QueryEmergencyEquipmentOutput,
    QueryRoofRiskCaseInput, QueryRoofRiskCaseOutput,
    QuerySafetyRegulationInput, QuerySafetyRegulationOutput,
    QuerySensorHistoryInput, QuerySensorHistoryOutput,
    RunDigitalTwinSimulationInput, RunDigitalTwinSimulationOutput,
    DisposalFeedback, EquipmentInfo, Evidence, HistoricalCase,
    RegulationClause, SensorHistoryRow, SimulationResult, WorkOrderAction, WorkerInfo,
)
from app.schemas.workflow import RiskLevel
from mcp_server.permission import gate

mcp = FastMCP("coal-mine")

AGENT1_ROOT = Path(__file__).resolve().parent.parent  # agent1/（server.py 位于 agent1/mcp_server/ 下，两级 .parent 即达 agent1）
_FIXTURES = AGENT1_ROOT / "tests" / "fixtures"


def _load_yaml(name: str) -> dict:
    import yaml
    return yaml.safe_load((_FIXTURES / name).read_text(encoding="utf-8"))


def _mcp_result(status: str, data: Any, error: str | None = None) -> dict:
    """统一 MCP 工具返回信封（对齐 agent1 工具层约定：{status, data, error, audit}）。"""
    return {
        "status": status,
        "data": data,
        "error": error,
        "audit": {"tool": "mcp", "timestamp": datetime.now(timezone.utc).isoformat()},
    }


def _evidence(source_type: str, description: str, **kw) -> list[dict]:
    return [Evidence(source_type=source_type, description=description, **kw).model_dump()]


# ── 确定性数据台账（fixture 驱动）──
_WORKERS = [
    {"worker_id": "W001", "name": "张矿工", "role": "支护工", "shift": "早班", "location": "东翼201工作面", "certifications": ["支护作业证"], "available": True},
    {"worker_id": "W002", "name": "李监测员", "role": "监测工", "shift": "早班", "location": "中央监控室", "certifications": ["微震监测证"], "available": True},
    {"worker_id": "W003", "name": "王调度", "role": "调度员", "shift": "中班", "location": "调度室", "certifications": ["调度资格证"], "available": True},
    {"worker_id": "W004", "name": "赵救援", "role": "救护队员", "shift": "待命", "location": "井下基地", "certifications": ["矿山救护证"], "available": False},
]
_EQUIPMENT = [
    {"equipment_id": "E001", "name": "锚杆", "equipment_type": "support_material", "quantity": 200.0, "unit": "根", "location": "地面库", "status": "available", "last_checked_at": "2026-08-01T08:00:00"},
    {"equipment_id": "E002", "name": "单体液压支柱", "equipment_type": "support_material", "quantity": 50.0, "unit": "根", "location": "井下201库", "status": "available", "last_checked_at": "2026-08-01T08:00:00"},
    {"equipment_id": "E003", "name": "救生索", "equipment_type": "rescue_equipment", "quantity": 5.0, "unit": "套", "location": "救护基地", "status": "in_use", "last_checked_at": None},
    {"equipment_id": "E004", "name": "矿用对讲机", "equipment_type": "communication", "quantity": 30.0, "unit": "台", "location": "调度室", "status": "available", "last_checked_at": "2026-08-01T08:00:00"},
]
_WARNING_IDS: set[str] = set()
_WORK_ORDER_SEQ = 0


# ── 9 个 handler（确定性实现）──


def _query_sensor_history_impl(args: QuerySensorHistoryInput) -> dict:
    """确定性监测模拟：正弦曲线（无随机），sensor_types/时间窗驱动。"""
    import math
    from datetime import timedelta

    rows = []
    amps = {"microseismic": 200.0, "borehole_stress": 40.0, "roof_separation": 0.2,
            "hydraulic_support": 30.0, "roadway_displacement": 0.1}
    points = args.point_ids or [f"P{i+1}" for i in range(3)]
    step_s = max((args.end_time - args.start_time).total_seconds() / 30, 1.0)
    count = 0
    for i, stype in enumerate(args.sensor_types):
        for p, point_id in enumerate(points):
            amp = amps.get(stype, 1.0)
            t = args.start_time
            while t < args.end_time and count < args.limit:
                v = amp * (0.5 + 0.5 * math.sin(i * 1.3 + p + t.timestamp() / 3600.0))
                rows.append(SensorHistoryRow(
                    sensor_type=stype, point_id=point_id, timestamp=t, value=round(v, 3), unit="auto",
                ).model_dump())
                t += timedelta(seconds=step_s)
                count += 1
    out = QuerySensorHistoryOutput(
        rows=rows[:args.limit], total_count=count, truncated=count > args.limit,
        evidence=_evidence("simulator", "监测数据确定性模拟", value={"sensor_types": args.sensor_types}),
    )
    return _mcp_result("success", out.model_dump())


def _query_roof_risk_case_impl(args: QueryRoofRiskCaseInput) -> dict:
    """按风险等级/致灾因子过滤 accident_cases.yaml。"""
    cases = _load_yaml("accident_cases.yaml").get("cases", [])
    matched = []
    for c in cases:
        if args.risk_level and c.get("risk_level") != args.risk_level.value:
            continue
        if args.causal_factors:
            text = " ".join(c.get("causes", []))
            if not any(k in text for k in args.causal_factors):
                continue
        matched.append(HistoricalCase(
            case_id=c["case_id"], location=c.get("location", ""),
            occurred_at=datetime.fromisoformat(c["occurred_at"]),
            risk_level=RiskLevel(c["risk_level"]), causes=c.get("causes", []),
            precursors=c.get("precursors", []), actions=c.get("actions", []),
            outcome=c.get("outcome", ""), lessons=c.get("lessons", []), source=c.get("source", ""),
        ).model_dump())
    matched = matched[: args.top_k]
    out = QueryRoofRiskCaseOutput(
        cases=matched, total_matched=len(matched),
        evidence=_evidence("fixture_yaml", "accident_cases.yaml", value={"top_k": args.top_k}),
    )
    return _mcp_result("success", out.model_dump())


def _query_safety_regulation_impl(args: QuerySafetyRegulationInput) -> dict:
    """按关键词/强制筛选 regulations.yaml。"""
    regs = _load_yaml("regulations.yaml").get("regulations", [])
    matched = []
    for r in regs:
        if args.mandatory_only and not r.get("mandatory", True):
            continue
        text = f"{r.get('document', '')} {r.get('requirement', '')}"
        if not any(k in text for k in args.keywords):
            continue
        matched.append(RegulationClause(
            regulation_id=r.get("regulation_id", ""), document=r.get("document", ""),
            clause=r.get("clause", ""), requirement=r.get("requirement", ""),
            mandatory=r.get("mandatory", True),
            applicable_conditions=r.get("applicable_conditions", []),
            source_type=r.get("source_type", "safety_regulation"), source=r.get("source", ""),
        ).model_dump())
    matched = matched[: args.top_k]
    out = QuerySafetyRegulationOutput(
        clauses=matched, total_matched=len(matched),
        evidence=_evidence("fixture_yaml", "regulations.yaml"),
    )
    return _mcp_result("success", out.model_dump())


def _query_available_workers_impl(args: QueryAvailableWorkersInput) -> dict:
    """按角色/班次过滤人员台账。"""
    shift_map = {"current": {"早班"}, "next": {"中班"}, "all": set()}
    allowed = shift_map.get(args.shift, set())
    matched = [WorkerInfo(**w).model_dump() for w in _WORKERS
               if (not args.roles or w["role"] in args.roles)
               and (not allowed or w["shift"] in allowed)]
    out = QueryAvailableWorkersOutput(
        workers=matched, total_available=sum(1 for w in matched if w["available"]),
        evidence=_evidence("fixture", "人员台账（确定性）"),
    )
    return _mcp_result("success", out.model_dump())


def _query_emergency_equipment_impl(args: QueryEmergencyEquipmentInput) -> dict:
    """按类型/状态过滤装备台账。"""
    matched = []
    for it in _EQUIPMENT:
        if it["equipment_type"] not in args.equipment_types:
            continue
        if it["status"] not in args.status_filter:
            continue
        item = {**it}
        if item.get("last_checked_at"):
            item["last_checked_at"] = datetime.fromisoformat(item["last_checked_at"])
        matched.append(EquipmentInfo(**item).model_dump())
    out = QueryEmergencyEquipmentOutput(
        items=matched, total_available=sum(1 for x in matched if x["status"] == "available"),
        evidence=_evidence("fixture", "装备台账（确定性）"),
    )
    return _mcp_result("success", out.model_dump())


def _create_warning_record_impl(args: CreateWarningRecordInput) -> dict:
    """创建预警记录（变更工具：需审批）。"""
    ok, status = gate("create_warning_record", provided_token=os.getenv("MCP_APPROVAL_TOKEN"))
    if not ok:
        return _mcp_result("needs_approval", {}, "变更工具需审批（配置并携带 MCP_APPROVAL_TOKEN）")
    duplicate = args.warning_id in _WARNING_IDS
    _WARNING_IDS.add(args.warning_id)
    out = CreateWarningRecordOutput(
        warning_id=args.warning_id, created_at=datetime.now(timezone.utc),
        status="duplicate" if duplicate else "created",
        evidence=_evidence("workflow", "预警记录已创建（确定性落账）"),
    )
    return _mcp_result("success", out.model_dump())


def _create_disposal_work_order_impl(args: CreateDisposalWorkOrderInput) -> dict:
    """创建处置工单（变更工具：需审批）。"""
    global _WORK_ORDER_SEQ
    ok, status = gate("create_disposal_work_order", provided_token=os.getenv("MCP_APPROVAL_TOKEN"))
    if not ok:
        return _mcp_result("needs_approval", {}, "变更工具需审批（配置并携带 MCP_APPROVAL_TOKEN）")
    _WORK_ORDER_SEQ += 1
    wid = f"wo-{_WORK_ORDER_SEQ:04d}"
    actions = [WorkOrderAction(sequence=i + 1, action=a, status="pending").model_dump()
               for i, a in enumerate(args.actions)]
    out = CreateDisposalWorkOrderOutput(
        work_order_id=wid, warning_id=args.warning_id, status="pending", actions=actions,
        created_at=datetime.now(timezone.utc), approval_required=args.requires_approval,
        evidence=_evidence("workflow", "处置工单已生成（确定性落账）"),
    )
    return _mcp_result("success", out.model_dump())


def _run_digital_twin_simulation_impl(args: RunDigitalTwinSimulationInput) -> dict:
    """数字孪生确定性仿真（只读）。"""
    sim_id = f"sim-{args.workflow_run_id}-{args.simulation_type}"
    summaries = {
        "stress_field": "应力场仿真完成：顶板应力集中区位于东翼201工作面切眼外侧，峰值应力超允许值 12%",
        "displacement_field": "位移场仿真完成：两帮累计位移 48mm，处于黄色预警区间",
        "risk_overlay": "风险叠加仿真完成：综合风险等级 YELLOW，建议加强支护监测",
    }
    result = SimulationResult(
        simulation_id=sim_id, simulation_type=args.simulation_type, status="completed",
        result_url=f"/twin/{sim_id}",
        summary=summaries.get(args.simulation_type, "仿真完成"), warnings=[],
    ).model_dump()
    out = RunDigitalTwinSimulationOutput(
        simulation_id=sim_id, result=result,
        evidence=_evidence("simulator", "数字孪生确定性仿真"),
    )
    return _mcp_result("success", out.model_dump())


def _query_disposal_result_impl(args: QueryDisposalResultInput) -> dict:
    """查询处置执行回执（确定性反馈）。"""
    qid = args.work_order_id or args.warning_id or args.decision_id or "unknown"
    feedbacks = [
        DisposalFeedback(action="撤出危险区人员", assigned_to="赵救护", status="completed",
                         completed_at=datetime.fromisoformat("2026-08-16T09:30:00"),
                         result="已撤出 12 人", issues=[]).model_dump(),
        DisposalFeedback(action="加强支护", assigned_to="支护班组", status="in_progress",
                         completed_at=None, result="补打锚索 20 根", issues=["材料短缺 5 根"]).model_dump(),
    ]
    out = QueryDisposalResultOutput(
        query_id=qid, overall_status="in_progress", feedbacks=feedbacks,
        completion_rate=0.6, evidence=_evidence("workflow", "处置执行回执（确定性）"),
    )
    return _mcp_result("success", out.model_dump())


# ── 注册：handler ↔ tool_id 一一对应（与 MCP_TOOL_REGISTRY 契约一致）──
_HANDLERS: dict[str, Callable[[Any], dict]] = {
    "query_sensor_history": _query_sensor_history_impl,
    "query_roof_risk_case": _query_roof_risk_case_impl,
    "query_safety_regulation": _query_safety_regulation_impl,
    "query_available_workers": _query_available_workers_impl,
    "query_emergency_equipment": _query_emergency_equipment_impl,
    "create_warning_record": _create_warning_record_impl,
    "create_disposal_work_order": _create_disposal_work_order_impl,
    "run_digital_twin_simulation": _run_digital_twin_simulation_impl,
    "query_disposal_result": _query_disposal_result_impl,
}


def register_all() -> None:
    """把 9 个 handler 注册到 FastMCP（tools/list 可发现）。"""
    for tool_id, fn in _HANDLERS.items():
        mcp.add_tool(
            fn, name=tool_id,
            description=MCP_TOOL_REGISTRY[tool_id]["description"],
        )


register_all()


if __name__ == "__main__":
    mcp.run(transport="stdio")
