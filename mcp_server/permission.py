"""阶段 8 MCP 生产化：权限审批层（PermissionGate）。

只读工具直接放行；变更类工具（MCP_TOOL_REGISTRY.read_only=False）需持有
MCP_APPROVAL_TOKEN 环境凭据才可执行，否则返回 needs_approval（不执行）。
安全默认：未配置 token → 变更工具一律 needs_approval（对齐『高风险动作必须人工审批』）。
"""

from __future__ import annotations

import hmac
import os


def approval_token() -> str:
    """审批凭据：默认空串（未配置 → 变更工具不自动放行）。"""
    return os.getenv("MCP_APPROVAL_TOKEN", "")


def verify_approval(token: str | None) -> bool:
    """常数时间比较审批凭据；未配置凭据时恒 False。"""
    expected = approval_token()
    if not expected:
        return False
    return token is not None and hmac.compare_digest(token, expected)


def requires_approval(tool_id: str) -> bool:
    """由 MCP_TOOL_REGISTRY 的 read_only 标记推导（变更工具需审批）。"""
    from app.schemas.mcp_tools import MCP_TOOL_REGISTRY
    meta = MCP_TOOL_REGISTRY.get(tool_id, {})
    return not meta.get("read_only", True)


def gate(tool_id: str, provided_token: str | None = None) -> tuple[bool, str]:
    """执行前校验：返回 (放行?, 状态)。状态 ∈ {'ok', 'needs_approval'}。"""
    if not requires_approval(tool_id):
        return True, "ok"
    if verify_approval(provided_token):
        return True, "ok"
    return False, "needs_approval"
