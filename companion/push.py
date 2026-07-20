"""Companion mobile push — deliver Odysseus events to a paired phone.

This is the additive, companion-side counterpart to the outbound webhook
manager. Where webhooks POST a fixed JSON envelope to an arbitrary http(s)
URL, this routes a lifecycle event to the Expo push service so a paired phone
buzzes — no public relay, no third party beyond Expo's push gateway.

Wiring: ``build_push_sink()`` is registered with ``event_bus.add_event_sink`` in
app.py — the SAME lifecycle producer path the rest of the app fires on, so push
does not depend on the outbound-webhook layer (or any unmerged bridge) to reach
a phone. The bus invokes it as ``(event_name, owner)`` on every fired event; we
deliver only when the event is a mapped lifecycle event AND carries an owner
(owner attribution is the whole game — a push must reach exactly one user's
phones), so ownerless events are skipped rather than broadcast.

Tokens live in a small JSON file under DATA_DIR (alongside auth.json), keyed by
owner. An Expo push token is a device handle, not a high-value secret, but it
is still owner-scoped: a caller only ever reads/writes its own list.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

# Expo's push gateway. Public https endpoint — privacy-wise this is the only
# third party in the path, matching how Expo apps normally send notifications.
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# ExponentPushToken[...] (current) / ExpoPushToken[...] (older). The inner
# handle is opaque; reject anything that isn't one of these shapes so we never
# forward junk to Expo.
_TOKEN_RE = re.compile(r"^Exp(?:o|onent)PushToken\[[A-Za-z0-9 _.\-]+\]$")
_MAX_TOKENS_PER_OWNER = 20

# internal event-bus name -> (title, body). These are the names lifecycle
# producers actually fire on the event bus (research_completed, document_created,
# ...); anything else returns None from notification_for and is skipped. Only
# owner-bearing lifecycle events are useful as a phone notification.
_EVENT_NOTIFICATIONS = {
    "research_completed": ("Research complete", "Your research report is ready."),
    "document_created": ("Document added", "A new document was created."),
    "memory_added": ("Memory saved", "A new memory was saved."),
    "email_received": ("New email", "You have new mail."),
    "skill_added": ("Skill added", "A new skill is available."),
}

_lock = threading.Lock()

# Test seam: when set, the JSON store lives here instead of DATA_DIR.
_store_path_override = None


# --------------------------------------------------------------------------- #
# Token store (owner -> [expo tokens])
# --------------------------------------------------------------------------- #
def _store_path() -> str:
    if _store_path_override:
        return _store_path_override
    from src.constants import DATA_DIR

    return os.path.join(DATA_DIR, "companion_push.json")


def _read_all() -> dict:
    try:
        with open(_store_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write-then-rename so a crash mid-write can't truncate the store.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def is_valid_token(token: str) -> bool:
    return bool(token) and len(token) <= 256 and bool(_TOKEN_RE.match(token))


def register_push_token(owner: str, token: str) -> None:
    """Add an Expo push token for ``owner`` (idempotent). Raises ValueError on a
    malformed token or missing owner."""
    if not owner:
        raise ValueError("owner is required")
    token = (token or "").strip()
    if not is_valid_token(token):
        raise ValueError("invalid Expo push token")
    with _lock:
        data = _read_all()
        tokens = [t for t in data.get(owner, []) if isinstance(t, str)]
        if token not in tokens:
            tokens.append(token)
        # Keep the newest N; a device that re-registers stays, stale ones age out.
        data[owner] = tokens[-_MAX_TOKENS_PER_OWNER:]
        _write_all(data)


def unregister_push_token(owner: str, token: str) -> None:
    """Remove a single token for ``owner`` (no-op if absent)."""
    if not owner:
        return
    with _lock:
        data = _read_all()
        tokens = [t for t in data.get(owner, []) if isinstance(t, str) and t != token]
        if tokens:
            data[owner] = tokens
        else:
            data.pop(owner, None)
        _write_all(data)


def list_push_tokens(owner: str) -> list:
    """The Expo tokens registered for ``owner`` (never another owner's)."""
    if not owner:
        return []
    with _lock:
        return list(_read_all().get(owner, []))


def rename_owner(old_owner: str, new_owner: str) -> None:
    """Move an owner's registered devices to a new username on account rename.

    Without this, renaming an account strands its paired phones under the old
    key (their events would silently stop). Merges into any tokens the new name
    already has, de-duped and capped, and drops the old key.
    """
    if not old_owner or not new_owner or old_owner == new_owner:
        return
    with _lock:
        data = _read_all()
        moving = [t for t in data.pop(old_owner, []) if isinstance(t, str)]
        if not moving:
            return
        existing = [t for t in data.get(new_owner, []) if isinstance(t, str)]
        merged = existing + [t for t in moving if t not in existing]
        data[new_owner] = merged[-_MAX_TOKENS_PER_OWNER:]
        _write_all(data)


def purge_owner(owner: str) -> None:
    """Remove all of an owner's registered devices on account deletion.

    Prevents a deleted account's Expo tokens from lingering — and, if the
    username is later reused, from associating a prior account's phone with the
    new identity.
    """
    if not owner:
        return
    with _lock:
        data = _read_all()
        if data.pop(owner, None) is not None:
            _write_all(data)


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def notification_for(event: str):
    """Map an internal event name to (title, body), or None to skip it."""
    return _EVENT_NOTIFICATIONS.get(event)


def build_messages(tokens: list, title: str, body: str, data: dict) -> list:
    """Expo push message objects, one per device token."""
    return [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "data": data or {},
        }
        for token in tokens
    ]


async def deliver(messages: list) -> None:
    """POST a batch of Expo push messages. Best-effort: network/HTTP failures are
    logged, never raised, so a sink failure can't break event handling."""
    if not messages:
        return
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        if resp.status_code >= 400:
            logger.warning("Expo push returned HTTP %s", resp.status_code)
    except Exception:
        logger.warning("Expo push delivery failed", exc_info=True)


async def push_event(event: str, owner) -> None:
    """Event-bus sink: route one fired lifecycle event to the owner's devices.

    Called as ``(event_name, owner)`` by ``event_bus``. Only owner-bearing,
    mapped lifecycle events reach a phone; everything else is a no-op.
    """
    owner = (owner or "").strip() if isinstance(owner, str) else owner
    if not owner:
        return
    note = notification_for(event)
    if note is None:
        return
    tokens = list_push_tokens(owner)
    if not tokens:
        return
    title, body = note
    await deliver(build_messages(tokens, title, body, {"event": event}))


async def send_test_push(owner: str) -> int:
    """Send a test notification to all of ``owner``'s devices. Returns the number
    of devices targeted (0 if none registered)."""
    tokens = list_push_tokens(owner)
    if not tokens:
        return 0
    await deliver(build_messages(
        tokens,
        "Odysseus",
        "Push notifications are working.",
        {"event": "push.test"},
    ))
    return len(tokens)


def build_push_sink():
    """The async (event, payload) callable to register via WebhookManager.add_sink."""
    return push_event
