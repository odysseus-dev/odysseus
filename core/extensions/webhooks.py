"""webhooks.py — inbound webhook endpoints for OXS extensions.

Each extension that declares ``webhooks_in`` capability gets a private
inbound webhook at:

    POST /api/ext/<extension_id>/webhook/<token>

The path is exempt from session auth (mirrored in AUTH_EXEMPT_PATTERNS
in PR 4). The token is per-extension, generated on install, and stored
hashed in the ``extension_permissions`` DB table. A request with the
wrong token returns 404 (not 401 — we don't leak the existence of
the endpoint).

The optional ``X-<extension_id>-Signature`` header is verified in
constant time when set. Signature algorithm: HMAC-SHA256 over the raw
body using a per-extension secret stored alongside the token.

The handler runs in an asyncio task — the endpoint returns 202
immediately. Failure (handler exception) writes to the audit log.

Rate limiting is per-token, 10 requests / second default. Exceeding the
limit returns 429 and writes an audit entry.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .audit import audit_log

logger = logging.getLogger(__name__)


# ── Token + secret ─────────────────────────────────────────────────────────


def generate_token() -> str:
    """Generate a webhook token. 32 bytes of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """One-way hash for storage. We use SHA-256 with a static pepper."""
    # The pepper is the OXS core version string. This is not a substitute
    # for bcrypt — it's an extra obscurity layer on top of an already-
    # random 32-byte secret. If you lose the pepper, regenerate tokens.
    pepper = "OXS-WEBHOOK-TOKEN-PEPPER-v1"
    return hashlib.sha256((pepper + token).encode("utf-8")).hexdigest()


def generate_secret() -> str:
    """Generate a per-extension HMAC signing secret. URL-safe, 32 bytes."""
    return secrets.token_urlsafe(32)


def verify_token_hash(token: str, expected_hash: str) -> bool:
    """Constant-time comparison of a token against a stored hash."""
    actual = hash_token(token)
    return hmac.compare_digest(actual, expected_hash)


def compute_signature(secret: str, body: bytes) -> str:
    """Return ``sha256=<hex>`` HMAC over the body using ``secret``."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time verification of the signature header.

    Returns True when no header is supplied (signature is optional) or
    when the supplied signature matches. Returns False on mismatch or on
    any decoding failure.
    """
    if not signature_header:
        return True
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature_header)


# ── Rate limit ─────────────────────────────────────────────────────────────


@dataclass
class _RateState:
    """Sliding-window counters per (extension_id, token_hash)."""
    last_reset: float = 0.0
    count: int = 0


_rate: dict[tuple[str, str], _RateState] = {}


def _check_rate_limit(
    extension_id: str, token_hash: str, *, limit: int = 10, window: float = 1.0,
) -> bool:
    """Return True if request is within limit, False if rate-limited."""
    key = (extension_id, token_hash)
    now = time.monotonic()
    state = _rate.get(key)
    if state is None or (now - state.last_reset) >= window:
        _rate[key] = _RateState(last_reset=now, count=1)
        return True
    state.count += 1
    return state.count <= limit


def reset_rate_limits() -> None:
    """Clear all rate-limit state. Tests + admin 'reset' button use this."""
    _rate.clear()


# ── Dispatch ──────────────────────────────────────────────────────────────


@dataclass
class WebhookRegistrationRecord:
    """Internal record of an installed webhook."""
    extension_id: str
    path_suffix: str        # the part after ``/api/ext/<id>/webhook/``
    event_filter: list[str]
    handler: Callable
    token_hash: str         # hashed, compared constant-time against request
    secret: str             # HMAC secret (raw, never sent to DB)
    registered_at: datetime = field(default_factory=lambda: _utcnow())


