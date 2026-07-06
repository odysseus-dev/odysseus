"""Shared privilege policy for scheduled task actions."""

from __future__ import annotations

ADMIN_ONLY_TASK_ACTIONS = frozenset({
    "run_local",
    "run_script",
    "ssh_command",
    "cookbook_serve",
})


def is_admin_only_task_action(task_type: str | None, action: str | None) -> bool:
    return (task_type or "llm") == "action" and (action or "") in ADMIN_ONLY_TASK_ACTIONS


def owner_has_admin_task_privileges(owner: str | None) -> bool:
    try:
        from src.auth_helpers import _auth_disabled
        if _auth_disabled():
            return True
    except Exception:
        pass

    if owner:
        try:
            from core.middleware import INTERNAL_TOOL_USER
            if owner == INTERNAL_TOOL_USER:
                return True
        except Exception:
            pass

    try:
        from core.auth import AuthManager
        auth = AuthManager()
        if not auth.is_configured:
            return True
        if not owner:
            return False
        return bool(auth.is_admin(owner))
    except Exception:
        pass

    if not owner:
        return False

    return False
