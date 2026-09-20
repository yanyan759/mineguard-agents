"""
六 Agent 工作流运行管理器

封装工作流在后台线程中的完整生命周期：启动、节点事件上报、人工审批中断与
恢复、状态查询。FastAPI 层只做 HTTP 映射，本模块对外暴露四个接口：

    start()       启动一次六 Agent 工作流（演示场景或实际快照驱动）
    approve()     人工审批确认（resume 工作流继续下发）
    reject()      人工审批拒绝（resume 工作流标记 rejected 终止）
    cancel()      取消工作流（waiting_human 注入取消决策 / running 置标记退出）
    get_status()  查询运行状态（节点轨迹 + 各 Agent 结果摘要 + 事件列表）

关键机制：
- 工作流在独立线程中同步驱动（LangGraph stream），不阻塞 FastAPI 事件循环
- 审批通过 LangGraph interrupt 暂停，由 threading.Event 跨线程唤醒恢复
- monitor 事件在构造时自动从 ContextVar 读取 workflow_run_id / thread_id / mine_id 等
  （工作流线程入口通过 set_workflow_context 绑定），无需逐事件传参
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from langgraph.types import Command

from app.agent.fixture_loader import FixtureDataSource
from app.agent.workflow import create_workflow
from app.agent.workflow_state import new_workflow_state
from app.api.context import (
    reset_thread_context,
    reset_workflow_context,
    set_thread_context,
    set_workflow_context,
)
from app.api.monitor import monitor
from app.utils.redis_lock import distributed_lock

# 节点名 → Agent 中文名映射（用于 monitor 事件与前端展示）
NODE_AGENT_NAMES = {
    "perception_warning": "感知预警 Agent",
    "knowledge_retrieval": "知识检索 Agent",
    "dispatch_decision": "调度决策 Agent",
    "resource_evaluation": "资源评估 Agent",
    "collaborative_control": "协同管控 Agent",
    "reflection_iteration": "反思迭代 Agent",
}

# 节点 → WorkflowState 中的阶段结果字段
_NODE_RESULT_KEY = {
    "perception_warning": "perception_result",
    "knowledge_retrieval": "knowledge_result",
    "dispatch_decision": "decision_result",
    "resource_evaluation": "resource_result",
    "collaborative_control": "control_result",
    "reflection_iteration": "reflection_result",
}


@dataclass
class WorkflowRunRecord:
    """单次工作流运行的运行时记录。"""

    run_id: str
    thread_id: str
    scenario_id: str
    query: Optional[str] = None              # 前端提交的真实监测任务文本
    actual_snapshot: Optional[dict] = None   # 准入时冻结，后台执行不读取后续版本
    status: str = "running"          # running | waiting_human | approved | rejected | completed | failed
    workflow: Optional[Any] = None   # 编译后的 StateGraph（必须复用同一实例以保留 InMemorySaver checkpoint）
    config: dict = field(default_factory=dict)
    # 审批唤醒使用 asyncio.Event；记录运行所在事件循环，支持同步线程入口与
    # FastAPI 异步入口之间的跨循环唤醒。
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_loop: Optional[asyncio.AbstractEventLoop] = None
    approval_request: Optional[dict] = None
    approval_decision: Optional[dict] = None
    phases: list[str] = field(default_factory=list)     # 已执行节点（含回退重入）
    events: list[dict] = field(default_factory=list)    # 事件摘要（供测试与 status 接口查询）
    final_state: Optional[dict] = None
    error: Optional[str] = None
    thread: Optional[threading.Thread] = None           # 同步 start() 路径的后台线程（测试 join 用）
    task: Optional[asyncio.Task] = None                 # async start_async() 路径的后台任务
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    cancel_requested: bool = False                      # 取消标记：运行中取消在节点边界退出
    cancelled_by: Optional[str] = None                  # 取消操作者（审计）


class WorkflowRunner:
    """六 Agent 工作流后台运行管理器（深模块：小接口，内部承载线程/中断/事件）。"""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRunRecord] = {}
        self._registration_lock = threading.RLock()

    # ═══════════════════════════════════════════════════════════════════════
    # 对外接口
    # ═══════════════════════════════════════════════════════════════════════

    def start(
        self,
        scenario_id: str = "yellow",
        run_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        mine_id: Optional[str] = None,
        roadway_id: Optional[str] = None,
        operator: Optional[dict] = None,
        query: Optional[str] = None,
        actual_snapshot: Optional[dict] = None,
    ) -> WorkflowRunRecord:
        """启动一次六 Agent 工作流（同步入口，向后兼容）。

        本方法保留为「守护线程 + 线程内 asyncio.run()」的同步包装，满足命令行、
        测试和已有同步调用方；FastAPI 端点使用 start_async() 在当前事件循环调度。

        :param scenario_id: 演示场景 ID；实际数据通过 snapshot 参数传入
        :param run_id: 工作流运行 ID；为空则自动生成（workflow_run_id）
        :param thread_id: WebSocket 定向推送的会话 ID；为空则复用 run_id
        :param mine_id/roadway_id/operator: 矿井/巷道/操作者上下文
        :return: 运行记录（status 初始为 running，后台线程异步推进）
        """
        run_id = run_id or f"wf-{uuid.uuid4().hex[:8]}"
        thread_id = thread_id or run_id
        # 分布式锁防同一 run_id 并发重复启动（跨进程）
        self._register_run(run_id, thread_id, scenario_id, query, actual_snapshot)
        record = self._runs[run_id]
        if record.status != "running" or record.thread is not None:
            return record
        # 守护线程 + 线程内事件循环驱动 async 工作流（保留真实 threading.Thread 供 join）
        worker = threading.Thread(
            target=lambda: asyncio.run(
                self._adrive_workflow(record, mine_id, roadway_id, operator, query)
            ),
            name=f"workflow-{run_id}",
            daemon=True,
        )
        record.thread = worker
        worker.start()
        return record

    async def start_async(
        self,
        scenario_id: str = "yellow",
        run_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        mine_id: Optional[str] = None,
        roadway_id: Optional[str] = None,
        operator: Optional[dict] = None,
        query: Optional[str] = None,
        actual_snapshot: Optional[dict] = None,
    ) -> WorkflowRunRecord:
        """启动一次六 Agent 工作流（async 入口，FastAPI 端点使用）。

        与 start() 语义一致，但直接在当前事件循环上 asyncio.create_task 驱动
        async 工作流（Agent 子图 / MCP client 均为 async-native），审批唤醒走
        asyncio.Event（同 loop 直接 set，跨 loop 由 _wake_approval 转 call_soon_threadsafe）。

        :return: 运行记录（status 初始为 running，后台任务异步推进）
        """
        run_id = run_id or f"wf-{uuid.uuid4().hex[:8]}"
        thread_id = thread_id or run_id
        # 分布式锁为同步阻塞实现：async 上下文经 asyncio.to_thread 规避阻塞事件循环
        # （REDIS_URL 未配置时 distributed_lock 为 no-op，to_thread 开销可忽略）
        await asyncio.to_thread(
            self._register_run, run_id, thread_id, scenario_id, query, actual_snapshot
        )
        record = self._runs[run_id]
        if record.status != "running" or record.task is not None or record.thread is not None:
            return record
        # async 路径：直接在当前 loop 上调度，无需独立线程
        record.task = asyncio.create_task(
            self._adrive_workflow(record, mine_id, roadway_id, operator, query)
        )
        return record

    def _register_run(self, run_id: str, thread_id: str, scenario_id: str, query: Optional[str] = None, actual_snapshot: Optional[dict] = None) -> None:
        """在分布式锁临界区内注册一次运行（防同 run_id 并发重复启动）。

        抽成同步小函数以便 async 入口经 asyncio.to_thread 调用。
        """
        with self._registration_lock, distributed_lock(f"run:{run_id}", timeout=5, ttl=60):
            existing = self._runs.get(run_id)
            if existing is not None:
                if (existing.scenario_id, existing.query, existing.thread_id) != (scenario_id, query, thread_id):
                    raise ValueError("运行编号已绑定其他输入或会话，请使用新编号")
                return
            from app.services.workflow_feedback import archive
            try:
                saved = archive.get_run(run_id)
            except KeyError:
                saved = None
            record = WorkflowRunRecord(
                run_id=run_id, thread_id=thread_id, scenario_id=scenario_id, query=query
            )
            if saved:
                snap = saved["status_snapshot"]
                if (saved["result"]["scenario_id"], saved["result"]["query"], snap["thread_id"]) != (scenario_id, query, thread_id):
                    raise ValueError("已归档运行编号不能绑定其他输入或会话")
                record.status = saved["result"]["status"]
                record.phases = snap["phases"]
                record.events = snap["events"]
            elif scenario_id == "custom":
                from app.services.mine_configuration import prepare_actual
                from app.services.monitoring_task import parse_monitoring_task
                record.actual_snapshot = actual_snapshot or prepare_actual(parse_monitoring_task(query))
            self._runs[run_id] = record

    async def approve(
        self, run_id: str, approved_by: Optional[str] = None, note: Optional[str] = None, verified_identity: Optional[dict] = None
    ) -> dict:
        """人工审批确认：恢复工作流继续下发处置指令。

        :return: {"ok": bool, "error": str|None}
        """
        record = self._get(run_id)
        if record.status != "waiting_human":
            return {
                "ok": False,
                "error": f"run {run_id} 当前状态为 {record.status}，非待审批状态",
            }
        if record.approval_event.is_set():
            return {"ok": False, "error": "该审批请求已被处理"}
        record.approval_decision = {
            "decision": "approved",
            "approved_by": approved_by,
            "note": note,
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "verified_identity": verified_identity,
        }
        record.status = "approved"
        await self._wake_approval(record)
        return {"ok": True}

    async def reject(
        self, run_id: str, rejected_by: Optional[str] = None, note: Optional[str] = None
    ) -> dict:
        """人工审批拒绝：恢复工作流并标记处置为 rejected 终止。"""
        record = self._get(run_id)
        if record.status != "waiting_human":
            return {
                "ok": False,
                "error": f"run {run_id} 当前状态为 {record.status}，非待审批状态",
            }
        if record.approval_event.is_set():
            return {"ok": False, "error": "该审批请求已被处理"}
        record.approval_decision = {
            "decision": "rejected",
            "rejected_by": rejected_by,
            "note": note,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        record.status = "rejected"
        await self._wake_approval(record)
        return {"ok": True}

    async def cancel(
        self, run_id: str, cancelled_by: Optional[str] = None, note: Optional[str] = None
    ) -> dict:
        """取消一次工作流运行。

        两种取消路径（结构完整，语义分级）：
        - waiting_human：注入 decision="cancelled" 决策并唤醒审批等待，协同管控节点
          以 rejected 终止，_finalize 保留 cancelled 终态
        - running：置 cancel_requested 标记 + 取消 async 任务（async 路径），
          工作流在节点边界退出；守护线程路径在下一节点边界观察到标记后终止
        :return: {"ok": bool, "error": str|None}
        """
        record = self._get(run_id)
        if record.status in ("completed", "failed", "cancelled", "rejected"):
            return {
                "ok": False,
                "error": f"run {run_id} 已处于终态 {record.status}，无法取消",
            }
        was_waiting_human = record.status == "waiting_human"
        record.status = "cancelled"
        record.cancel_requested = True
        record.cancelled_by = cancelled_by
        self._emit(
            record,
            "workflow_cancelled",
            "六 Agent 工作流",
            message=f"工作流已取消（{cancelled_by or '操作者未记录'}）",
            data={"final_status": "cancelled", "cancelled_by": cancelled_by, "note": note},
        )

        if was_waiting_human:
            # 等待审批中：注入取消决策并唤醒，协同管控节点以 rejected 终止
            record.approval_decision = {
                "decision": "cancelled",
                "cancelled_by": cancelled_by,
                "note": note,
                "decided_at": datetime.now(timezone.utc).isoformat(),
            }
            await self._wake_approval(record)
        elif record.task is not None:
            # 运行中（async 路径）：取消底层任务（CancelledError 在 finally 释放上下文）
            record.task.cancel()
        # 运行中（守护线程路径）：依赖 cancel_requested 标记在节点边界退出
        return {"ok": True}

    @staticmethod
    async def _wake_approval(record: WorkflowRunRecord) -> None:
        """唤醒等待审批的 asyncio.Event。

        start_async 路径：工作流与 approve/reject 同 loop，直接 set。
        start() 同步路径：工作流跑在独立线程的独立 loop 上，需经
        loop.call_soon_threadsafe 跨线程唤醒（asyncio.Event 非线程安全）。
        """
        loop = record.approval_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(record.approval_event.set)
        else:
            record.approval_event.set()

    def get_status(self, run_id: str) -> dict:
        """查询工作流运行状态快照。"""
        record = self._runs.get(run_id)
        if record is None or (record.final_state is None and record.status not in {"running", "waiting_human", "approved"}):
            from app.services.workflow_feedback import archive
            try:
                return archive.get_run(run_id)["status_snapshot"]
            except KeyError:
                if record is None: raise
        return self._snapshot(self._get(run_id))

    # ═══════════════════════════════════════════════════════════════════════
    # 后台线程主循环
    # ═══════════════════════════════════════════════════════════════════════

    async def _adrive_workflow(
        self,
        record: WorkflowRunRecord,
        mine_id: Optional[str] = None,
        roadway_id: Optional[str] = None,
        operator: Optional[dict] = None,
        query: Optional[str] = None,
    ) -> None:
        """工作流驱动协程：astream 驱动 + asyncio.Event 审批等待。

        start_async 路径由 FastAPI 端点在自身 loop 上 create_task；
        start() 同步路径在守护线程内经 asyncio.run() 调用（独立 loop）。
        两路径共用本协程，审批唤醒见 _wake_approval 的跨 loop 处理。
        """
        # 记录运行 loop：供 approve/reject 跨线程唤醒使用（见 _wake_approval）
        record.approval_loop = asyncio.get_running_loop()
        # 绑定 run 级上下文：线程内所有 monitor 事件自动携带 workflow_run_id/thread_id 等
        ctx_tokens = set_workflow_context(
            run_id=record.run_id,
            mine_id=mine_id,
            roadway_id=roadway_id,
            operator=operator,
        )
        thread_token = set_thread_context(record.thread_id)
        try:
            custom_task = None
            if record.scenario_id == "custom":
                from app.services.monitoring_task import parse_monitoring_task
                from app.schemas.workflow import WorkflowContext

                custom_task = parse_monitoring_task(query or record.query)
                base_ctx = WorkflowContext(
                    workflow_run_id=record.run_id, trace_id=record.run_id,
                    thread_id=record.thread_id, mine_id=custom_task.mine_id,
                    roadway_id=custom_task.roadway_id, started_at=datetime.now(timezone.utc),
                )
            else:
                loader = FixtureDataSource()
                scenario_input = loader.load_scenario(record.scenario_id)
                base_ctx = scenario_input.context
            # 以 fixture 上下文为底，覆盖 run 级标识与外部传入的矿井/操作者信息
            ctx = base_ctx.model_copy(
                update={
                    "workflow_run_id": record.run_id,
                    "thread_id": record.thread_id,
                    "iteration_count": 0,
                    "max_iterations": 2,
                    "mine_id": mine_id or base_ctx.mine_id,
                    "roadway_id": roadway_id or base_ctx.roadway_id,
                    "operator": operator
                    or (base_ctx.operator.model_dump() if base_ctx.operator else None),
                }
            )
            state = new_workflow_state(ctx, scenario_id=None if custom_task else record.scenario_id)
            state["custom_task"] = custom_task.model_dump(mode="json") if custom_task else None
            if custom_task:
                state["actual_snapshot"] = record.actual_snapshot
                state["custom_task"] = record.actual_snapshot["task"]
            if custom_task and getattr(custom_task, "input_type", None) == "raw_events":
                from app.services.raw_monitoring import select_profile
                from app.services.monitoring_files import verify_input_files
                from pathlib import Path
                state["assessment_profile"] = select_profile(custom_task.method_id)
                state["input_file_evidence"] = verify_input_files(Path(__file__).resolve().parents[1]/"updated", record.thread_id, custom_task)
            state["user_query"] = query or record.query
            workflow = create_workflow()
            record.workflow = workflow
            # require_approval=True：启用协同管控节点的人工审批中断
            record.config = {
                "configurable": {
                    "thread_id": record.run_id,
                    "require_approval": True,
                }
            }

            self._emit(
                record,
                "workflow_start",
                "六 Agent 工作流",
                message=f"工作流启动: 场景 {record.scenario_id}",
                data={"scenario_id": record.scenario_id, "run_id": record.run_id, "query": query or record.query,
                      "input_source": getattr(custom_task, "input_type", "user_monitoring_summary") if custom_task else "fixture",
                      "parsed_input": state["custom_task"]},
            )

            # ── 主循环：astream → 遇 interrupt 等待审批 → Command(resume) 继续 ──
            stream_input = state
            while True:
                pending_interrupt = None
                async for stream_mode, event in workflow.astream(stream_input, record.config, stream_mode=["updates", "custom"]):
                    if stream_mode == "custom":
                        name = event.get("node_started")
                        if name:
                            self._emit(record, "agent_start", NODE_AGENT_NAMES.get(name, name),
                                       message=f"Agent 开始执行: {NODE_AGENT_NAMES.get(name, name)}",
                                       data={"node": name, "phase": name, "started_at": event.get("started_at")})
                        continue
                    for node_name, node_output in event.items():
                        if node_name == "__interrupt__":
                            pending_interrupt = node_output
                        else:
                            self._handle_node_event(record, node_name, node_output)
                    # 取消观察：运行中取消在节点边界立即退出（async 路径）
                    if record.cancel_requested:
                        break

                # 取消观察：节点循环退出后再次确认
                if record.cancel_requested:
                    break

                # 检查是否还有 pending 节点（即 interrupt 等待中）
                snap = await workflow.aget_state(record.config)
                record.final_state = snap.values
                if pending_interrupt is not None:
                    # 状态快照就绪后才对外显示待审批，查询者始终能读到此前节点结果。
                    self._handle_interrupt(record, pending_interrupt)
                if not snap.next:
                    break

                # 有人工审批待处理 → 发 approval_required 事件并阻塞等待
                self._emit(
                    record,
                    "approval_required",
                    "协同管控 Agent",
                    message=f"等待人工审批: {record.approval_request.get('coordination_id', record.run_id) if record.approval_request else record.run_id}",
                    data=record.approval_request or {},
                )
                await record.approval_event.wait()
                record.approval_event.clear()
                stream_input = Command(resume=record.approval_decision)

            self._finalize(record)
        except Exception as e:  # 工作流异常 → 标记 failed，不中断服务
            import traceback
            record.status = "failed"
            record.error = str(e)
            self._emit(
                record,
                "workflow_failed",
                "六 Agent 工作流",
                message=f"工作流异常终止: {e}",
                data={"error": str(e)},
            )
            print(f"[WorkflowRunner] run {record.run_id} failed: {e}")
            traceback.print_exc()
        finally:
            record.finished_at = record.finished_at or datetime.now(timezone.utc)
            if record.status in {"failed", "cancelled"}:
                # 失败/取消也保留终态供查询和幂等重试；未完成节点不伪造结果。
                from app.services.workflow_feedback import archive
                final = record.final_state or {}
                archive.save_run(record.run_id, {"result":self._build_result_payload(record.run_id,final,record),
                    "input":final.get("custom_task"), "status_snapshot":self._snapshot(record)})
            reset_workflow_context(ctx_tokens)
            reset_thread_context(thread_token)

    # ═══════════════════════════════════════════════════════════════════════
    # 事件处理
    # ═══════════════════════════════════════════════════════════════════════

    def _handle_interrupt(self, record: WorkflowRunRecord, interrupt_payload: Any) -> None:
        """处理 LangGraph 中断：记录审批请求并把 run 置为等待人工审批。"""
        if not interrupt_payload:
            return
        value = getattr(interrupt_payload[0], "value", None)
        if isinstance(value, dict) and value.get("type") == "approval_request":
            record.approval_request = value
            record.status = "waiting_human"
        else:
            # 非审批类中断（未来扩展），仅记录
            record.approval_request = value
            record.status = "waiting_human"

    def _handle_node_event(
        self, record: WorkflowRunRecord, node_name: str, node_output: Any
    ) -> None:
        """处理单个节点输出：上报 Agent 开始/完成/失败/回退/告警/回执事件。"""
        if not isinstance(node_output, dict):
            return
        agent_name = NODE_AGENT_NAMES.get(node_name, node_name)
        record.phases.append(node_name)

        phase = node_output.get("current_phase")
        msgs = node_output.get("messages") or []
        summary = ""
        if msgs:
            last = msgs[-1]
            summary = last.get("content", "") if isinstance(last, dict) else str(last)

        result = node_output.get(_NODE_RESULT_KEY.get(node_name, ""))
            # 节点事件补充展示字段（置信度/执行耗时/证据来源），供前端看板渲染
        data = {
            "node": node_name,
            "phase": phase,
            "summary": summary,
            "confidence": getattr(result, "confidence", None) if result is not None else None,
            "evaluation_basis": getattr(result, "evaluation_basis", None) if result is not None else None,
            "duration_ms": getattr(result, "duration_ms", None) if result is not None else None,
            "evidence": (
                [
                    {"source_type": e.source_type, "description": e.description}
                    for e in result.evidence
                ]
                if result is not None and getattr(result, "evidence", None)
                else []
            ),
        }

        # 快速告警（感知预警红/橙）
        if node_name == "perception_warning" and result is not None and getattr(result, "fast_alert_required", False):
            self._emit(
                record, "alert_triggered", agent_name,
                message=f"触发预警: {result.risk_level.value if result.risk_level else '研究方法报警'}",
                data={"risk_level": result.risk_level.value if result.risk_level else None, "risk_score": result.risk_score,
                      "warning_id": result.warning_id},
            )

        # 回退事件（资源不足 / 反思定向返回）
        rollback: Optional[tuple[str, str]] = None
        if node_name == "resource_evaluation" and result is not None and getattr(result, "redecision_required", False):
            rollback = ("资源不可行，需调整方案", "dispatch_decision")
        elif node_name == "reflection_iteration" and result is not None:
            rd = getattr(result, "return_decision", None)
            if rd is not None and rd.action.startswith("return_to_"):
                rollback = (rd.reason or "反思建议返回上游", rd.target_agent)
        if rollback:
            reason, to_agent = rollback
            self._emit(
                record, "agent_rollback", agent_name,
                message=f"工作流回退: {agent_name} → {to_agent}（{reason}）",
                data={"from_agent": agent_name, "to_agent": to_agent, "reason": reason},
            )

        # 执行回执（协同管控下发成功）
        if node_name == "collaborative_control" and result is not None and getattr(result, "dispatch_status", "") == "dispatched":
            self._emit(
                record, "execution_feedback", agent_name,
                message=f"处置指令已下发: {len(result.dispatched_actions or [])} 个动作",
                data={"dispatch_status": result.dispatch_status, "dispatched_actions": result.dispatched_actions or []},
            )

        # 节点失败
        errors = getattr(result, "errors", None) if result is not None else None
        if errors and getattr(result, "status", None) is not None and getattr(result, "status").value == "failed":
            self._emit(record, "agent_failed", agent_name, message=f"Agent 执行失败: {agent_name}", data={"errors": errors})

        # 节点完成
        self._emit(record, "agent_complete", agent_name, message=f"Agent 执行完成: {agent_name}", data=data)

    def _finalize(self, record: WorkflowRunRecord) -> None:
        """工作流跑完后的收尾：根据协同管控结果确定最终状态。

        取消为优先终态：cancel() 已置 status=cancelled 并发 workflow_cancelled 事件，
        此处不再覆盖（协同管控节点 resume 后 dispatch_status=rejected 也不改写取消态）。
        """
        if record.status == "cancelled" or record.cancel_requested:
            record.status = "cancelled"
            # cancel() 已发 workflow_cancelled（async 路径任务取消后 _finalize 不运行），
            # 这里仅在未发过时补发（waiting_human 路径 resume 后走到 _finalize 的守卫）。
            if not any(e.get("type") == "workflow_cancelled" for e in record.events):
                self._emit(
                    record, "workflow_cancelled", "六 Agent 工作流",
                    message="工作流已取消",
                    data={"final_status": "cancelled", "cancelled_by": record.cancelled_by},
                )
            return
        final = record.final_state or {}
        control = final.get("control_result")
        dispatch_status = getattr(control, "dispatch_status", None) if control else None

        if dispatch_status == "rejected":
            record.status = "rejected"
        elif record.status in ("running", "approved"):
            record.status = "completed"

        # 保存不可覆盖的终态快照，供重启后查看与反馈复核；不冒充运行中检查点。
        from app.services.workflow_feedback import archive
        record.finished_at = datetime.now(timezone.utc)
        self._emit(record, "workflow_complete", "六 Agent 工作流", message=f"工作流结束: {record.status}",
                   data={"final_status":record.status}, publish=False)
        archive.save_run(record.run_id, {"result": self._build_result_payload(record.run_id, final, record),
                                        "input": final.get("custom_task"), "actual_snapshot":final.get("actual_snapshot"),
                                        "status_snapshot": self._snapshot(record)})
        if final.get("actual_snapshot"):
            from app.services.mine_configuration import ConfigurationStore
            used = {e.source_id for key in ("knowledge_result", "decision_result", "resource_result")
                    for e in getattr(final.get(key), "evidence", [])}
            ConfigurationStore().mark_used(record.run_id,[a for a in final["actual_snapshot"]["documents"] if a["asset_id"] in used])
        self._push_monitor("workflow_complete", "六 Agent 工作流", f"工作流结束: {record.status}", {"final_status":record.status})

    def _emit(
        self,
        record: WorkflowRunRecord,
        event_type: str,
        agent_name: str,
        message: Optional[str] = None,
        data: Optional[dict] = None,
        publish: bool = True,
    ) -> dict:
        """记录事件摘要（record.events）并同步推送到 monitor（WebSocket）。"""
        event = {
            "type": event_type,
            "agent_name": agent_name,
            "workflow_run_id": record.run_id,
            "thread_id": record.thread_id,
            "message": message or "",
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        record.events.append(event)
        if publish:
            self._push_monitor(event_type, agent_name, message or "", data or {})
        return event

    def _push_monitor(self, event_type: str, agent_name: str, message: str, data: dict) -> None:
        """把事件推送给 monitor（内部从 ContextVar 读取 run 级字段拼 payload）。

        任一事件推送失败仅打印告警，不影响工作流主流程。
        """
        d = dict(data)
        try:
            if event_type == "agent_start":
                monitor.report_agent_start(agent_name, **d)
            elif event_type == "agent_complete":
                monitor.report_agent_complete(agent_name, **d)
            elif event_type == "agent_failed":
                monitor.report_agent_failed(agent_name, error=str(d.pop("errors", "")), **d)
            elif event_type == "agent_rollback":
                monitor.report_agent_rollback(
                    agent_name,
                    from_agent=str(d.pop("from_agent", "")),
                    to_agent=str(d.pop("to_agent", "")),
                    reason=str(d.pop("reason", "")),
                    **d,
                )
            elif event_type == "approval_required":
                monitor.report_approval_required(agent_name, **d)
            elif event_type == "alert_triggered":
                monitor.report_alert_triggered(agent_name, risk_level=str(d.pop("risk_level", "")), **d)
            elif event_type == "execution_feedback":
                monitor.report_execution_feedback(agent_name, **d)
            elif event_type == "workflow_start":
                monitor._emit("workflow_start", message, d, agent_name=agent_name)
            elif event_type == "workflow_complete":
                monitor._emit("workflow_complete", message, d, agent_name=agent_name)
            elif event_type == "workflow_failed":
                monitor._emit("workflow_failed", message, d, agent_name=agent_name)
        except Exception as e:
            print(f"[WorkflowRunner] monitor push failed: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # 内部辅助
    # ═══════════════════════════════════════════════════════════════════════

    def _get(self, run_id: str) -> WorkflowRunRecord:
        if run_id not in self._runs:
            raise KeyError(run_id)
        return self._runs[run_id]

    async def get_result(self, run_id: str) -> dict:
        """从 Redis checkpoint 恢复工作流完整结果（六 Agent 完整 Pydantic 数据）。

        优先从内存 _runs 获取（服务器未重启时），否则从 Redis checkpoint
        通过 CoalMineRedisSaver 恢复最终 WorkflowState，提取六个 Agent
        的完整 Pydantic model_dump() 结果。

        :raises KeyError: run_id 在内存记录和 Redis checkpoint 中均不存在
        """
        # 优先从内存获取（服务器未重启，record.final_state 存在）
        record = self._runs.get(run_id)
        if record is not None and record.final_state is not None:
            return self._build_result_payload(run_id, record.final_state, record)

        # 内存无记录，从 Redis checkpoint 恢复（进程已重启）
        from app.services.workflow_feedback import archive
        try:
            return archive.get_run(run_id)["result"]
        except KeyError:
            pass
        final_state = await self._restore_from_checkpoint(run_id)
        if final_state is None:
            raise KeyError(f"结果不可用: {run_id} 在内存和 Redis checkpoint 中均不存在")
        return self._build_result_payload(run_id, final_state, None)

    async def _restore_from_checkpoint(self, run_id: str) -> dict | None:
        """从 Redis checkpoint 恢复最终 WorkflowState（异步，仅当内存无记录时调用）。"""
        from app.agent.checkpoint import get_checkpointer

        try:
            cp = get_checkpointer()
            config = {"configurable": {"thread_id": run_id}}
            tup = await cp.aget_tuple(config)
            if tup is None:
                return None
            return tup.checkpoint.get("channel_values", {})
        except Exception as exc:
            print(f"[WorkflowRunner] checkpoint 恢复失败 ({run_id}): {exc}")
            return None

    def _build_result_payload(self, run_id: str, final_state: dict, record: object) -> dict:
        """从 WorkflowState 构造结果载荷，序列化所有 Pydantic 模型为 JSON-safe dict。"""
        # Pydantic 模型 → JSON-safe dict 的安全序列化
        def safe_dump(obj):
            if hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
            return obj

        # 六 Agent 字段提取（与 _snapshot 一致的 key 映射）
        agent_keys = {
            "perception_result": "perception_result",
            "knowledge_result": "knowledge_result",
            "decision_result": "decision_result",
            "resource_result": "resource_result",
            "control_result": "control_result",
            "reflection_result": "reflection_result",
        }
        agents = {}
        for state_key, label in agent_keys.items():
            val = final_state.get(state_key)
            if val is not None:
                agents[label] = safe_dump(val)

        # 审批决策序列化
        approval_decision = final_state.get("approval_decision") or getattr(record, "approval_decision", None)
        if hasattr(approval_decision, "model_dump"):
            approval_decision = approval_decision.model_dump(mode="json")

        # 知识写回结果序列化
        knowledge_writeback = final_state.get("knowledge_writeback_result")
        if hasattr(knowledge_writeback, "model_dump"):
            knowledge_writeback = knowledge_writeback.model_dump(mode="json")

        # 迭代信息提取
        iteration_count = final_state.get("iteration_count", 0)
        max_iterations = final_state.get("max_iterations", 2)

        # 运行元数据（优先从 record，否则从 state 兜底）
        now = datetime.now(timezone.utc)
        scenario_id = (
            getattr(record, "scenario_id", None)
            or final_state.get("scenario_id")
            or final_state.get("fixture_scenario_id")
            or ""
        )
        started_at = getattr(record, "started_at", now)
        finished_at = getattr(record, "finished_at", now)

        return {
            "run_id": run_id,
            "scenario_id": scenario_id or "",
            "provenance": final_state.get("actual_snapshot"),
            "query": getattr(record, "query", None) if record else None,
            "status": getattr(record, "status", "completed") if record else "completed",
            "started_at": started_at.isoformat() if isinstance(started_at, datetime) else str(started_at),
            "finished_at": finished_at.isoformat() if isinstance(finished_at, datetime) else str(finished_at) if finished_at else None,
            "agents": agents,
            "approval_decision": approval_decision,
            "knowledge_writeback": knowledge_writeback,
            "iteration_count": iteration_count,
            "max_iterations": max_iterations,
        }

    def _snapshot(self, record: WorkflowRunRecord) -> dict:
        """构造 status 接口返回的快照。"""
        final = record.final_state or {}
        results = {}
        for key, label in [
            ("perception_result", "感知预警"),
            ("knowledge_result", "知识检索"),
            ("decision_result", "调度决策"),
            ("resource_result", "资源评估"),
            ("control_result", "协同管控"),
            ("reflection_result", "反思迭代"),
        ]:
            r = final.get(key)
            if r is not None:
                results[label] = {
                    "status": r.status.value,
                    "summary": r.summary,
                    "confidence": r.confidence,
                    "next_action": r.next_action,
                }
        return {
            "run_id": record.run_id,
            "thread_id": record.thread_id,
            "scenario_id": record.scenario_id,
            "query": record.query,
            "status": record.status,
            "phases": record.phases,
            "results": results,
            "approval_request": record.approval_request,
            "approval_decision": record.approval_decision,
            "events": record.events[-50:],
            "error": record.error,
            "started_at": record.started_at.isoformat(),
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        }


# 模块级单例：API 层与测试共用同一运行注册表
runner = WorkflowRunner()
