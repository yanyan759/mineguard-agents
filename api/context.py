"""
请求上下文管理模块

负责在异步请求链路中保存当前任务的 thread_id、session_dir，以及六 Agent 工作流
的 run 级追踪信息（workflow_run_id、mine_id、roadway_id、operator）。
工具、智能体和监控模块可以在深层调用中读取这些值，而不需要层层传参。
"""

from contextvars import ContextVar, Token
from typing import Optional

# ContextVar 是协程级上下文变量，适合 FastAPI 这类异步 Web 服务
# 它可以避免多个并发请求共用全局变量时出现 thread_id 或 session_dir 串台
_session_dir_ctx: ContextVar[Optional[str]] = ContextVar(
    "session_dir",
    default=None,
)
_thread_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "thread_id",
    default=None,
)

# ── 六 Agent 工作流 run 级上下文 ──
# workflow_run_id 贯穿一次工作流运行的所有 monitor 事件，前端据此聚合各阶段进度；
# mine_id / roadway_id 标记矿井与巷道归属；operator 记录当前操作者（用户身份）。
_workflow_run_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "workflow_run_id",
    default=None,
)
_mine_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "mine_id",
    default=None,
)
_roadway_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "roadway_id",
    default=None,
)
_operator_ctx: ContextVar[Optional[dict]] = ContextVar(
    "operator",
    default=None,
)


def set_session_context(path: str) -> Token[Optional[str]]:
    """
    设置当前请求链路的会话目录

    :param path: 当前任务的工作目录
    :return: reset 时需要使用的上下文 token
    """
    return _session_dir_ctx.set(path)


def get_session_context() -> Optional[str]:
    """
    获取当前请求链路的会话目录

    :return: 当前任务工作目录；未设置时返回 None
    """
    return _session_dir_ctx.get()


def set_thread_context(thread_id: str) -> Token[Optional[str]]:
    """
    设置当前请求链路的线程 ID

    :param thread_id: 前端连接和 Agent 执行共用的任务 ID
    :return: reset 时需要使用的上下文 token
    """
    return _thread_id_ctx.set(thread_id)


def get_thread_context() -> Optional[str]:
    """
    获取当前请求链路的线程 ID

    :return: 当前任务 ID；未设置时返回 None
    """
    return _thread_id_ctx.get()


# ── 工作流 run 级上下文 setter/getter ──


def set_workflow_context(
    run_id: Optional[str] = None,
    mine_id: Optional[str] = None,
    roadway_id: Optional[str] = None,
    operator: Optional[dict] = None,
) -> tuple:
    """
    设置六 Agent 工作流的 run 级追踪上下文。

    在工作流后台线程入口调用一次，线程内所有 monitor 事件都会自动携带
    workflow_run_id 等字段，无需在每个事件里手动传参。

    :param run_id: 工作流运行 ID（workflow_run_id）
    :param mine_id: 矿井 ID
    :param roadway_id: 巷道 ID
    :param operator: 操作者信息 {user_id, role, permissions}
    :return: 4 个上下文 token，用于 reset_workflow_context 恢复
    """
    return (
        _workflow_run_id_ctx.set(run_id),
        _mine_id_ctx.set(mine_id),
        _roadway_id_ctx.set(roadway_id),
        _operator_ctx.set(operator),
    )


def get_workflow_run_id() -> Optional[str]:
    """获取当前工作流运行 ID（workflow_run_id）。"""
    return _workflow_run_id_ctx.get()


def get_mine_id() -> Optional[str]:
    """获取当前矿井 ID。"""
    return _mine_id_ctx.get()


def get_roadway_id() -> Optional[str]:
    """获取当前巷道 ID。"""
    return _roadway_id_ctx.get()


def get_operator() -> Optional[dict]:
    """获取当前操作者信息。"""
    return _operator_ctx.get()


def reset_workflow_context(tokens: tuple) -> None:
    """
    恢复工作流 run 级上下文，避免本次运行信息残留到后续请求/线程。

    :param tokens: set_workflow_context 返回的 4 个 token
    """
    for var, token in zip(
        (_workflow_run_id_ctx, _mine_id_ctx, _roadway_id_ctx, _operator_ctx),
        tokens,
    ):
        var.reset(token)


def reset_thread_context(token: Token[Optional[str]]) -> None:
    """
    恢复线程 ID 上下文（供后台工作流线程退出时复位使用）。

    :param token: set_thread_context 返回的 token
    """
    _thread_id_ctx.reset(token)


def reset_session_context(
    session_token: Token[Optional[str]],
    thread_token: Optional[Token[Optional[str]]] = None,
) -> None:
    """
    恢复请求上下文，避免本次任务信息残留到后续请求

    :param session_token: set_session_context 返回的 token
    :param thread_token: set_thread_context 返回的 token
    """
    _session_dir_ctx.reset(session_token)
    if thread_token is not None:
        _thread_id_ctx.reset(thread_token)
