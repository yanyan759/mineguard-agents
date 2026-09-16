"""
权限校验工具

根据用户角色和操作类型检查权限白名单。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

# 权限矩阵 V2：7 角色 × 9 动作，逐条关联条款出处。
# 权威源 config/permission_matrix.py（内置矩阵 = 落库种子矩阵 = 条款出处矩阵），
# 详见 `任务成果/03_权限矩阵依据/权限条款动作映射.csv` 与 `PM1-PM6来源说明.md`。
from app.config.permission_matrix import ACTION_RISK as _ACTION_RISK
from app.config.permission_matrix import (
    AVAILABLE_ROLES as _AVAILABLE_ROLES,
)
from app.config.permission_matrix import (
    ROLE_PERMISSIONS as _ROLE_PERMISSIONS,
)
from app.config.permission_matrix import permission_source as _permission_source


def _build_result(status: str, data: Any = None, error: str | None = None) -> dict:
    return {
        "status": status, "data": data, "error": error,
        "audit": {
            "tool": "permission_check",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def _resolve_role_permissions(user_role: str) -> tuple[list[str] | None, str]:
    """解析角色权限：优先 MySQL role_permissions 表（用户权限落库），不可达回退内置矩阵。

    :return: (permissions | None, source)  source ∈ mysql | builtin | unknown
    """
    from app.services.mysql_bridge import load_role_permissions

    mysql_perms = load_role_permissions(user_role)
    if mysql_perms is not None:
        return mysql_perms, "mysql"
    builtin = _ROLE_PERMISSIONS.get(user_role)
    if builtin is not None:
        return builtin, "builtin"
    return None, "unknown"


@tool
def check_permission(user_role: str, requested_action: str) -> dict:
    """校验用户角色是否具有执行指定操作的权限。

    用户权限已落 MySQL role_permissions 表（对齐 deploy/mysql/sql 种子）；
    MySQL 可达时优先读表，不可达/角色不在表内时回退内置权限矩阵。

    :param user_role: 用户角色（如"矿总工程师""支护工程师"等）
    :param requested_action: 请求的操作类型
    """
    role_perms, perm_source = _resolve_role_permissions(user_role)
    if role_perms is None:
        return _build_result(
            "rejected",
            data={
                "passed": False,
                "user_role": user_role,
                "requested_action": requested_action,
                "permission_source": perm_source,
            },
            error=f"未知角色 '{user_role}'，可用角色：{_AVAILABLE_ROLES}",
        )

    has_permission = requested_action in role_perms
    action_risk = _ACTION_RISK.get(requested_action, "unknown")
    # 条款出处（真实授权依据；无映射动作权限一律不给，override_threshold 不落矩阵）
    clause = _permission_source(user_role, requested_action) if has_permission else None

    return _build_result(
        "success",
        data={
            "passed": has_permission,
            "user_role": user_role,
            "requested_action": requested_action,
            "action_risk_level": action_risk,
            "role_permissions": role_perms,
            "permission_source": perm_source,
            "permission_clause": clause,
        },
        error=None if has_permission else (
            f"角色 '{user_role}' 无权执行 '{requested_action}'（风险等级：{action_risk}），"
            f"该角色可用权限：{role_perms}"
        ),
    )