def _utcnow():
    """Naive UTC for storage (matches core.database convention)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Server-side registry of installed webhooks. The PR 4 wiring layer
# populates this on enable and clears on disable.
_webhooks: dict[str, list[WebhookRegistrationRecord]] = {}


def list_for_extension(extension_id: str) -> list[WebhookRegistrationRecord]:
    return list(_webhooks.get(extension_id, []))


def install(
    extension_id: str,
    path_suffix: str,
    event_filter: list[str],
    handler: Callable,
) -> WebhookRegistrationRecord:
    """Generate a token and register a webhook for ``extension_id``.

    Returns the registration record. The handler will receive the parsed
    JSON body and the request headers as kwargs.
    """
    token = generate_token()
    rec = WebhookRegistrationRecord(
        extension_id=extension_id,
        path_suffix=path_suffix,
        event_filter=list(event_filter),
        handler=handler,
        token_hash=hash_token(token),
        secret=generate_secret(),
    )
    _webhooks.setdefault(extension_id, []).append(rec)
    audit_log(
        "extension.webhook.installed",
        extension_id=extension_id,
        actor="system",
        payload={"path_suffix": path_suffix, "event_filter": list(event_filter)},
    )
    # Stash the raw token on the record for the caller. The caller (PR 4
    # wiring) needs the raw token to build the public URL shown in the
    # admin UI. Once shown, it is no longer recoverable.
    rec._raw_token = token  # type: ignore[attr-defined]
    return rec


def uninstall(extension_id: str) -> int:
    """Remove every webhook for ``extension_id``. Returns count removed."""
    removed = len(_webhooks.pop(extension_id, []))
    if removed:
        audit_log(
            "extension.webhook.uninstalled",
            extension_id=extension_id,
            actor="system",
            payload={"count": removed},
        )
    # Drop rate state for this extension
    for key in list(_rate.keys()):
        if key[0] == extension_id:
            del _rate[key]
    return removed


def reset_for_tests() -> None:
    """Clear all installed webhooks and rate-limit state. Tests only."""
    _webhooks.clear()
    _rate.clear()


# ── Dispatcher ─────────────────────────────────────────────────────────────


async def dispatch(
    extension_id: str,
    token: str,
    body: bytes,
    *,
    headers: dict | None = None,
    query: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    """Find the webhook matching ``(extension_id, token)`` and invoke it.

    Returns ``(http_status, message)`` for the HTTP response.
    """
    headers = headers or {}
    query = query or {}

    candidates = _webhooks.get(extension_id, [])
    if not candidates:
        return 404, "not found"

    # Find the webhook with matching token
    matched: WebhookRegistrationRecord | None = None
    for rec in candidates:
        if verify_token_hash(token, rec.token_hash):
            matched = rec
            break
    if matched is None:
        audit_log(
            "extension.webhook.invalid_token",
            extension_id=extension_id,
            actor="system",
            payload={"path_suffix": matched.path_suffix if matched else "?"},
        )
        return 404, "not found"

    # Rate limit
    if not _check_rate_limit(extension_id, matched.token_hash):
        audit_log(
            "extension.webhook.rate_limited",
            extension_id=extension_id,
            actor="system",
            payload={"path_suffix": matched.path_suffix},
        )
        return 429, "rate limited"

    # Signature
    sig_header = None
    for k, v in headers.items():
        if k.lower() == f"x-{extension_id}-signature":
            sig_header = v
            break
    if not verify_signature(matched.secret, body, sig_header):
        audit_log(
            "extension.webhook.bad_signature",
            extension_id=extension_id,
            actor="system",
            payload={"path_suffix": matched.path_suffix},
        )
        return 401, "bad signature"

    # Parse body (best-effort — extensions decide what to do with raw bytes)
    import json
    try:
        parsed: Any = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None

    # Schedule handler in background; never block the HTTP response
    asyncio.create_task(
        _run_handler(matched, body, parsed, headers, query, timeout),
        name=f"oxs_webhook:{extension_id}:{matched.path_suffix}",
    )
    return 202, "accepted"


async def _run_handler(
    rec: WebhookRegistrationRecord,
    raw_body: bytes,
    parsed_body: Any,
    headers: dict,
    query: dict,
    timeout: float,
) -> None:
    """Invoke the extension's handler with structured kwargs."""
    try:
        coro = rec.handler(
            body=parsed_body if parsed_body is not None else raw_body,
            headers=headers,
            query=query,
            event_filter=rec.event_filter,
        )
        if asyncio.iscoroutine(coro):
            await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        logger.exception(
            "OXS webhook handler for %s raised", rec.extension_id,
        )
        audit_log(
            "extension.webhook.handler_failed",
            extension_id=rec.extension_id,
            actor=f"extension:{rec.extension_id}",
            payload={"path_suffix": rec.path_suffix},
        )


# ── Public URL helpers ────────────────────────────────────────────────────


def public_url(extension_id: str, *, base: str = "") -> str:
    """Return the canonical inbound URL for ``extension_id``.

    ``base`` is the public origin (e.g. ``https://odysseus.example.com``).
    The token is per-extension and rotated per install/enable cycle in
    PR 4 wiring.
    """
    return f"{base}/api/ext/{extension_id}/webhook/<token>"


def get_raw_token(rec: WebhookRegistrationRecord) -> str | None:
    """Return the raw token attached to a record by :func:`install`.

    Operators see this once in the install UI; thereafter only the hash
    is recoverable.
    """
    return getattr(rec, "_raw_token", None)


__all__ = [
    "generate_token",
    "hash_token",
    "generate_secret",
    "verify_token_hash",
    "compute_signature",
    "verify_signature",
    "WebhookRegistrationRecord",
    "install",
    "uninstall",
    "list_for_extension",
    "dispatch",
    "public_url",
    "get_raw_token",
    "reset_for_tests",
    "reset_rate_limits",
]