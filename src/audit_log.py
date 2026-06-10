"""
Actor-attributed structured audit logger for Odysseus.

Public API:
    audit_event(actor, action, detail="", level="INFO") -> None

Logger name: "odysseus.audit"
Format:  AUDIT actor=<actor> action=<action> detail=<detail>
         All values are repr()-formatted so they are unambiguous.
"""

import logging

_audit_logger = logging.getLogger("odysseus.audit")
_audit_error_logger = logging.getLogger("odysseus.audit.errors")


def audit_event(
    actor: str,
    action: str,
    detail: str = "",
    level: str = "INFO",
) -> None:
    """Emit a single structured audit log line.

    Args:
        actor:  The authenticated username performing the action.
                Use "anon" when no session is present, "system" for
                background/scheduled tasks.
        action: A snake_case verb describing the operation, e.g.
                "pii_export", "bulk_wipe_chats", "privilege_grant",
                "vault_unlock", "vault_lock", "user_create",
                "user_delete", "role_grant".
        detail: Additional coarse context.  Must NOT contain passwords,
                email addresses, message subjects, or other PII beyond
                the actor username and the action itself.
        level:  "INFO" (default) or "WARNING" for destructive / high-
                privilege operations (wipe, unlock, role/privilege grant).

    This function never raises.  Failures are swallowed silently so that
    audit-log failures cannot break a user-facing request.
    """
    try:
        message = f"AUDIT actor={actor!r} action={action!r} detail={detail!r}"
        log_level = logging.WARNING if level.upper() == "WARNING" else logging.INFO
        _audit_logger.log(log_level, message)
    except Exception:
        _audit_error_logger.exception(
            "audit_event delivery failed: actor=%r action=%r", actor, action
        )
