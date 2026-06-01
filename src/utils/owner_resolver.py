"""Pure helper for resolving the effective request owner."""

from __future__ import annotations

INTERNAL_TOOL_OWNER = "internal-tool"


def resolve_owner(header_owner: str | None, user: str | None, auth_users: list[str]) -> str:
    """Resolve the effective owner name for a request.

    If the X-Odysseus-Owner header names a valid user, return it.
    Otherwise, if the current user is known and valid, return the current user.
    Otherwise, fall back to the internal-tool owner.
    """
    requested_owner = (header_owner or "").strip()
    if requested_owner and requested_owner in auth_users:
        return requested_owner
    if user and user in auth_users:
        return user
    return INTERNAL_TOOL_OWNER
