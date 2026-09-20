"""
Agent 执行过程监控模块

负责把工具调用、Agent 节点执行、任务结果和会话目录等事件统一包装后推送给前端
在 Web 服务中优先通过 WebSocket 定向推送；在脚本调试场景中保留控制台输出
"""

import asyncio
import builtins
import datetime
from typing import Any, Optional

from fastapi import WebSocket

from app.api.context import get_thread_context, get_workflow_run_id

    # 六 Agent 工作流事件类型
# 用于前端按 workflow_run_id 聚合各阶段的执行轨迹（开始/完成/失败/回退/审批/告警/回执）
AGENT_EVENT_TYPES = {
    "agent_start",
    "agent_complete",
    "agent_failed",
    "agent_rollback",
    "approval_required",
    "alert_triggered",
    "execution_feedback",
}


class ToolMonitor:
    """
    工具和助手调用的统一监控入口

    业务工具只需要导入全局 monitor，并调用 report_tool/report_assistant 等方法
    具体是通过 WebSocket 推送，还是输出到脚本运行时，由本类内部统一处理
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            cls._instance.websocket_manager = None
        return cls._instance

    def set_websocket_manager(self, manager: "ConnectionManager") -> None:
        """绑定 FastAPI WebSocket 连接管理器"""
        self.websocket_manager = manager

    def _emit(
        self,
        event_type: str,
        message: str,
        data: Optional[dict[str, Any]] = None,
        agent_name: Optional[str] = None,
    ) -> None:
        """
        构造统一监控事件，并尝试推送到当前 thread_id 对应的前端连接

        :param event_type: 事件类型，例如 tool_start、assistant_call、agent_start
        :param message: 面向前端展示的事件说明
        :param data: 附加结构化数据
        :param agent_name: 产生事件的 Agent 名称（六 Agent 事件必填）
        """
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
            "timestamp": datetime.datetime.now().isoformat(),
            # 所有事件统一携带 workflow_run_id / agent_name / thread_id，
            # 前端可据此按运行聚合六 Agent 各阶段执行轨迹
            "workflow_run_id": get_workflow_run_id(),
            "agent_name": agent_name,
            "thread_id": get_thread_context(),
        }

        if self.websocket_manager:
            try:
                thread_id = get_thread_context()
                manager_loop = self.websocket_manager.loop

                if manager_loop and not manager_loop.is_closed() and thread_id:
                    self._send_to_websocket(payload, thread_id, manager_loop)
            except Exception as e:
                print(f"[Monitor] WebSocket send failed: {e}")

        # 若运行时暴露了 stream_writer，则同步写入 LangGraph 流式输出。
        if hasattr(builtins, "runtime") and hasattr(builtins.runtime, "stream_writer"):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 控制台保底输出，便于无前端场景下观察执行过程
        print(f"\n[Monitor:{event_type}] {message}")

    def _send_to_websocket(
        self,
        payload: dict[str, Any],
        thread_id: str,
        manager_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        将监控事件投递到 WebSocket 所在事件循环

        FastAPI 的 WebSocket 必须在创建它的事件循环中发送消息
        如果当前代码已经在同一个循环里，直接 create_task；否则使用线程安全投递
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        coroutine = self.websocket_manager.send_to_thread(payload, thread_id)
        if current_loop and current_loop == manager_loop:
            current_loop.create_task(coroutine)
        else:
            asyncio.run_coroutine_threadsafe(coroutine, manager_loop)

    def report_tool(
        self,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> None:
        """报告开始执行某个工具"""
        self._emit(
            "tool_start",
            f"开始执行工具: {tool_name}",
            {"tool_name": tool_name, "args": args},
        )

    def report_assistant(
        self,
        assistant_name: str,
        args: Optional[dict[str, Any]] = None,
    ) -> None:
        """报告某个 Agent 节点开始执行"""
        self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args},
        )

    def report_task_result(self, result: str) -> None:
        """报告任务最终结果"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_task_cancelled(self) -> None:
        """报告任务已被用户取消"""
        self._emit("task_cancelled", "任务已取消")

    def report_session_dir(self, path: str) -> None:
        """报告当前任务工作目录"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})

    # ═══════════════════════════════════════════════════════════════════════
    # 六 Agent 工作流事件上报
    # 所有事件自动携带 workflow_run_id / agent_name / thread_id（见 _emit）
    # ═══════════════════════════════════════════════════════════════════════

    def report_agent_start(self, agent_name: str, **details: Any) -> None:
        """报告某个 Agent 开始执行（节点进入）"""
        self._emit(
            "agent_start",
            f"Agent 开始执行: {agent_name}",
            {"agent_name": agent_name, **details},
            agent_name=agent_name,
        )

    def report_agent_complete(self, agent_name: str, **details: Any) -> None:
        """报告某个 Agent 执行完成"""
        self._emit(
            "agent_complete",
            f"Agent 执行完成: {agent_name}",
            {"agent_name": agent_name, **details},
            agent_name=agent_name,
        )

    def report_agent_failed(self, agent_name: str, error: str = "", **details: Any) -> None:
        """报告某个 Agent 执行失败"""
        self._emit(
            "agent_failed",
            f"Agent 执行失败: {agent_name} {error}",
            {"agent_name": agent_name, "error": error, **details},
            agent_name=agent_name,
        )

    def report_agent_rollback(
        self,
        agent_name: str,
        from_agent: str = "",
        to_agent: str = "",
        reason: str = "",
        **details: Any,
    ) -> None:
        """报告工作流回退（反思/资源评估返回上游节点）"""
        self._emit(
            "agent_rollback",
            f"工作流回退: {from_agent} → {to_agent}（{reason}）",
            {
                "agent_name": agent_name,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "reason": reason,
                **details,
            },
            agent_name=agent_name,
        )

    def report_approval_required(self, agent_name: str, **details: Any) -> None:
        """报告高风险动作等待人工审批"""
        self._emit(
            "approval_required",
            f"等待人工审批: {agent_name}",
            {"agent_name": agent_name, **details},
            agent_name=agent_name,
        )

    def report_alert_triggered(self, agent_name: str, risk_level: str = "", **details: Any) -> None:
        """报告触发预警（红/橙高风险快速告警）"""
        self._emit(
            "alert_triggered",
            f"触发预警: {risk_level}",
            {"agent_name": agent_name, "risk_level": risk_level, **details},
            agent_name=agent_name,
        )

    def report_execution_feedback(self, agent_name: str, **details: Any) -> None:
        """报告执行回执/处置反馈"""
        self._emit(
            "execution_feedback",
            f"执行反馈: {agent_name}",
            {"agent_name": agent_name, **details},
            agent_name=agent_name,
        )


monitor = ToolMonitor()


class ConnectionManager:
    """
    WebSocket 连接管理器

    active_connections 使用 thread_id 作为 key，保证监控事件只推送给对应任务的前端连接
    """

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        # WebSocket 发送必须回到创建连接的事件循环，因此启动时需要显式绑定 loop
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定 FastAPI 主事件循环，并同步注册到 monitor"""
        self.loop = loop
        monitor.set_websocket_manager(self)
        print(f"[Monitor] ConnectionManager manually bound to loop: {id(self.loop)}")

    async def connect(self, websocket: WebSocket, thread_id: str) -> None:
        """接受 WebSocket 连接，并按 thread_id 保存"""
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        print(f"Client connected: {thread_id}")

    def disconnect(self, websocket: WebSocket, thread_id: str) -> None:
        """移除已经断开的 WebSocket 连接"""
        if self.active_connections.get(thread_id) is websocket:
            del self.active_connections[thread_id]
            print(f"Client disconnected: {thread_id}")
        else:
            print(f"Stale websocket disconnected, current connection kept: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        """向指定 WebSocket 发送纯文本消息"""
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict[str, Any], thread_id: str) -> None:
        """向指定 thread_id 对应的前端连接发送 JSON 消息"""
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)


manager = ConnectionManager()
