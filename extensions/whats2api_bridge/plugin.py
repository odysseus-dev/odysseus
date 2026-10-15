"""whats2api_bridge plugin entry point.

This OXS extension bridges Odysseus to https://whats2api.com — a hostedWhatsApp gateway that exposes both a REST API and a remote MCP server.

Two surfaces are exposed:

1. **MCP tools** that the agent can call as ``mcp__oxs_whats2api_bridge__<tool>``:
   - Connection management (``list_connections``, ``get_connection``,
     ``create_connection``, ``patch_connection``, ``remove_connection``,
     ``start_connection``, ``stop_connection``).
   - Messaging (``send_message``, ``send_reaction``, ``mark_read``,
     ``send_media``, ``send_presence``, ``is_on_whatsapp``,
     ``call_method`` — a generic passthrough for any Baileys/whatsmeow
     method).

2. **Inbound webhook** that receives the event fan-out from whats2api
   (POST body is ``{eventName: payload, ...}``). The handler persists
   every event, fires OXS events for the interesting ones, and updates
   the cached connections / messages tables.

Capabilities (declared in ``extension.json``):

- ``data`` — own tables ``whats2api_bridge__connections`` / ``__messages`` /
  ``__events``.
- ``mcp_tools`` / ``tools`` — agent-callable tools.
- ``webhooks_in`` / ``webhooks_out`` — receive events from whats2api.
- ``events_fire`` — fire ``whats2api.*`` events on the OXS bus so other
  extensions and the agent see them.
- ``static`` / ``ui_panel`` — serve a static panel under
  ``/ext-static/whats2api_bridge/``.
- ``network_outbound`` (allowlisted) — only ``https://api.whats2api.com``.
- ``filesystem_data_subpath`` (``cache``) — for downloaded media.

Lifecycle:
- ``on_install``:  write default settings.
- ``on_enable``:   register tools, webhooks, UI; build storage handle.
- ``on_disable``:  nothing (host unwinds registrations automatically).
- ``on_uninstall``: nothing (host drops our tables on uninstall).
"""


import asyncio
import hashlib
import threading
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable
from fastapi import Body, Header, Query, Request

import httpx

from core.database import engine
from core.extensions.events import dispatch_via_event_bus
from core.extensions.storage import PluginStorage

# Lazy imports — these pull in the MCP manager and DB model only when an
# MCP integration endpoint is hit. Done at import time so the plugin
# module can be imported without forcing those to be loaded eagerly.
def _mcp_manager():
    from src.mcp_manager import get_mcp_manager as _g
    return _g()

def _mcp_models():
    from src.database import SessionLocal as _SL, McpServer as _MS
    return _SL, _MS


EXTENSION_ID = "whats2api_bridge"
TOOL_NS = "oxs_whats2api_bridge"  # unique namespace; "whats2api" collides with the remote MCP server
DEFAULT_BASE_URL = "https://api.whats2api.com"

# ── Conversation Analyzer (periodic) ─────────────────────────────────────
# Runs as a ScheduledTask owned by this extension. The scheduler creates a
# dedicated Session in folder="Tasks" so the operator can review each run
# as a normal chat — same UX as any other scheduled task. A dedicated
# CrewMember enforces the "no WhatsApp writes" rule via enabled_tools
# allowlist.
ANALYZER_TASK_NAME = "[whats2api_bridge] Conversation Analyzer"
ANALYZER_CREW_NAME = "WhatsApp Conversation Analyzer"
ANALYZER_CREW_ICON = "🛰️"

# Tools the analyzer may call. Everything else — including ALL whats2api /
# whats2api_bridge WRITE tools (send_message, send_reaction, send_media,
# mark_read, send_presence, call_method, ...) — is filtered out by the
# scheduler's disabled_tools derivation in _execute_llm_task.
ANALYZER_ALLOWED_TOOLS: tuple[str, ...] = (
    # Build-in / native tools (unqualified names — these are the keys in
    # ``src.tool_index.BUILTIN_TOOL_DESCRIPTIONS``, which the scheduler
    # uses to derive ``disabled_tools`` from the inverse of this
    # allowlist. Only names that actually live in that dict will
    # meaningfully *unblock* a tool; non-matching names like MCP
    # qualified ones are simply ignored by that filter and stay
    # available via the agent's normal MCP tool surface).
    # Read tools (cross-conversation context):
    "search_chats",
    # Durable annotations — actions the operator explicitly approved:
    "manage_notes",
    "manage_tasks",
    "manage_calendar",
    "manage_memory",
    # NOTE: "todowrite" is deliberately NOT here. It is WRITE_PRIVATE
    # with no authority grant from the analyzer prompt, so after the
    # untrusted WhatsApp context the post-external gate demands an
    # interactive approval a scheduled task cannot give — the run dies
    # with "paused safely" and no findings. Persistence goes through
    # the manage_* tools above, which the prompt does grant.
    # Local cache of the bridge — accessed via the qualified MCP
    # names. Listed here for documentation; the scheduler's
    # ``disabled_tools`` filter does not key on these. They get
    # registered in the MCP server's namespace and the RAG picks
    # them up when relevant to the analyzer's prompt.
    "mcp__oxs_whats2api_bridge__get_analysis_queue",
    "mcp__oxs_whats2api_bridge__list_messages",
    "mcp__oxs_whats2api_bridge__get_message",
    "mcp__oxs_whats2api_bridge__list_events",
    "mcp__oxs_whats2api_bridge__list_connections_local",
)

# Tools that the analyzer must NEVER call. Stated as a negative list
# so a future change to ``ANALYZER_ALLOWED_TOOLS`` cannot accidentally
# re-introduce a WhatsApp write tool. The runtime gate that enforces
# this is the bidirectional check performed after the scheduler's
# own ``disabled_tools`` derivation: anything starting with the
# prefixes below is rejected with HTTP 409 by the OXS tool dispatcher
# when the analyzer's loop tries to call it.
ANALYZER_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    # Native write surface:
    "send_message", "send_reaction", "send_media", "send_presence",
    "mark_read", "call_method", "send_to_session",
    # Shell / filesystem write surface:
    "write_file", "edit_file", "apply_patch",
    "bash", "host_shell", "python",
    # OAuth / external shells:
    "set_setting", "manage_settings",
)

DEFAULT_ANALYZER_PROMPT = """# WhatsApp Conversation Analyzer — system prompt

## Role
You are the WhatsApp Conversation Analyzer: a silent, periodic reviewer of the \
operator's recent WhatsApp conversations. You run on a schedule; the operator \
reviews each run as a normal chat session in folder "Tasks".

## Scope (every run)
- **You analyze NEW conversation intervals only.** Your FIRST call must be \
`mcp__oxs_whats2api_bridge__get_analysis_queue` (once, no arguments). It returns:
  - `new_intervals` — conversation intervals no run has analyzed yet, with \
their messages. Extract findings from THESE ONLY.
  - `done_intervals` — intervals already analyzed, with what was persisted. \
Those items are final: never recreate, update or extend them.
- If `new_intervals` is empty, output exactly `No new conversation intervals.` \
and call **zero** write tools — doing nothing is the correct outcome.
- **Skip intervals with fewer than {min_messages} message(s)** — pure noise.
- Use `mcp__oxs_whats2api_bridge__list_messages` with `"since": {since_ts}` or \
`mcp__oxs_whats2api_bridge__get_message` only to drill into one specific chat \
when an interval needs more context.
- **You NEVER reply on WhatsApp.** No side effects. Read, decide, persist as notes/tasks/calendar/memory.

## Tools to use (priority order)
| Signal in chat                                 | Persist via        |
|------------------------------------------------|--------------------|
| Promise with a stated deadline                 | `manage_tasks`     |
| Future event with an unambiguous date/time     | `manage_calendar`  |
| Decision / context / person fact worth keeping | `manage_notes` or `manage_memory` |
| New contact / phone / handle                   | `manage_memory`    |
| Plan or arrangement agreed in the chat, however casual | `manage_notes` (plus `manage_calendar` when it has a resolvable day) |

**A casual plan is still content.** "Amanhã vamos tomar sorvete" is an
arrangement someone made with the operator — it belongs in a note. Treating it
as noise loses it forever, because the interval is sealed once judged.
Bias toward `manage_notes` for anything that says the operator is going to
do, meet, or forget something.

**Relative dates are resolvable.** Today's date is in your context. "amanhã" is
tomorrow, "sexta" is this Friday, "semana que vem" is within seven days.
Compute them — do not skip an item merely because the chat used a relative
word instead of a date. Only genuinely unresolvable ones ("em breve", "logo
mais") are ambiguous.

Read tools (always allowed):
- `mcp__oxs_whats2api_bridge__list_messages`
- `mcp__oxs_whats2api_bridge__list_events`
- `mcp__oxs_whats2api_bridge__get_message`
- `mcp__oxs_whats2api_bridge__list_connections_local`
- `search_chats`

## Hard rules
1. **NEVER call** `send_message`, `send_reaction`, `send_media`, `mark_read`,
   `send_presence`, `call_method`, `send_to_session`, `bash`, `host_shell`,
   `python`, `read_file`, `write_file`, `edit_file`, `apply_patch`. These tools
   are filtered out at the framework level, but respect the intent.
2. **No fabrication.** If a deadline is vague ("em breve"), do not create a task.
   If a date is genuinely unresolvable, skip the calendar entry — but compute
   relative dates rather than treating them as ambiguous.
3. **Be terse.** No preamble, no chain-of-thought, no "let me check the data".
4. **Owner scope.** Only read/write data belonging to this operator.

## Output (always — short and structured)

End your reply with one block, in this exact shape:

```
Findings (chat: <push_name or jid>):
- <one line per finding, e.g.>
  - task: send X to Y by Friday — created
  - event: meeting Thu 15:00 — created
  - note: changed delivery address — saved
  - memory: contact João (11 9 9999-0000) — saved
  - event: meeting Thu 15:00 — already exists, skipped
- no action needed
```

If the queue is empty, output exactly `No new conversation intervals.`
If the queue has intervals but nothing is actionable, output exactly:
`No actionable items in the last {lookback_hours}h.` — and call **zero** write tools.

## Grouping rule
Group by `chat_jid` / `push_name`. One findings block per chat that yielded
actionable items. Skip chitchat, greetings, status messages and reactions.
"""

logger = logging.getLogger("oxs.whats2api_bridge")


# ── Settings helpers ───────────────────────────────────────────────────────


def _s(host, key: str, default: Any = None) -> Any:
    """Read a setting from the extension's settings namespace."""
    try:
        return host.get_settings().get(key, default)
    except Exception:
        return default


# ── Result envelope ────────────────────────────────────────────────────────


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def _err(message: str, **extra: Any) -> dict:
    out = {"ok": False, "error": str(message)}
    out.update(extra)
    return out


# ── HTTP client builder ────────────────────────────────────────────────────
#
# whats2api has two *different* credentials, and conflating them is the
# single biggest thing to understand about this client:
#
#   1. A long-lived **MCP token** (dashboard → /p/mcp-tokens). Used as
#      ``Authorization: Bearer`` for the MCP surface, and embedded in the
#      server URL ``https://api.whats2api.com/mcp/<token>``.
#   2. A **daily JWT**, minted by ``POST /authentication`` with the
#      account email + password. It is what the REST surface
#      (``GET /connections``, ``POST /connections/request``) checks.
#
# Handing the REST surface the MCP token yields
# ``401 {"message": "jwt malformed", "className": "JsonWebTokenError"}``
# — verified against the live API with a real credential, which is the
# only way to find it: the token is perfectly valid, just on the other
# door. So the client prefers a JWT when it can mint one, and falls back
# to the stored token only when it cannot.

# Refresh a little before the documented 1-day life so a long-running
# analyzer does not discover expiry mid-turn.
_JWT_TTL_SECONDS = 20 * 3600
_JWT_CACHE: dict = {"token": "", "at": 0.0}
_JWT_LOCK = None


def _rest_credentials(host) -> tuple:
    """``(email, password)`` for the daily-JWT flow, or ``(None, None)``."""
    return (
        str(_s(host, "api_email", "") or "").strip(),
        str(_s(host, "api_password", "") or ""),
    )


def _jwt_cached() -> str:
    cached = _JWT_CACHE.get("token") or ""
    if cached and time.time() < (_JWT_CACHE.get("at") or 0.0):
        return cached
    return ""


async def _mint_jwt(host) -> str:
    """Exchange email+password for a fresh access token.

    Raises on anything the provider rejects, so the caller can report the
    real reason instead of a downstream 401 with no explanation.
    """
    email, password = _rest_credentials(host)
    if not (email and password):
        raise RuntimeError(
            "whats2api_bridge: api_email/api_password are not configured. "
            "The REST API requires a daily JWT from POST /authentication; "
            "the MCP token alone is rejected with 'jwt malformed'. Set both "
            "in Settings → Extensions → Whats2API Bridge."
        )
    base = str(_s(host, "base_url", DEFAULT_BASE_URL)).rstrip("/")
    timeout = float(_s(host, "request_timeout_seconds", 30))
    async with httpx.AsyncClient(base_url=base, timeout=timeout) as client:
        response = await client.post(
            "/authentication",
            json={"email": email, "password": password, "strategy": "local"},
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"whats2api rejected the login (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )
    try:
        token = (response.json() or {}).get("accessToken")
    except Exception as exc:
        raise RuntimeError(
            f"whats2api login returned no accessToken: {exc}"
        ) from exc
    if not token:
        raise RuntimeError("whats2api login returned an empty accessToken")
    _JWT_CACHE["token"] = str(token)
    _JWT_CACHE["at"] = time.time() + _JWT_TTL_SECONDS
    return str(token)


async def _rest_token(host, *, force_refresh: bool = False) -> str:
    """The bearer to put on a REST call."""
    email, password = _rest_credentials(host)
    if not (email and password):
        # No account credentials: fall back to whatever token is stored.
        # Kept so a misconfigured install fails the way it always did
        # rather than erroring differently from a different cause.
        return str(_s(host, "api_token", "") or "")
    if not force_refresh:
        cached = _jwt_cached()
        if cached:
            return cached
    return await _mint_jwt(host)


def _is_auth_failure(status_code: int, body: str = "") -> bool:
    """Whether a response means "your bearer is not valid", not "bad call"."""
    if status_code in (401, 403):
        return True
    lowered = (body or "").lower()
    return "jwt" in lowered and (
        "malformed" in lowered or "expired" in lowered or "invalid" in lowered
    )


def _build_client(host) -> httpx.AsyncClient:
    """Build an unauthenticated client.

    The bearer is attached per call by :func:`_authorized_request`, which
    is async and may need to mint a JWT first. Building it here would mean
    either blocking the loop on a network round trip or caching a token
    that nobody can refresh.
    """
    base = str(_s(host, "base_url", DEFAULT_BASE_URL)).rstrip("/")
    timeout = float(_s(host, "request_timeout_seconds", 30))
    return httpx.AsyncClient(
        base_url=base,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "odysseus-whats2api-bridge/0.5.2",
        },
        timeout=timeout,
    )


def _attach_auth(host, client: httpx.AsyncClient) -> None:
    """Route every request on ``client`` through JWT auth with one retry.

    ``AsyncClient.request`` is the single funnel that ``get``/``post``/
    ``patch``/``delete`` all call, so wrapping it here covers the whole
    transport family without touching any handler. The retry is what
    makes a daily JWT usable at all: a token that expires mid-window
    refreshes and the call proceeds, instead of every outbound tool
    failing until a human notices.
    """
    inner = client.request

    async def _request(method, url, **kw):
        headers = dict(kw.pop("headers", None) or {})
        token = await _rest_token(host)
        if not token:
            # Without this the header below becomes the literal "Bearer ",
            # which httpx rejects as an illegal header value -- an error
            # that says nothing about the real cause.
            raise RuntimeError(
                "whats2api_bridge: no API credential configured. Set "
                "api_email + api_password in Settings → Extensions → "
                "Whats2API Bridge; the REST API needs the daily JWT from "
                "POST /authentication and rejects an MCP token with "
                "'jwt malformed'."
            )
        headers["Authorization"] = f"Bearer {token}"
        response = await inner(method, url, headers=headers, **kw)
        if not _is_auth_failure(response.status_code, response.text[:300]):
            return response
        if not all(_rest_credentials(host)):
            # Nothing better to try; let the provider's own answer stand.
            response.raise_for_status()
            return response
        headers["Authorization"] = f"Bearer {await _mint_jwt(host)}"
        logger.info(
            "whats2api bearer rejected (%s); refreshed and retrying once",
            response.status_code,
        )
        return await inner(method, url, headers=headers, **kw)

    client.request = _request


# ── Defensive defaults + MCP integrations registry ─────────────────────────


# Whitelist of user-tunable setting keys; used by both the admin PATCH
# handler and the renderer in the static UI.
_ALLOWED_USER_SETTINGS = (
    "base_url",
    "default_connection_id",
    "request_timeout_seconds",
    "retention_days",
    "log_inbound_events",
)

# Write-only credentials. Listed here rather than in the readable settings
# view so the admin GET never echoes a password back over the wire; the UI
# shows only whether one is set.
_CREDENTIAL_SETTINGS = ("api_token", "api_email", "api_password")


def _ensure_settings(host) -> None:
    """Idempotently pre-fill any unset/empty setting with a sane default.

    Called from both ``on_install`` (fresh installs) and ``on_enable``
    (covers upgrades where the install hook predated these defaults —
    or where the user wiped a single key). Never overwrites a value
    the user has actually typed.
    """
    try:
        ns = host.get_settings()
    except Exception:
        return

    def _set_if_empty(key, value):
        try:
            cur = ns.get(key)
        except Exception:
            cur = None
        if cur is None or (isinstance(cur, str) and not cur.strip()):
            try:
                ns.set(key, value)
            except Exception:
                pass

    _set_if_empty("base_url", DEFAULT_BASE_URL)
    _set_if_empty("request_timeout_seconds", 30)
    _set_if_empty("retention_days", 7)
    _set_if_empty("log_inbound_events", True)
    _set_if_empty("analyzer_enabled", False)
    _set_if_empty("analyzer_interval_minutes", 30)
    _set_if_empty("analyzer_lookback_hours", 24)
    _set_if_empty("analyzer_min_messages", 3)
    # The boundary for interval adoption (see ``_adopt_existing_intervals``).
    # Armed once, at first enable, and never moved: everything older is
    # pre-tracking content that earlier runs already handled; everything
    # newer is live work that must never be sealed without being analyzed.
    _set_if_empty("analyzer_tracking_since", int(time.time()))
    # The pre-intervalization migration is opt-in (see ``_adopt_is_enabled``).
    # Seeding it False is deliberate: enabling it would let a wiped state
    # table silently seal everything in the window.
    _set_if_empty("analyzer_adopt_existing", False)
    # analyzer_prompt is deliberately NOT seeded: an empty value means
    # "use the live DEFAULT_ANALYZER_PROMPT" (see _analyzer_settings), so
    # prompt improvements ship with upgrades instead of being frozen by a
    # snapshot written at first enable. A non-empty value is the operator's
    # own custom prompt and is never overwritten.


def _resolve_connection_id(host, args: dict) -> str:
    cid = (
        args.get("connection_id")
        or args.get("connectionId")
        or args.get("connection")
        or ""
    )
    if not cid:
        cid = str(_s(host, "default_connection_id", "") or "")
    if not cid:
        raise RuntimeError(
            "connection_id is required: pass it in args or set "
            "default_connection_id in Settings → Whats2API Bridge."
        )
    return cid


# ── REST wrappers (typed, async) ───────────────────────────────────────────
def _make_async_handler(
    host,
    fn: Callable[..., Any],
    needs_connection_id: bool = False,
    needs_body: bool = False,
) -> Callable[[dict], Any]:
    """Build an async MCP handler that opens an httpx client per call.

    ``fn`` is an async function ``async def fn(client, args, host) -> Any``.
    If ``needs_connection_id`` is True the args must include connection_id
    (resolved against default_connection_id) before fn is invoked.
    """

    async def _handler(args: dict) -> dict:
        try:
            try:
                cid = _resolve_connection_id(host, args) if needs_connection_id else None
            except RuntimeError as e:
                return _err(str(e))
            client = _build_client(host)
            try:
                _attach_auth(host, client)
                if needs_connection_id:
                    result = await fn(client, cid, args, host)
                else:
                    result = await fn(client, args, host)
                if isinstance(result, dict) and "ok" in result:
                    return result
                return _ok(result)
            finally:
                await client.aclose()
        except httpx.HTTPStatusError as e:
            body_text = ""
            try:
                body_text = e.response.text[:500]
            except Exception:
                pass
            return _err(
                f"whats2api returned HTTP {e.response.status_code}",
                status_code=e.response.status_code,
                body=body_text,
            )
        except httpx.HTTPError as e:
            return _err(f"network error: {type(e).__name__}: {e}")
        except RuntimeError as e:
            return _err(str(e))
        except Exception as e:
            logger.exception("whats2api MCP handler raised")
            return _err(f"{type(e).__name__}: {e}")

    return _handler


# ── Tool factories ─────────────────────────────────────────────────────────
# Rich per-tool descriptions now ship inside ``_read_tools`` (each tool
# tuple carries a ``description_rich`` string). OxsHost.register_mcp_tool
# forwards that to the McpManager so tool_index indexes real semantic
# text. Older workaround ``_enrich_mcp_tool_descriptions`` +
# ``_MCP_TOOL_DESCRIPTIONS`` was removed when the core team shipped
# ``MCPToolRegistration.description_rich``.


def _read_tools(host, storage: PluginStorage):
    """Read-only tools that query the local extension tables.

    These let the agent see what the bridge has already ingested from
    whats2api.com without making a new outbound call. Useful for
    "what did this number send me?" or "show me today's events" type
    questions — the data is right here in ``whats2api_bridge__messages``,
    ``whats2api_bridge__events`` and ``whats2api_bridge__connections``.

    Returns a 4-tuple ``(tool_name, schema, handler, description_rich)``
    per tool. ``description_rich`` is consumed by ``OXSHost.register_mcp_tool``
    so the tool_index lane indexes the bridge tools with real semantic
    text — without it the framework falls back to ``"[<ext_id>] <name>"``
    which the RAG can't match against free-form LLM prompts.
    """

    async def _list_messages(client, args, host):
        """List recent messages from the local table.

        Filters: connection_id, jid, direction ("in"/"out"), since (epoch
        seconds), text_search, limit (default 25, max 200).
        """
        from core.database import engine
        where = []
        params: dict = {}
        cid = args.get("connection_id")
        if cid:
            where.append("connection_id = :cid")
            params["cid"] = cid
        jid = args.get("jid")
        if jid:
            where.append("remote_jid LIKE :jid")
            params["jid"] = f"%{jid}%"
        direction = args.get("direction")
        if direction in ("in", "out"):
            where.append("direction = :dir")
            params["dir"] = direction
        since = args.get("since")
        if since is not None:
            where.append("ts >= :since")
            params["since"] = int(since)
        text = args.get("text_search")
        if text:
            where.append("text LIKE :text")
            params["text"] = f"%{text}%"
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        limit = max(1, min(int(args.get("limit") or 25), 200))
        sql = (
            f"SELECT id, connection_id, direction, remote_jid, from_me, "
            f"push_name, text, message_type, ts "
            f"FROM {storage.table_name('messages')} {where_sql} "
            f"ORDER BY ts DESC LIMIT {limit}"
        )
        rows = storage.execute_raw(sql, params)
        return _ok({"messages": rows, "count": len(rows)})

    async def _get_message(client, args, host):
        from core.database import engine
        msg_id = args.get("id")
        if not msg_id:
            return _err("id is required")
        sql = (
            f"SELECT id, connection_id, direction, remote_jid, from_me, "
            f"chat_jid, sender_jid, push_name, text, message_type, ts, raw_json "
            f"FROM {storage.table_name('messages')} WHERE id = :id"
        )
        rows = storage.execute_raw(sql, {"id": msg_id})
        if not rows:
            return _err("message not found")
        return _ok({"message": rows[0]})

    async def _list_events(client, args, host):
        from core.database import engine
        where = []
        params: dict = {}
        ev = args.get("event_name")
        if ev:
            where.append("event_name = :en")
            params["en"] = ev
        cid = args.get("connection_id")
        if cid:
            where.append("connection_id = :cid")
            params["cid"] = cid
        since = args.get("since")
        if since is not None:
            where.append("received_at >= :since")
            params["since"] = int(since)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        limit = max(1, min(int(args.get("limit") or 25), 200))
        sql = (
            f"SELECT id, event_name, connection_id, user_id, received_at, "
            f"raw_json "
            f"FROM {storage.table_name('events')} {where_sql} "
            f"ORDER BY received_at DESC LIMIT {limit}"
        )
        rows = storage.execute_raw(sql, params)
        # Truncate the heavy raw_json field for the listing
        for r in rows:
            if r.get("raw_json"):
                r["raw_json"] = r["raw_json"][:500] + ("..." if len(r["raw_json"]) > 500 else "")
        return _ok({"events": rows, "count": len(rows)})

    async def _list_connections_local(client, args, host):
        from core.database import engine
        limit = max(1, min(int(args.get("limit") or 25), 200))
        sql = (
            f"SELECT id, name, engine, phone, status, owner_id, updated_at "
            f"FROM {storage.table_name('connections')} ORDER BY updated_at DESC LIMIT {limit}"
        )
        rows = storage.execute_raw(sql, {})
        return _ok({"connections": rows, "count": len(rows)})

    async def _get_analysis_queue(client, args, host):
        """Return the analysis queue: unseen conversation intervals plus a
        report of intervals already analyzed.

        Harness-owned state: the tool claims the intervals it hands out and
        seals them when the surrounding scheduled run finishes with
        success (claims of failed/aborted runs are released so the content
        is offered again). The model never writes this state.
        """
        gap = ANALYZER_INTERVAL_GAP_SECONDS
        try:
            gap = max(60, min(int(args.get("gap_seconds") or gap), 6 * 3600))
        except (TypeError, ValueError):
            pass
        now = int(time.time())
        lookback = _coerce_setting_int(
            _s(host, "analyzer_lookback_hours", 24), 24, 1, 168
        )
        since = now - lookback * 3600

        # Transition claims from finished runs BEFORE computing the queue,
        # so this run sees exactly what previous runs really completed.
        runs = []
        try:
            from core.database import SessionLocal as _SL
            from core.database import ScheduledTask as _ST
            from core.database import TaskRun as _TR
            db = _SL()
            try:
                task = db.query(_ST).filter(_ST.name == ANALYZER_TASK_NAME).first()
                if task is not None:
                    for r in (
                        db.query(_TR).filter(_TR.task_id == task.id)
                        .order_by(_TR.started_at.desc())
                        .limit(20)
                    ):
                        runs.append({
                            "id": r.id,
                            "started_at": _epoch(r.started_at),
                            "finished_at": _epoch(r.finished_at) if r.finished_at else None,
                            "status": r.status,
                            "result": r.result,
                        })
            finally:
                db.close()
        except Exception:
            logger.exception("whats2api_bridge: analysis_queue run lookup failed")

        _maybe_adopt_intervals(storage, host, now=now, gap_seconds=gap)

        claimed_rows = [
            r for r in _load_interval_rows(storage)
            if (r.get("status") or "") == "claimed"
        ]
        try:
            decisions = seal_claims(claimed_rows, runs, now=now)
            _apply_seal_decisions(storage, decisions)
        except Exception:
            logger.exception("whats2api_bridge: interval seal/release failed")

        covered_rows = [
            r for r in _load_interval_rows(storage)
            if (r.get("status") or "") in ("analyzed", "claimed")
        ]
        msgs = _fetch_window_messages(storage, since)
        new_intervals, done = compute_analysis_queue(
            msgs, covered_rows, now=now, gap_seconds=gap
        )

        logger.info(
            "whats2api_bridge: analysis_queue args=%s gap=%s since=%s "
            "msgs=%s covered=%s new=%s",
            sorted((args or {}).keys()), gap, since,
            len(msgs), len(covered_rows), len(new_intervals),
        )
        trimmed = []
        for iv in new_intervals[:_ANALYZER_QUEUE_MAX_INTERVALS]:
            iv = dict(iv)
            msgs_cut = iv["messages"][:_ANALYZER_QUEUE_MAX_MSGS]
            iv["truncated"] = len(iv["messages"]) > len(msgs_cut)
            iv["messages"] = [
                {
                    "id": m.get("id"),
                    "ts": m.get("ts"),
                    "push_name": m.get("push_name"),
                    "sender_jid": m.get("sender_jid"),
                    "text": str(m.get("text") or "")[:_ANALYZER_MSG_TEXT_CHARS],
                }
                for m in msgs_cut
            ]
            trimmed.append(iv)
        try:
            _claim_intervals(storage, trimmed, now=now)
        except Exception:
            logger.exception("whats2api_bridge: interval claim failed")

        done_report = [
            {
                "chat_jid": r.get("chat_jid"),
                "start_ts": r.get("start_ts"),
                "end_ts": r.get("end_ts"),
                "findings_summary": r.get("findings_summary"),
                "analyzed_at": r.get("analyzed_at"),
            }
            for r in sorted(
                done,
                key=lambda r: (r.get("chat_jid") or "", int(r.get("start_ts") or 0)),
            )
        ]
        return _ok({
            "since_ts": since,
            "gap_seconds": gap,
            "new_intervals": trimmed,
            "new_interval_count": len(trimmed),
            "overflow_intervals": max(0, len(new_intervals) - len(trimmed)),
            "done_intervals": done_report,
            "done_interval_count": len(done_report),
            "note": (
                "Analyze new_intervals only; persist findings for them. "
                "done_intervals are already persisted - never recreate those "
                "items. If new_intervals is empty there is nothing to do."
            ),
        })

    return [
        (
            "list_messages",
            {
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string", "description": "filter by connection id"},
                    "jid": {"type": "string", "description": "substring match on remote_jid"},
                    "direction": {"type": "string", "enum": ["in", "out"]},
                    "since": {"type": "integer", "description": "epoch seconds; only messages >= this"},
                    "text_search": {"type": "string", "description": "LIKE match on text"},
                    "limit": {"type": "integer", "description": "1-200, default 25"},
                },
                "additionalProperties": False,
            },
            _make_async_handler(host, _list_messages, needs_connection_id=False),
            "List recent WhatsApp messages that the bridge ingested from "
            "inbound and outbound chats. Filter by connection_id, "
            "remote_jid substring, direction (in/out), since (epoch "
            "seconds), and text_search. Returns the same rows the bridge "
            "persisted locally (whats2api_bridge__messages) — useful for "
            "examining what a contact sent or what the operator replied.",
        ),
        (
            "get_message",
            {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "message id (primary key)"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            _make_async_handler(host, _get_message, needs_connection_id=False),
            "Fetch one WhatsApp message by its bridge id (the same id from "
            "messages.upsert fan-out). Returns the normalized text plus "
            "the raw Baileys payload. Read-only — does not trigger any "
            "WhatsApp outbound call.",
        ),
        (
            "get_analysis_queue",
            {
                "type": "object",
                "properties": {
                    "gap_seconds": {
                        "type": "integer",
                        "description": "silence gap that splits conversation "
                        "intervals (default 2700 = 45 min)",
                    },
                },
                "additionalProperties": False,
            },
            _make_async_handler(host, _get_analysis_queue, needs_connection_id=False),
            "Analysis queue for periodic conversation review. Call FIRST, "
            "once per run. Returns new_intervals: conversation intervals "
            "(per chat, split on silent gaps) that no run has analyzed yet, "
            "with their messages inline; and done_intervals: intervals "
            "already analyzed, with what was persisted - never recreate "
            "those. Empty new_intervals means nothing to do this run. The "
            "harness owns the analyzed/claimed state; the caller never "
            "writes it.",
        ),
        (
            "list_events",
            {
                "type": "object",
                "properties": {
                    "event_name": {"type": "string"},
                    "connection_id": {"type": "string"},
                    "since": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            _make_async_handler(host, _list_events, needs_connection_id=False),
            "List every whats2api event the bridge captured: "
            "messages.upsert, messages.update, messages.delete, connection."
            "update, groups.upsert, presence.update, call, etc. Filter by "
            "event_name and connection_id. Useful for diagnosing what the "
            "bridge actually received vs what the agent sees as "
            "conversations.",
        ),
        (
            "list_connections_local",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
            _make_async_handler(host, _list_connections_local, needs_connection_id=False),
            "List the cached WhatsApp connections known by the bridge in "
            "this process — id, name, engine, phone, status, owner_id, "
            "updated_at. Read-only; no outbound call.",
        ),
    ]


# ── Transport tools (outbound WhatsApp) ─────────────────────────────────────
# The other half of the bridge. ``_read_tools`` above only reads what the
# webhook already ingested; these talk to whats2api and change WhatsApp
# state. They are the reason the extension has an ``ANALYZER_ALLOWED_TOOLS``
# allowlist at all — see ``_refuse_if_analyzer`` below, which is the only
# thing standing between the periodic analyzer and ``send_message``.


def _adopt_is_enabled(host) -> bool:
    """Whether the pre-intervalization migration is armed.

    Off by default, and that is the safe direction. Adoption marks content
    as already analyzed without anyone having analyzed it, so getting it
    wrong loses conversations silently; not running it costs a one-off
    re-offer of old content, which is visible and recoverable. An operator
    upgrading from a pre-interval build opts in once
    (``analyzer_adopt_existing``) and the flag clears itself.
    """
    return bool(_s(host, "analyzer_adopt_existing", False))


def _maybe_adopt_intervals(storage, host, *, now=None, gap_seconds=None) -> int:
    """Run the pre-intervalization migration if — and only if — armed.

    Single entry point on purpose. There were two callsites (enable and
    queue) and the enable one bypassed both the opt-in flag and the
    tracking boundary, so adoption ran on *every* enable and sealed
    whatever was in the window. Two gates in one place is the only way
    they stay in place.
    """
    if not _adopt_is_enabled(host):
        return 0
    try:
        # Resolved here, not as a default: this helper sits above the
        # interval-queue constants, and a default argument is evaluated at
        # def time.
        if gap_seconds is None:
            gap_seconds = ANALYZER_INTERVAL_GAP_SECONDS
        raw = _s(host, "analyzer_tracking_since", None)
        tracking_since = (
            _coerce_setting_int(raw, 0, 1, 2 ** 40) if raw else None
        )
        adopted = _adopt_existing_intervals(
            storage,
            now=int(now if now is not None else time.time()),
            gap_seconds=gap_seconds,
            tracking_since=tracking_since,
        )
        # One-shot: disarm, so a later wipe of the table can never re-arm
        # the migration behind the operator's back.
        host.get_settings().set("analyzer_adopt_existing", False)
        if adopted:
            logger.info(
                "whats2api_bridge: adopted %d pre-tracking interval(s)", adopted,
            )
        return adopted
    except Exception:
        logger.exception("whats2api_bridge: interval adoption failed")
        return 0


def _analyzer_session_ids():
    """Session ids belonging to the analyzer's scheduled task, or ``None``.

    Deliberately uncached. The first version memoised this for 30 s, which
    meant a freshly created or recreated analyzer task could answer calls
    for half a minute with an empty set — and an empty set is "nobody is
    the analyzer", i.e. the guard failing open on exactly the window where
    it matters. It is one indexed read against a table with one row; there
    is no micro-optimisation worth a fail-open path here.

    ``None`` means "could not find out", which the caller must treat as
    refuse. An empty set means "nobody" and means allow.

    Belt and braces: since C-8 landed, a crew whose allowlist names any
    ``mcp__*`` tool also has the rest of them denied by the scheduler. This
    still matters because it holds for crews that never mention MCP, and for
    any call path where no ``RequestAuthority`` is bound for the scheduler
    to filter.
    """
    try:
        from core.database import SessionLocal as _SL
        from core.database import ScheduledTask as _ST
        db = _SL()
        try:
            rows = (
                db.query(_ST.session_id)
                .filter(_ST.name == ANALYZER_TASK_NAME)
                .all()
            )
            return {r[0] for r in rows if r and r[0]}
        finally:
            db.close()
    except Exception:
        logger.warning(
            "whats2api_bridge: analyzer session lookup failed; refusing "
            "WhatsApp writes until it recovers", exc_info=True,
        )
        return None


def _refuse_if_analyzer(tool_name: str):
    """Return an error dict when the caller is the analyzer, else ``None``.

    The analyzer is read-only over WhatsApp by construction (§1 of the
    handoff): it persists findings to native Odysseus artifacts and never
    writes back to the chat.

    Two ways to refuse. The obvious one is "this session is the analyzer".
    The one that matters more in practice is "we could not find out": a
    failed lookup returns ``None`` rather than an empty set, because empty
    means "nobody" and would allow the write. Failing closed here costs at
    most a manual retry, since the analyzer never calls these tools anyway.
    """
    try:
        from src.agent_runtime.authority import active_request_authority
        auth = active_request_authority()
    except Exception:
        return _err(
            f"{tool_name} refused: could not verify the caller is not the "
            f"Conversation Analyzer.",
            blocked_by="analyzer_lookup_unavailable",
        )
    if auth is None:
        return None
    session_id = str(getattr(auth, "session_id", "") or "")
    if not session_id:
        return None
    ids = _analyzer_session_ids()
    if ids is None:
        return _err(
            f"{tool_name} refused: could not verify the caller is not the "
            f"Conversation Analyzer (analyzer session lookup failed). This "
            f"fails closed on purpose — retry once the database responds.",
            blocked_by="analyzer_lookup_unavailable",
        )
    if session_id not in ids:
        return None
    return _err(
        f"{tool_name} is not available to the Conversation Analyzer. The "
        f"analyzer is read-only over WhatsApp: it reads intervals and "
        f"persists findings to Odysseus artifacts, and never writes to a "
        f"chat. Use the bridge tools from an interactive session instead.",
        blocked_by="analyzer_read_only_policy",
    )


# ── Engine-aware RPC mapping ───────────────────────────────────────────────
#
# whats2api exposes the *same intent* under different method names and
# different wire shapes depending on the connection's engine, and the engine
# is fixed for the life of the connection. There is no single spelling that
# works for both, and guessing wrong does not fail loudly -- it fails at the
# WhatsApp server.
#
#   baileys  (JS, the classic Baileys client)
#   hypermeow (Go, whatsmeow speaking waE2E protojson)
#
# Verified against the provider's method reference, and against a live
# connection whose engine field reads "hypermeow". The names below are what
# each engine actually dispatches; the payload builders next to them are the
# shapes each engine's protobuf/JS object expects.

_ENGINE_METHODS: dict = {
    "baileys": {
        "send_text": "sendMessage",
        "send_reaction": "sendMessage",
        "send_media": "sendMessage",
        "mark_read": "readMessages",
        "send_presence": "sendPresenceUpdate",
        "is_on_whatsapp": "onWhatsApp",
        "delete_message": "sendMessage",
    },
    "hypermeow": {
        "send_text": "SendMessage",
        "send_reaction": "SendMessage",
        "send_media": "SendMessage",
        "mark_read": "MarkRead",
        "send_presence": "SendPresence",
        "is_on_whatsapp": "IsOnWhatsApp",
        "delete_message": "RevokeMessage",
    },
}

# Hypermeow wraps each message kind in its own protojson field, and the
# media fields require fileLength -- a number we cannot invent, since it is
# the byte length of the media being served.
_HYPERMEOW_MEDIA_FIELD = {
    "image": "imageMessage",
    "video": "videoMessage",
    "audio": "audioMessage",
    "document": "documentMessage",
    "ptt": "audioMessage",
}

_ENGINE_CACHE: dict = {"at": 0.0}
_ENGINE_TTL = 60.0


def _normalise_engine(raw) -> str:
    """``hypermeow``/``whatsmeow``/``baileys`` → the map key, default baileys."""
    value = str(raw or "").strip().lower()
    if value in ("hypermeow", "whatsmeow", "go", "whatsmeow (go)"):
        return "hypermeow"
    if value in ("baileys", "js", "baileys (js)"):
        return "baileys"
    return "baileys"


def _unpack_connections(payload) -> list:
    """Normalise ``GET /connections`` into a plain list.

    whats2api (a Feathers app) returns a paginated envelope
    ``{"total": n, "limit": …, "skip": …, "data": [...]}``, not a bare
    array -- which is what the provider actually does, verified live.
    Assuming a bare list silently produced ``connection_count: 0`` in
    ``health`` and an unreadable envelope from ``list_connections``.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "connections", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # A single connection document (from GET /connections/:id).
        if payload.get("_id") or payload.get("id"):
            return [payload]
    return []


async def _engine_for(client, cid: str, *, force: bool = False) -> str:
    """Which engine drives this connection.

    Read from the connection document and memoised briefly: it is immutable
    for the life of the connection, but a cache miss costs one GET and a
    wrong answer costs a failed send.
    """
    now = time.time()
    if (not force and _ENGINE_CACHE.get("cid") == cid
            and now - (_ENGINE_CACHE.get("at") or 0.0) < _ENGINE_TTL):
        return _ENGINE_CACHE.get("engine") or "baileys"
    engine = "baileys"
    try:
        r = await client.get(f"/connections/{cid}")
        if r.status_code < 400:
            engine = _normalise_engine(_unpack_connections(r.json())[0]
                                       .get("engine") if _unpack_connections(r.json())
                                       else None)
    except Exception:
        logger.debug("engine lookup failed for %s; assuming baileys", cid,
                     exc_info=True)
    _ENGINE_CACHE.update({"cid": cid, "engine": engine, "at": now})
    return engine


def _method_for(engine: str, intent: str) -> str:
    return (_ENGINE_METHODS.get(_normalise_engine(engine))
            or _ENGINE_METHODS["baileys"]).get(intent, intent)


def _jid_for_key(key: dict) -> str:
    """The chat a reaction/revoke targets, taken from the message key."""
    if not isinstance(key, dict):
        return ""
    return str(key.get("remoteJid") or key.get("remote_jid") or "")


def _text_params(engine: str, to: str, text: str, *,
                 quoted=None, mentions=None) -> list:
    """Positional params for a text send, per engine.

    ``params`` is an array the provider validates positionally -- sending an
    object returns ``"/params" must be array`` -- and the layout differs:

      hypermeow  ``[jid, message]``   (message is waE2E protojson)
      baileys    ``[jid, {text}, {quoted}?, {mentions}?]``

    Hypermeow nests a quote under ``contextInfo.quotedMessage`` and a
    mention under ``contextInfo.mentionedJid``; Baileys takes them as
    separate trailing objects. Learned from the provider, which rejects
    every other arrangement with a description of what it wanted.
    """
    if _normalise_engine(engine) == "hypermeow":
        # The `message` argument is a waE2E.Message protojson, so a bare
        # {"text": ...} is rejected with `proto: unknown field "text"`. The
        # text lives in the extendedTextMessage oneof member, which is also
        # where contextInfo (quote / mentions) has to sit. {"conversation":
        # ...} also validates and is the legacy simple form, but it cannot
        # carry a quote.
        extended: dict = {"text": text}
        context: dict = {}
        if quoted:
            context["quotedMessage"] = quoted
        if mentions:
            context["mentionedJid"] = list(mentions)
        if context:
            extended["contextInfo"] = context
        return [to, {"extendedTextMessage": extended}]
    params = [to, {"text": text}]
    if quoted:
        params.append({"quoted": quoted})
    if mentions:
        params.append({"mentions": list(mentions)})
    return params


def _reaction_params(engine: str, key: dict, emoji: str, *, to=None) -> list:
    """Positional params for a reaction, per engine.

    Hypermeow types the reaction as its own protojson message kind; Baileys
    nests it under ``react`` inside the message body.
    """
    chat = str(to or _jid_for_key(key) or "")
    if not chat:
        raise RuntimeError(
            "send_reaction needs the target chat: pass 'to', or a 'key' that "
            "carries remoteJid (the key from an inbound event has one)"
        )
    if _normalise_engine(engine) == "hypermeow":
        return [chat, {"reactionMessage": {"text": emoji, "key": key}}]
    return [chat, {"react": {"text": emoji, "key": key}}]


def _media_payload(engine: str, *, to: str, kind: str, url: str,
                   mimetype: str, caption=None, file_name=None,
                   file_length=None) -> dict:
    """The send-media body each engine expects.

    hypermeow needs the media nested under its own protojson field and
    requires ``fileLength``, which is the byte length of the media. We
    cannot invent it; the caller may pass it, and when it is missing the
    send is refused rather than sent into a guaranteed failure.
    """
    kind = str(kind or "").lower()
    if _normalise_engine(engine) == "hypermeow":
        field = _HYPERMEOW_MEDIA_FIELD.get(kind)
        if field is None:
            raise RuntimeError(
                f"send_media: {kind!r} has no hypermeow message field "
                f"(known: {sorted(set(_HYPERMEOW_MEDIA_FIELD))})"
            )
        if not file_length:
            raise RuntimeError(
                "send_media on a hypermeow connection requires file_length "
                "(the byte size of the media). whatsmeow rejects the send "
                "without it, and it cannot be derived here."
            )
        media = {"url": url, "mimetype": mimetype, "fileLength": int(file_length)}
        if field == "audioMessage" and kind == "ptt":
            media["ptt"] = True
        if caption:
            media["caption"] = caption
        if file_name:
            media["fileName"] = file_name
        return [to, {field: media}]
    # Baileys nests media under its own key per kind and wants the jid
    # separately: [jid, {image: {...}}].
    baileys_field = {"image": "image", "video": "video", "audio": "audio",
                     "ptt": "audio", "document": "document"}.get(kind)
    if baileys_field is None:
        raise RuntimeError(f"send_media: unsupported kind {kind!r}")
    media: dict = {"url": url, "mimetype": mimetype}
    if caption:
        media["caption"] = caption
    if file_name:
        media["fileName"] = file_name
    return [to, {baileys_field: media}]


def _transport_tools(host, storage: PluginStorage):
    """Outbound tools: connection lifecycle + WhatsApp writes.

    Same 4-tuple shape as :func:`_read_tools` (plus a 5th element,
    ``needs_guard``, so the registration loop can wrap them with
    ``_refuse_if_analyzer``). Every tool returns the SPEC §4.3 envelope.
    """

    async def _request(client, cid: str, method: str, params: dict):
        """POST /connections/request — the one endpoint every call funnels
        through, matching whats2api's Baileys/whatsmeow RPC surface.

        ``method`` must already be resolved for the connection's engine:
        the two engines do not share names (see ``_ENGINE_METHODS``).
        """
        r = await client.post(
            "/connections/request",
            json={"connectionId": cid, "method": method, "params": params},
        )
        r.raise_for_status()
        return r.json()

    # ── Connection lifecycle ────────────────────────────────────────────

    async def _list_connections(client, args, host):
        r = await client.get("/connections")
        r.raise_for_status()
        return _ok(_unpack_connections(r.json()))

    async def _get_connection(client, cid, args, host):
        r = await client.get(f"/connections/{cid}")
        r.raise_for_status()
        return _ok(r.json())

    async def _create_connection(client, args, host):
        body = {"name": args.get("name")}
        if args.get("engine"):
            body["engine"] = args["engine"]
        if args.get("webhook_url"):
            body["webhook_url"] = args["webhook_url"]
        r = await client.post("/connections", json=body)
        r.raise_for_status()
        return _ok(r.json())

    async def _patch_connection(client, cid, args, host):
        body = {k: args[k] for k in ("name", "webhook_url") if args.get(k)}
        if not body:
            return _err("patch_connection needs at least one of name, webhook_url")
        r = await client.patch(f"/connections/{cid}", json=body)
        r.raise_for_status()
        return _ok(r.json())

    async def _remove_connection(client, cid, args, host):
        r = await client.delete(f"/connections/{cid}")
        if r.status_code >= 400:
            r.raise_for_status()
        return _ok({"removed": cid})

    async def _start_connection(client, cid, args, host):
        r = await client.post(f"/connections/{cid}/start")
        r.raise_for_status()
        return _ok(r.json())

    async def _stop_connection(client, cid, args, host):
        r = await client.post(f"/connections/{cid}/stop")
        r.raise_for_status()
        return _ok(r.json())

    async def _health(client, args, host):
        """Reachability probe. Never raises — a failure IS the answer."""
        try:
            r = await client.get("/connections")
        except httpx.HTTPStatusError as e:
            return _ok({
                "healthy": False,
                "status_code": e.response.status_code,
                "connection_count": 0,
            })
        except httpx.HTTPError as e:
            return _ok({
                "healthy": False,
                "error": f"{type(e).__name__}: {e}",
                "connection_count": 0,
            })
        try:
            data = r.json()
        except Exception:
            return _ok({
                "healthy": False,
                "status_code": r.status_code,
                "connection_count": 0,
            })
        connections = _unpack_connections(data)
        return _ok({
            "healthy": r.status_code < 400,
            "status_code": r.status_code,
            "connection_count": len(connections),
            "connections": [
                {"id": c.get("_id") or c.get("id"),
                 "name": c.get("name"),
                 "engine": _normalise_engine(c.get("engine")),
                 "status": c.get("status")}
                for c in connections
            ],
        })

    # ── Messaging ───────────────────────────────────────────────────────

    async def _send_message(client, cid, args, host):
        to = args.get("to")
        text = args.get("text")
        if not to:
            return _err("send_message requires 'to' (the recipient remote_jid)")
        if text is None:
            return _err("send_message requires 'text'")
        engine = await _engine_for(client, cid)
        params = _text_params(
            engine, to, text,
            quoted=args.get("quoted"), mentions=args.get("mentions"),
        )
        data = await _request(client, cid, _method_for(engine, "send_text"), params)
        try:
            dispatch_via_event_bus(
                "whats2api.message.sent",
                owner=None,
                payload={
                    "connection_id": cid,
                    "to": to,
                    "text": text,
                    "engine": engine,
                    "result": data,
                },
            )
        except Exception:
            logger.exception("whats2api.message.sent dispatch failed")
        return _ok(data)

    async def _send_reaction(client, cid, args, host):
        key = args.get("key")
        if not key:
            return _err("send_reaction requires 'key' (the message key to react to)")
        engine = await _engine_for(client, cid)
        try:
            params = _reaction_params(engine, key, args.get("text") or "",
                                      to=args.get("to"))
        except RuntimeError as e:
            return _err(str(e))
        return _ok(await _request(
            client, cid, _method_for(engine, "send_reaction"), params,
        ))

    async def _mark_read(client, cid, args, host):
        chat = args.get("chat")
        ids = args.get("ids")
        if not chat:
            return _err("mark_read requires 'chat' (the remote_jid)")
        if not ids:
            return _err("mark_read requires 'ids' (a non-empty list of message ids)")
        if isinstance(ids, str):
            ids = [ids]
        engine = await _engine_for(client, cid)
        body = {"chat": chat, "ids": list(ids)}
        # hypermeow's MarkRead takes one struct; Baileys' readMessages takes
        # the chat and the ids as separate positional arguments.
        params = [body] if engine == "hypermeow" else [chat, list(ids)]
        return _ok(await _request(
            client, cid, _method_for(engine, "mark_read"), params,
        ))

    async def _send_media(client, cid, args, host):
        to = args.get("to")
        kind = args.get("kind")
        url = args.get("url")
        mimetype = args.get("mimetype")
        missing = [
            n for n, v in (
                ("to", to), ("kind", kind), ("url", url), ("mimetype", mimetype)
            ) if not v
        ]
        if missing:
            return _err("send_media requires " + ", ".join(missing))
        engine = await _engine_for(client, cid)
        try:
            params = _media_payload(
                engine, to=to, kind=kind, url=url, mimetype=mimetype,
                caption=args.get("caption"),
                file_name=args.get("file_name"),
                file_length=args.get("file_length"),
            )
        except RuntimeError as e:
            return _err(str(e))
        return _ok(await _request(
            client, cid, _method_for(engine, "send_media"), params,
        ))

    async def _send_presence(client, cid, args, host):
        engine = await _engine_for(client, cid)
        presence = args.get("presence") or "available"
        params = [presence] if engine == "hypermeow" else [presence]
        if args.get("to"):
            params.append(args["to"])
        return _ok(await _request(
            client, cid, _method_for(engine, "send_presence"), params,
        ))

    async def _is_on_whatsapp(client, cid, args, host):
        phones = args.get("phones")
        if not phones:
            return _err("is_on_whatsapp requires 'phones' (a non-empty list)")
        if isinstance(phones, str):
            phones = [phones]
        engine = await _engine_for(client, cid)
        # One positional string per phone. The provider answers
        # `cannot unmarshal object into Go value of type string` for both
        # {"phones": [...]} and [[...]] -- only ["..."] is accepted.
        return _ok(await _request(
            client, cid, _method_for(engine, "is_on_whatsapp"),
            [str(p) for p in phones],
        ))

    async def _call_method(client, cid, args, host):
        """Generic RPC escape hatch — the method string goes upstream verbatim."""
        method = args.get("method")
        if not method:
            return _err("call_method requires 'method'")
        params = args.get("params")
        if params is None:
            params = []
        elif isinstance(params, dict):
            # whats2api accepts either a positional list or a named object;
            # pass through whatever the caller wrote.
            pass
        elif not isinstance(params, list):
            params = [params]
        return _ok(await _request(client, cid, str(method), params))

    def _spec(name, props, required=(), needs_cid=False):
        schema = {
            "type": "object",
            "properties": props,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = list(required)
        return name, schema, needs_cid

    cid_prop = {
        "connection_id": {
            "type": "string",
            "description": "connection id; falls back to the default_connection_id setting",
        }
    }

    def _with(cid_props, extra=None):
        out = dict(cid_props)
        out.update(extra or {})
        return out

    specs = [
        _spec("list_connections", {}, needs_cid=False),
        _spec("get_connection", _with(cid_prop), ("connection_id",), needs_cid=True),
        _spec("create_connection", {
            "name": {"type": "string", "description": "display name (required)"},
            "engine": {"type": "string", "description": "baileys or whatsmeow"},
            "webhook_url": {"type": "string", "description": "where whats2api posts events"},
        }, ("name",), needs_cid=False),
        _spec("patch_connection", _with(cid_prop, {
            "name": {"type": "string"},
            "webhook_url": {"type": "string"},
        }), ("connection_id",), needs_cid=True),
        _spec("remove_connection", _with(cid_prop), ("connection_id",), needs_cid=True),
        _spec("start_connection", _with(cid_prop), ("connection_id",), needs_cid=True),
        _spec("stop_connection", _with(cid_prop), ("connection_id",), needs_cid=True),
        _spec("refresh_connections", {}, needs_cid=False),
        _spec("health", {}, needs_cid=False),
        _spec("send_message", _with(cid_prop, {
            "to": {"type": "string", "description": "recipient remote_jid"},
            "text": {"type": "string", "description": "message body"},
            "quoted": {"type": "object", "description": "message key being quoted"},
            "mentions": {"type": "array", "items": {"type": "string"},
                         "description": "remote_jids to @-mention"},
        }), ("to", "text"), needs_cid=True),
        _spec("send_reaction", _with(cid_prop, {
            "key": {"type": "object", "description": "message key from a prior event"},
            "text": {"type": "string", "description": "the emoji; empty removes the reaction"},
            "to": {"type": "string",
                   "description": "target chat jid; taken from key.remoteJid when omitted"},
        }), ("key",), needs_cid=True),
        _spec("mark_read", _with(cid_prop, {
            "chat": {"type": "string", "description": "remote_jid to mark read"},
            "ids": {"type": "array", "items": {"type": "string"},
                    "description": "message ids to mark as read"},
        }), ("chat", "ids"), needs_cid=True),
        _spec("send_media", _with(cid_prop, {
            "to": {"type": "string"},
            "kind": {"type": "string",
                     "enum": ["image", "video", "audio", "document", "ptt"]},
            "url": {"type": "string", "description": "public or signed media URL"},
            "mimetype": {"type": "string"},
            "caption": {"type": "string"},
            "file_name": {"type": "string"},
            "file_length": {
                "type": "integer",
                "description": "byte size of the media; REQUIRED on a "
                               "hypermeow connection (whatsmeow rejects the "
                               "send without it)",
            },
        }), ("to", "kind", "url", "mimetype"), needs_cid=True),
        _spec("send_presence", _with(cid_prop, {
            "presence": {"type": "string",
                         "description": "available / unavailable / typing / recording"},
            "to": {"type": "string", "description": "optional single-jid presence"},
        }), (), needs_cid=True),
        _spec("is_on_whatsapp", _with(cid_prop, {
            "phones": {"type": "array", "items": {"type": "string"},
                       "description": "E.164 numbers to probe"},
        }), ("phones",), needs_cid=True),
        _spec("call_method", _with(cid_prop, {
            "method": {"type": "string",
                       "description": "RPC method name, passed upstream verbatim"},
            "params": {"description": "positional list or named object (default [])"},
        }), ("method",), needs_cid=True),
    ]

    fns = {
        "list_connections": (_list_connections, False),
        "get_connection": (_get_connection, True),
        "create_connection": (_create_connection, False),
        "patch_connection": (_patch_connection, True),
        "remove_connection": (_remove_connection, True),
        "start_connection": (_start_connection, True),
        "stop_connection": (_stop_connection, True),
        "refresh_connections": (_list_connections, False),
        "health": (_health, False),
        "send_message": (_send_message, True),
        "send_reaction": (_send_reaction, True),
        "mark_read": (_mark_read, True),
        "send_media": (_send_media, True),
        "send_presence": (_send_presence, True),
        "is_on_whatsapp": (_is_on_whatsapp, True),
        "call_method": (_call_method, True),
    }

    rich = {
        "list_connections": "List the WhatsApp connections registered on the "
                            "whats2api account, with their engine, phone and live status.",
        "get_connection": "Fetch one WhatsApp connection by id from whats2api.",
        "create_connection": "Register a new WhatsApp connection (a linked device) on "
                             "the whats2api account. Returns the connection id and the "
                             "QR/pairing payload whats2api issues.",
        "patch_connection": "Rename a WhatsApp connection or change the webhook URL "
                            "whats2api posts its events to.",
        "remove_connection": "Delete a WhatsApp connection from the account and drop its "
                             "linked device.",
        "start_connection": "Bring a WhatsApp connection back online after it was "
                            "stopped or dropped.",
        "stop_connection": "Take a WhatsApp connection offline without deleting it.",
        "refresh_connections": "Refresh and list the WhatsApp connections, picking up any "
                               "connection whose status changed on the provider side.",
        "health": "Check whether the whats2api API is reachable and how many WhatsApp "
                  "connections it currently reports. Never raises — the result is the "
                  "healthy flag plus the connection count.",
        "send_message": "Send a WhatsApp text message to a contact. WRITES to the chat. "
                        "Requires a connection_id (or the default_connection_id setting). "
                        "Returns the upstream message key.",
        "send_reaction": "React to a specific WhatsApp message with an emoji. WRITES to "
                         "the chat. An empty text removes an existing reaction.",
        "mark_read": "Mark one or more WhatsApp messages as read in a chat. WRITES read "
                     "receipt state for the operator's own number.",
        "send_media": "Send an image, video, audio, document or voice note to a contact "
                      "from a URL. WRITES to the chat.",
        "send_presence": "Publish the operator's WhatsApp presence (available, typing, "
                         "recording) for one chat or globally. WRITES presence state.",
        "is_on_whatsapp": "Check whether a list of phone numbers has a WhatsApp account. "
                          "Read-only despite living in the messaging family.",
        "call_method": "Call any whats2api/Baileys RPC method by name, passing params "
                       "through verbatim. The escape hatch for shapes the typed tools "
                       "above do not cover — and a WRITER, since it can reach any method.",
    }

    out = []
    for name, schema, needs_cid in specs:
        fn, cid_arg = fns[name]
        handler = _make_async_handler(host, fn, needs_connection_id=cid_arg)
        out.append((name, schema, handler, rich[name]))
    return out


# ── Inbound webhook handler ────────────────────────────────────────────────


# Filename (under the extension's data path) where we persist the inbound
# webhook token so a container restart doesn't rotate it out from under
# the operator's ``whats2api.com`` configuration. Delete this file to
# force a fresh token on the next enable.
_INBOUND_TOKEN_FILENAME = "inbound_webhook_token"


def _inbound_token_path(host) -> "Path":
    """Absolute path to the on-disk token file (under data/extensions/)."""
    return Path(host.get_data_path()) / _INBOUND_TOKEN_FILENAME


def _read_inbound_token(host) -> str | None:
    """Read the persisted token, or None if absent / unreadable."""
    try:
        with open(_inbound_token_path(host), "r", encoding="utf-8") as f:
            tok = f.read().strip()
        return tok or None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("whats2api_bridge: could not read inbound token file: %s", e)
        return None


def _write_inbound_token(host, token: str) -> None:
    """Persist the token with owner-only permissions (0600).

    Creates the parent directory first so this works both in production
    (where ``on_install`` ran and the data dir exists) and in tests
    (where the framework only creates the top-level ``data/``).
    """
    path = _inbound_token_path(host)
    try:
        os.makedirs(path.parent, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode("utf-8") + b"\n")
        finally:
            os.close(fd)
    except Exception as e:
        logger.warning("whats2api_bridge: could not persist inbound token: %s", e)
        raise


async def _async_pin_webhook_token(host, *, max_wait: float = 1.0) -> str | None:
    """Wait for the framework to finish ``_wire`` (which installs the
    freshly-generated webhook record) and then run the pinner.

    Without the wait, ``_pin_webhook_token`` would run before
    ``_webhooks[ext_id]`` is populated and return ``None``.
    """
    try:
        from core.extensions import webhooks as _oxs_wh
    except Exception:
        return None
    waited = 0.0
    while not _oxs_wh.list_for_extension(EXTENSION_ID) and waited < max_wait:
        await asyncio.sleep(0.01)
        waited += 0.01
    pinned = _pin_webhook_token(host)
    if pinned:
        logger.info("whats2api_bridge inbound webhook token pinned to disk")
    else:
        logger.warning(
            "whats2api_bridge: webhook record never appeared in the "
            "registry after %.2fs; token not pinned", max_wait,
        )
    return pinned


def _generate_url_token() -> str:
    """Generate a fresh URL-safe webhook token (the framework's default)."""
    import secrets as _secrets
    return _secrets.token_urlsafe(32)


def _validate_url_token(raw: str) -> None:
    """Reject tokens that wouldn't survive a URL round-trip. The only
    real constraint is the charset — ``[A-Za-z0-9_-]`` mirrors what
    ``secrets.token_urlsafe`` produces so any operator-typed value can
    replace a random one without rewriting the dispatcher. Length is
    bounded only to keep pathological inputs out of the URL path.
    """
    import re as _re
    if len(raw) > 1024:
        raise ValueError("webhook_url_token must be at most 1024 characters")
    if not _re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise ValueError(
            "webhook_url_token may only contain URL-safe characters "
            "(A–Z, a–z, 0–9, '-', '_') — the value goes into the URL path"
        )


def _apply_url_token(host, raw: str) -> None:
    """Persist ``raw`` to disk AND patch the live webhook record so the
    dispatcher accepts the new token immediately (no restart needed).

    Raises on persistence failure so the admin handler can return 5xx
    instead of leaving the operator thinking the URL changed when it
    actually didn't.
    """
    import re as _re  # noqa: F401  (mirrors the validation regex above)
    _validate_url_token(raw)
    # Persist to the same file the existing pinner reads on enable.
    _write_inbound_token(host, raw)
    # Patch the in-memory record so subsequent requests route correctly
    # and the next /admin/config GET returns the new full URL.
    try:
        from core.extensions import webhooks as _oxs_wh
    except Exception as e:
        raise RuntimeError(
            "webhook engine not importable; token persisted but "
            f"dispatcher not patched: {e}"
        )
    recs = _oxs_wh.list_for_extension(EXTENSION_ID)
    if not recs:
        # No live record yet (extension may be disabled). The file on
        # disk is the source of truth — the pinner will pick it up on
        # the next enable.
        return
    rec = recs[-1]
    rec.token_hash = _oxs_wh.hash_token(raw)
    rec._raw_token = raw


def _pin_webhook_token(host) -> str | None:
    """Pin the inbound webhook token across enable cycles.

    OXS's ``register_webhook`` always generates a fresh 32-byte token,
    which means every container restart silently invalidates the URL the
    operator configured in ``whats2api.com``. To keep the URL stable we:

      1. If a token file already exists on disk, rewrite the just-
         installed record's ``token_hash`` and ``_raw_token`` so the
         dispatcher keeps accepting the original token.
      2. Otherwise grab the token the framework just generated, save it
         to disk, and return it so the operator can copy the URL once.

    Returns the token the dispatcher will now accept (or ``None`` if
    the webhook wasn't actually installed — e.g. capability denied).
    """
    try:
        from core.extensions import webhooks as _oxs_wh
    except Exception:
        return None
    # The framework installs the record on the same call stack right after
    # ``on_enable`` returns. Poll briefly so every enable path — async
    # request, startup thread, tests — lands after the install instead of
    # failing silently and leaving a freshly-rotated token in place.
    recs = _oxs_wh.list_for_extension(EXTENSION_ID)
    waited = 0.0
    while not recs and waited < 2.0:
        time.sleep(0.05)
        waited += 0.05
        recs = _oxs_wh.list_for_extension(EXTENSION_ID)
    if not recs:
        logger.warning(
            "whats2api_bridge: webhook record never appeared; token NOT pinned"
        )
        return None
    rec = recs[-1]  # the one we just installed
    saved = _read_inbound_token(host)
    if saved:
        try:
            rec.token_hash = _oxs_wh.hash_token(saved)  # type: ignore[attr-defined]
            rec._raw_token = saved  # type: ignore[attr-defined]
            return saved
        except Exception as e:
            logger.warning("whats2api_bridge: token pin failed, using fresh: %s", e)
    fresh = _oxs_wh.get_raw_token(rec)
    if fresh:
        _write_inbound_token(host, fresh)
    return fresh


_EVENT_TO_OXS = {
    "messages.upsert": "whats2api.message.received",
    "Message": "whats2api.message.received",
    "messages.update": "whats2api.message.update",
    "messages.delete": "whats2api.message.delete",
    "messages.reaction": "whats2api.message.reaction",
    "messages.media-update": "whats2api.message.media",
    "message-receipt.update": "whats2api.message.receipt",
    "connection.update": "whats2api.connection.update",
    "chats.upsert": "whats2api.chat.upsert",
    "chats.update": "whats2api.chat.update",
    "chats.delete": "whats2api.chat.delete",
    "contacts.upsert": "whats2api.contact.upsert",
    "contacts.update": "whats2api.contact.update",
    "groups.upsert": "whats2api.group.update",
    "groups.update": "whats2api.group.update",
    "group-participants.update": "whats2api.group.participants",
    "presence.update": "whats2api.presence.update",
    "blocklist.set": "whats2api.blocklist.set",
    "blocklist.update": "whats2api.blocklist.update",
    "call": "whats2api.call",
}


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, dict):
        # Baileys Long shape: {"low": <int>, "high": <int>, "unsigned": bool}
        low = v.get("low")
        high = v.get("high", 0)
        if isinstance(low, int) and isinstance(high, int):
            return (high << 32) | (low & 0xFFFFFFFF)
    return None


def _synth_id_from_payload(payload: Any) -> str:
    """Deterministic id for a `Message` event whose payload is bare content.

    whats2api fans out per-message `Message` events with just the message
    body (no key wrapper, no messageTimestamp). The messages table needs
    a stable primary key, so we hash the canonicalized payload and prefix
    with ``synth:`` so it's obvious in the UI that the row came from a
    partial event. Same payload → same id → re-deliveries hit the unique
    constraint instead of duplicating the row. Once the row is enriched
    with the corresponding ``Info`` event the synthetic id is replaced
    by the real WhatsApp message id (``Info.ID``).
    """
    try:
        canonical = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        canonical = repr(payload)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:24]
    return "synth:" + digest


def _enrich_with_info(m: dict, info: dict) -> dict:
    """Fill the gaps a bare-content `Message` leaves in the row.

    The per-message ``Info`` event carries the metadata the bare
    ``Message`` payload doesn't have: real WhatsApp message id, chat
    jid, sender jid, push name, ISO timestamp, message type, from-me
    flag. Without this enrichment, the row would have a synthetic id
    and empty jids — useless for searching or correlating with the
    ``messages.upsert`` envelope when it arrives later.

    Only fills fields whose value the caller couldn't extract (i.e.
    rows coming from a bare-content shape, signalled by a synthetic
    ``synth:`` prefix on the id). Never overwrites a non-empty value
    the caller already extracted from a full ``messages.upsert``
    envelope — those rows already have every field and re-enriching
    would risk regressions if a key name ever drifts.
    """
    if not isinstance(info, dict) or not m:
        return m
    # Only enrich if the row came from a bare-content payload. A row
    # from a full envelope already has every field populated; touching
    # it would only introduce drift risk.
    is_partial = isinstance(m.get("id"), str) and m["id"].startswith("synth:")
    if not is_partial:
        return m
    real_id = info.get("ID")
    if real_id:
        m["id"] = str(real_id)
    chat = info.get("Chat")
    if chat:
        m["chat_jid"] = str(chat)
        m["remote_jid"] = str(chat)
    sender_alt = info.get("SenderAlt") or info.get("Sender")
    if sender_alt:
        m["sender_jid"] = str(sender_alt)
    push = info.get("PushName")
    if push:
        m["push_name"] = str(push)
    ts_iso = info.get("Timestamp")
    if ts_iso:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
            parsed = int(dt.timestamp())
            if parsed > 0:
                m["ts"] = parsed
        except Exception:
            pass
    mtype = info.get("Type")
    if mtype:
        m["message_type"] = str(mtype)
    if "IsFromMe" in info:
        m["from_me"] = bool(info.get("IsFromMe"))
    return m


_BARE_CONTENT_KEYS = (
    "conversation",
    "extendedTextMessage",
    "imageMessage",
    "videoMessage",
    "audioMessage",
    "documentMessage",
    "stickerMessage",
    "contactMessage",
    "locationMessage",
    "messageContextInfo",
)


def _is_bare_content(payload: dict) -> bool:
    """True if the payload looks like a Baileys `message` field with no
    key/timestamp wrapper. Distinct from the standard `Message` event
    envelope (which carries `key` + `message` + `pushName`)."""
    if "key" in payload or "messageTimestamp" in payload:
        return False
    return any(k in payload for k in _BARE_CONTENT_KEYS)


def _extract_message(payload: Any) -> dict | None:
    """Return a normalized message dict from a Baileys message payload.

    whats2api relays three shapes:
      1. ``messages.upsert`` — ``{"type": "notify", "messages": [msg1, ...]}``
      2. ``Message`` — full envelope ``{"key": ..., "message": ...,
         "pushName": ..., "messageTimestamp": ...}``
      3. ``Message`` — bare content (the inner ``message`` field only,
         e.g. ``{"conversation": "oi", "messageContextInfo": {...}}``).
         whats2api fans out the Baileys sub-event with just the message
         body — no key, no timestamp. We extract the text, synthesize a
         deterministic id, and mark the row as ``partial`` so the user
         can still see it in the Messages tab.
    """
    if not isinstance(payload, dict):
        return None
    is_partial = False
    batch = payload.get("messages")
    if isinstance(batch, list) and batch:
        msg = batch[0]
    elif "key" in payload and "message" in payload:
        msg = payload
    elif _is_bare_content(payload):
        msg = {"message": payload}
        is_partial = True
    else:
        return None
    if not isinstance(msg, dict):
        return None
    key = msg.get("key") or {}
    inner = msg.get("message") or {}
    text = (
        inner.get("conversation")
        or (inner.get("extendedTextMessage") or {}).get("text")
        # Some messages wrap the text in messageContextInfo (limit-sharing
        # notifications, device-list messages) — pull the text out of it
        # when the regular fields are missing.
        or (inner.get("messageContextInfo") or {}).get("conversation")
        or ""
    )
    push_name = msg.get("pushName")
    ts = _coerce_int(msg.get("messageTimestamp")) or int(time.time())
    if is_partial:
        msg_id = _synth_id_from_payload(payload)
        remote_jid = ""
        chat_jid = ""
        sender_jid = ""
        message_type = "partial"
        raw_json = json.dumps(payload, default=str)[:65000]
    else:
        msg_id = key.get("id")
        remote_jid = key.get("remoteJid")
        chat_jid = key.get("remoteJid")
        sender_jid = key.get("participant")
        message_type = (payload.get("type") or "")
        raw_json = json.dumps(msg, default=str)[:65000]
    return {
        "id": msg_id,
        "remote_jid": remote_jid,
        "from_me": bool(key.get("fromMe")),
        "chat_jid": chat_jid,
        "sender_jid": sender_jid,
        "push_name": push_name,
        "text": text,
        "message_type": message_type,
        "ts": ts,
        "raw_json": raw_json,
    }


def _persist_message(storage: PluginStorage, direction: str, m: dict) -> None:
    if not m.get("id"):
        return
    try:
        storage.insert(
            "messages",
            id=m["id"],
            connection_id=m.get("connection_id", ""),
            direction=direction,
            remote_jid=m.get("remote_jid") or "",
            from_me=("1" if m.get("from_me") else "0"),
            chat_jid=m.get("chat_jid") or "",
            sender_jid=m.get("sender_jid") or "",
            push_name=m.get("push_name") or "",
            text=m.get("text") or "",
            message_type=m.get("message_type") or "",
            ts=int(m.get("ts") or int(time.time())),
            raw_json=m.get("raw_json") or "",
        )
    except Exception as e:
        logger.warning("whats2api_bridge: failed to persist message: %s", e)


def _persist_event(
    storage: PluginStorage,
    event_name: str,
    connection_id: str,
    user_id: str,
    payload: Any,
) -> None:
    if not _s_for_storage_get("log_inbound_events", True):
        return
    try:
        storage.insert(
            "events",
            event_name=event_name,
            connection_id=connection_id or "",
            user_id=user_id or "",
            received_at=int(time.time()),
            raw_json=json.dumps(payload, default=str)[:65000],
        )
    except Exception as e:
        logger.warning("whats2api_bridge: failed to persist event: %s", e)


_settings_value_cache: dict[str, Any] = {}


def _s_for_storage_get(key: str, default: Any = None) -> bool:
    """Cheap cache of bool settings used inside the webhook hot path.

    The webhook handler runs in the asyncio loop and we don't want to
    touch the SettingsNamespace for every event (cheap reads but adds
    up under high-volume chats).
    """
    return _settings_value_cache.get(key, default)


def _build_webhook_handler(host, storage: PluginStorage):
    """Return the inbound webhook handler closure.

    The OXS framework calls the registered handler via::

        await rec.handler(body=..., headers=..., query=..., event_filter=...)

    so the original signature is positional kwargs. FastAPI, however,
    introspects every registered route and tries to interpret unannotated
    parameters (body / headers / query) as request inputs — which fails
    pydantic validation with HTTP 422. The clean fix is to expose a
    handler with the exact kwargs the OXS dispatcher passes — but bypass
    FastAPI's introspection entirely by NOT registering this function as
    a route. OXS dispatches the handler via an ``asyncio.create_task`` in
    ``_run_handler``; the route is the OXS-owned ``receive_webhook`` which
    has a proper FastAPI ``Request`` parameter.

    To keep both worlds happy we accept the kwargs explicitly here. Even
    though FastAPI would normally treat ``body`` / ``headers`` / ``query``
    as request inputs, the OXS route mount inspects via ``_wrap_async`` and
    delegates to ``asyncio.to_thread`` for sync functions — which means
    this handler is invoked from a background task without going through
    FastAPI's request validation at all.
    """
    async def _on_webhook(*, body=None, headers=None, query=None, event_filter=None):
        # FastAPI sees `body` / `headers` / `query` as required request
        # inputs (Body / Header / Query) and rejects the request with
        # HTTP 422 because the OXS framework invokes us with keyword
        # arguments, not as a real FastAPI handler. FastAPI 0.142 changed
        # the inference so that even ``= None`` defaults do not help when
        # the parameter name matches a reserved request-input identifier.
        # The OXS dispatcher runs the handler in a background task; it
        # never goes through FastAPI's request pipeline. We therefore
        # use ``*`` (keyword-only) so positional invocation is impossible
        # and rely on the caller to use ``handler(body=..., ...)``. The
        # default for each is None so accidental calls from OXS without
        # those kwargs would short-circuit cleanly.
        """Inbound webhook handler.

        The OXS framework dispatches the registered handler via::

            await rec.handler(body=..., headers=..., query=..., event_filter=...)

        FastAPI's parameter-name inference would normally treat unannotated
        ``body`` / ``headers`` / ``query`` as request inputs (Body / Header /
        Query) and fail pydantic validation with HTTP 422. We use the
        explicit ``Body()`` / ``Header()`` / ``Query()`` annotations and
        default to ``None`` so FastAPI treats them as fully optional and
        doesn't generate any required query/body parameters. The OXS
        framework's invocation is unaffected because kwargs are matched by
        parameter name, not by position.
        """
        """Inbound webhook handler.

        The OXS framework dispatches the registered handler via::

            await rec.handler(body=..., headers=..., query=..., event_filter=...)

        so the original parameter names are ``body`` / ``headers`` / ``query`` /
        ``event_filter``. FastAPI's parameter-name inference, however, treats
        ``body`` / ``headers`` / ``query`` as reserved request inputs which
        fails pydantic validation (HTTP 422). We rename the inbound
        parameters to ``*_in`` and rebind them to the original names here.
        """
        body = body
        headers = headers or {}
        connection_id = ""
        user_id = ""
        verified = False
        if isinstance(headers, dict):
            for k, v in headers.items():
                kl = k.lower()
                if kl == "whats2api" and str(v).lower() == "true":
                    verified = True
                elif kl == "userid":
                    user_id = str(v)
                elif kl == "connectionid":
                    connection_id = str(v)

        # ``body`` is the parsed JSON dict OR raw bytes if it wasn't JSON.
        if isinstance(body, bytes):
            try:
                body = json.loads(body.decode("utf-8"))
            except Exception as e:
                logger.warning("whats2api_bridge webhook: invalid JSON body: %s", e)
                return
        if not isinstance(body, dict):
            logger.warning("whats2api_bridge webhook: body is not a JSON object: %r", type(body))
            return

        if not verified:
            logger.warning(
                "whats2api_bridge webhook: missing/invalid 'whats2api: true' header "
                "(got headers=%r)",
                {k: v for k, v in (headers or {}).items()
                 if k.lower() in ("whats2api", "userid", "connectionid")},
            )

        # Body is a map of eventName -> payload. Per the docs, every
        # Baileys event from the ``ev`` bus is fanned out (creds.update
        # is filtered server-side).
        #
        # whats2api emits a per-message ``Info`` event with the chat jid,
        # sender jid, push name, message id and ISO timestamp right before
        # the bare-content ``Message`` event. We cache the most recent
        # ``Info`` in this body so a subsequent ``Message``/``RawMessage``
        # can fill in the metadata the bare payload doesn't carry.
        last_info: dict | None = None
        for event_name, payload in body.items():
            if not isinstance(event_name, str):
                continue
            oxs_event = _EVENT_TO_OXS.get(event_name)
            _persist_event(storage, event_name, connection_id, user_id, payload)

            if event_name == "Info" and isinstance(payload, dict):
                last_info = payload
                continue

            if event_name in ("messages.upsert", "Message", "RawMessage"):
                m = _extract_message(payload)
                if m:
                    if last_info is not None:
                        m = _enrich_with_info(m, last_info)
                    m["connection_id"] = connection_id
                    _persist_message(storage, "in", m)
                    if oxs_event and verified:
                        try:
                            dispatch_via_event_bus(
                                oxs_event,
                                owner=None,
                                payload={
                                    "connection_id": connection_id,
                                    "user_id": user_id,
                                    "message": {
                                        "id": m.get("id"),
                                        "remote_jid": m.get("remote_jid"),
                                        "from_me": m.get("from_me"),
                                        "push_name": m.get("push_name"),
                                        "text": m.get("text"),
                                        "ts": m.get("ts"),
                                        "type": m.get("message_type"),
                                    },
                                },
                            )
                        except Exception:
                            logger.exception("whats2api.message.received dispatch failed")
                continue

            if oxs_event and verified:
                try:
                    dispatch_via_event_bus(
                        oxs_event,
                        owner=None,
                        payload={
                            "connection_id": connection_id,
                            "user_id": user_id,
                            "event_name": event_name,
                            "data": payload,
                        },
                    )
                except Exception:
                    logger.exception("%s dispatch failed", oxs_event)

        # Refresh the connections cache on connection.update so the UI
        # can show the latest state without a manual refresh.
        if "connection.update" in body:
            try:
                await _refresh_connections_cache(host, storage, connection_id)
            except Exception:
                logger.exception("connection.update cache refresh failed")

    return _on_webhook


async def _refresh_connections_cache(host, storage: PluginStorage, hint_connection_id: str = "") -> None:
    """Best-effort cache refresh of the connections table."""
    try:
        client = _build_client(host)
    except RuntimeError:
        return
    try:
        _attach_auth(host, client)
        r = await client.get("/connections")
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("whats2api_bridge: cache refresh failed: %s", e)
        return
    finally:
        await client.aclose()

    if not isinstance(data, list):
        return
    now = int(time.time())
    # Upsert with INSERT OR REPLACE so we don't have to chase INSERT
    # errors when the connection is already cached.
    from sqlalchemy import text as _sa_text
    upsert_sql = _sa_text(
        "INSERT OR REPLACE INTO {tbl} "
        "(id, name, engine, phone, status, owner_id, raw_json, updated_at) "
        "VALUES (:id, :name, :engine, :phone, :status, :owner_id, :raw_json, :updated_at)"
        .format(tbl=storage.table_name("connections"))
    )
    with storage.engine.begin() as conn:
        for c in data:
            if not isinstance(c, dict):
                continue
            cid = c.get("_id") or c.get("id")
            if not cid:
                continue
            try:
                conn.execute(upsert_sql, {
                    "id": cid,
                    "name": (c.get("name") or ""),
                    "engine": (c.get("engine") or ""),
                    "phone": (c.get("phone") or ""),
                    "status": (c.get("status") or ""),
                    "owner_id": (c.get("ownerId") or c.get("owner_id") or ""),
                    "raw_json": json.dumps(c, default=str)[:65000],
                    "updated_at": now,
                })
            except Exception as e:
                logger.warning("whats2api_bridge: cache upsert failed: %s", e)


# ── Public read-only route for the static UI ────────────────────────────────


def _make_status_route(host, storage: PluginStorage):
    """Return a FastAPI-compatible callable for /ext-static status."""

    async def _route():  # type: ignore[no-untyped-def]
        # Public, unauthenticated, returns only non-secret stats.
        try:
            recent_events = storage.execute_raw(
                "SELECT event_name, connection_id, received_at "
                "FROM {tbl} ORDER BY received_at DESC LIMIT 10"
                .format(tbl=storage.table_name("events"))
            )
        except Exception:
            recent_events = []
        try:
            counts = storage.execute_raw(
                "SELECT direction, COUNT(*) AS n FROM {tbl} GROUP BY direction"
                .format(tbl=storage.table_name("messages"))
            )
        except Exception:
            counts = []
        return {
            "ok": True,
            "extension_id": EXTENSION_ID,
            "base_url": str(_s(host, "base_url", DEFAULT_BASE_URL)),
            "api_token_set": bool(_s(host, "api_token", "")),
            "default_connection_id": _s(host, "default_connection_id", "") or None,
            "recent_events": recent_events,
            "message_counts": counts,
            "inbound_url_pattern": (
                "/api/ext/" + EXTENSION_ID + "/webhook/<token>  "
                "(see Settings → Extensions → Whats2API Bridge → Webhooks "
                "for the live URL — token shown once at install)"
            ),
        }

    return _route


# ── whats2api.com MCP ────────────────────────────────────────────────────────
#
# This bridge exposes ONE controlled thing: the connection between
# Odysseus and whats2api.com's MCP server. Per the whats2api docs
# (https://whats2api.com/docs/mcp):
#
#   > MCP endpoint: https://api.whats2api.com/mcp
#   > Authorization: Bearer <token>
#
# That's all the operator ever configures — a single token from the
# whats2api dashboard, and an on/off switch. The transport, URL shape,
# Bearer header, and connection management are internal to Odysseus
# (handled by ``McpManager``); the bridge merely keeps a single row in
# ``mcp_servers`` in sync with the user's intent.


W2A_MCP_SERVER_ID = "whats2api"
W2A_MCP_NAME = "Whats2API MCP"
W2A_MCP_BASE_URL = "https://api.whats2api.com/mcp"


def _w2a_token_from_settings(host) -> str:
    try:
        return str(host.get_settings().get("whats2api_mcp_token") or "").strip()
    except Exception:
        return ""


def _w2a_enabled_from_settings(host) -> bool:
    try:
        return bool(host.get_settings().get("whats2api_mcp_enabled", False))
    except Exception:
        return False


async def _list_w2a_mcp(host) -> dict:
    """Return the single ``whats2api`` MCP integration's state.

    Tells the UI: whether a token is set, whether the integration is
    enabled, the live connection ``status`` and ``tool_count`` reported
    by ``McpManager``. Nothing about command/args/env/url — that's all
    internal.
    """
    token = _w2a_token_from_settings(host)
    enabled = _w2a_enabled_from_settings(host)
    status_info: dict = {}
    SessionLocal, McpServer = _mcp_models()
    db = SessionLocal()
    try:
        srv = db.query(McpServer).filter(McpServer.id == W2A_MCP_SERVER_ID).first()
        if srv:
            try:
                status_info = _mcp_manager().get_server_status(srv.id) or {}
            except Exception:
                status_info = {}
    finally:
        db.close()
    return {
        "ok": True,
        "id": W2A_MCP_SERVER_ID,
        "name": W2A_MCP_NAME,
        "enabled": bool(enabled),
        "token_set": bool(token),
        "status": status_info.get("status", "not_configured"),
        "tool_count": int(status_info.get("tool_count", 0) or 0),
        "error": status_info.get("error"),
    }


async def _update_w2a_mcp(host, body: dict) -> dict:
    """Apply ``token`` and/or ``enabled`` from the UI to settings + DB.

    The token is stored only in this extension's settings namespace
    (settings namespace is per-extension, in-memory). When enabled, we
    sync the row in ``mcp_servers`` so the live ``McpManager`` keeps a
    connected session. When disabled or the token is cleared, the row's
    ``is_enabled`` is flipped (or removed) and the connection tears
    down — the operator never sees URLs, headers, or env dicts.
    """
    if "token" in body:
        token = str(body.get("token") or "").strip()
        # whats2api tokens are 64 hex chars per the docs; allow common
        # variants without rejecting valid paste operations.
        if token and not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", token):
            return {"ok": False, "error": "token contains invalid characters"}
        try:
            host.get_settings().set("whats2api_mcp_token", token)
        except Exception as e:
            return {"ok": False, "error": f"could not persist token: {e}"}

    if "enabled" in body:
        try:
            host.get_settings().set(
                "whats2api_mcp_enabled", bool(body["enabled"])
            )
        except Exception as e:
            return {"ok": False, "error": f"could not persist enabled: {e}"}

    token = _w2a_token_from_settings(host)
    enabled = _w2a_enabled_from_settings(host)
    SessionLocal, McpServer = _mcp_models()
    db = SessionLocal()
    try:
        srv = db.query(McpServer).filter(McpServer.id == W2A_MCP_SERVER_ID).first()
        if not enabled or not token:
            # Disable / drop the row — operator has nothing to connect to.
            if srv:
                srv.is_enabled = False
                db.commit()
            try:
                await _mcp_manager().disconnect_server(W2A_MCP_SERVER_ID)
            except Exception:
                pass
            return await _list_w2a_mcp(host)

        url = f"{W2A_MCP_BASE_URL.rstrip('/')}/{token}"
        if srv is None:
            srv = McpServer(
                id=W2A_MCP_SERVER_ID,
                name=W2A_MCP_NAME,
                transport="http",
                command=None,
                args=json.dumps([]),
                env=json.dumps({}),
                url=url,
                is_enabled=True,
            )
            db.add(srv)
        else:
            srv.name = W2A_MCP_NAME
            srv.transport = "http"
            srv.command = None
            srv.args = json.dumps([])
            srv.env = json.dumps({})
            srv.url = url
            srv.is_enabled = True
        db.commit()
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": f"db sync failed: {e}"}
    finally:
        db.close()

    # Reconnect the live session so the new token takes effect.
    mgr = _mcp_manager()
    try:
        await mgr.disconnect_server(W2A_MCP_SERVER_ID)
        await mgr.connect_server(
            server_id=W2A_MCP_SERVER_ID,
            name=W2A_MCP_NAME,
            transport="http",
            command=None,
            args=[],
            env={},
            url=url,
        )
    except Exception as e:
        logger.warning("whats2api_bridge: reconnect after token update failed: %s", e)

    return await _list_w2a_mcp(host)


# ── Admin routes (config + data browser) ───────────────────────────────────


def _admin_config_get(host, storage):
    """GET /ext-api/whats2api_bridge/admin/config — full config incl. webhook URL."""
    ns = host.get_settings()
    base_raw = _s(host, "base_url", DEFAULT_BASE_URL)
    base = str(base_raw).strip().rstrip("/") if base_raw else DEFAULT_BASE_URL
    if not base:
        base = DEFAULT_BASE_URL

    # Look up the installed webhook to surface the full URL + token. The
    # token is only returned to an authenticated admin; this endpoint
    # is registered with auth="admin" below.
    webhook_url = None
    raw_token = None
    try:
        from core.extensions import webhooks as _oxs_wh
        recs = _oxs_wh.list_for_extension(EXTENSION_ID)
        if recs:
            rec = recs[0]
            webhook_url = f"/api/ext/{EXTENSION_ID}/webhook/{_oxs_wh.get_raw_token(rec)}"
            raw_token = _oxs_wh.get_raw_token(rec)
    except Exception:
        pass

    return {
        "ok": True,
        "base_url": base,
        "default_base_url": DEFAULT_BASE_URL,
        "api_token_set": bool(ns.get("api_token")),
        # The URL token is always populated after first enable (it's
        # either user-defined or the framework-generated one pinned to
        # disk). Used by the UI to set the field placeholder.
        "webhook_url_token_set": bool(_read_inbound_token(host)),
        "default_connection_id": ns.get("default_connection_id") or "",
        "request_timeout_seconds": int(ns.get("request_timeout_seconds") or 30),
        "retention_days": int(ns.get("retention_days") or 7),
        "log_inbound_events": bool(ns.get("log_inbound_events", True)),
        "webhook_url": webhook_url,
    }


def _admin_config_post(host, storage, body: dict):
    """POST /ext-api/whats2api_bridge/admin/config — update settings."""
    ns = host.get_settings()
    if "base_url" in body and body["base_url"]:
        candidate = str(body["base_url"]).strip().rstrip("/")
        if not candidate:
            candidate = DEFAULT_BASE_URL
        if not candidate.startswith("https://"):
            raise ValueError("base_url must start with https://")
        ns.set("base_url", candidate)
    if "default_connection_id" in body:
        ns.set("default_connection_id", str(body["default_connection_id"] or ""))
    if "api_token" in body:
        # Allow setting OR clearing the token (empty string = clear).
        ns.set("api_token", str(body["api_token"] or ""))
    if "api_email" in body:
        ns.set("api_email", str(body["api_email"] or "").strip())
    if "api_password" in body:
        # Setting a new password invalidates the cached JWT: it may have
        # been rotated, and the next call would otherwise keep presenting
        # a token minted with the old one for up to a day.
        ns.set("api_password", str(body["api_password"] or ""))
        _JWT_CACHE["token"] = ""
        _JWT_CACHE["at"] = 0.0
    if "webhook_url_token" in body:
        # The user-defined path token. Empty string → rotate to a fresh
        # random 32-byte token. Validation enforces URL-safe characters
        # and a sane minimum length; the field is then persisted to the
        # ``<data>/whats2api_bridge/inbound_webhook_token`` file (mode
        # 0600) AND the live ``_webhooks[ext_id]`` record is patched in
        # place so the dispatcher starts accepting the new token
        # immediately — no container restart required.
        raw = str(body["webhook_url_token"] or "").strip()
        if not raw:
            raw = _generate_url_token()
        else:
            _validate_url_token(raw)
        _apply_url_token(host, raw)
    if "request_timeout_seconds" in body:
        try:
            n = int(body["request_timeout_seconds"])
        except (TypeError, ValueError):
            raise ValueError("request_timeout_seconds must be an integer")
        ns.set("request_timeout_seconds", max(1, min(n, 300)))
    if "retention_days" in body:
        try:
            n = int(body["retention_days"])
        except (TypeError, ValueError):
            raise ValueError("retention_days must be an integer")
        ns.set("retention_days", max(1, min(n, 365)))
    if "log_inbound_events" in body:
        ns.set("log_inbound_events", bool(body["log_inbound_events"]))
    return {"ok": True}


# ── Data-browser search/edit/delete ────────────────────────────────────────


def _where_clause(filters: dict, allowed: tuple) -> tuple[str, dict]:
    """Build a SQL WHERE fragment + bound params from a filters dict.

    Whitelists column names to prevent SQL injection from query params.
    Supported operators per column: ``q`` (LIKE on text columns),
    ``since``/``until`` (numeric >=/<= on the ``ts``/``received_at``
    column), and exact-match for the rest.
    """
    clauses, params = [], {}
    for col, val in (filters or {}).items():
        if col in ("limit", "offset") or val is None or val == "":
            continue
        if col not in allowed:
            continue
        if col == "q":
            clauses.append("LOWER(CAST(text AS TEXT)) LIKE :q")
            params["q"] = f"%{str(val).lower()}%"
        elif col == "since":
            clauses.append("ts >= :since")
            try:
                params["since"] = int(val)
            except (TypeError, ValueError):
                continue
        elif col == "until":
            clauses.append("ts <= :until")
            try:
                params["until"] = int(val)
            except (TypeError, ValueError):
                continue
        else:
            clauses.append(f"{col} = :{col}")
            params[col] = val
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _admin_messages_get(host, storage, **filters):
    allowed = ("connection_id", "chat_jid", "direction", "q", "since", "until", "text")
    where, params = _where_clause(filters, allowed)
    limit = int(filters.get("limit") or 50)
    offset = int(filters.get("offset") or 0)
    sql = (
        "SELECT id, connection_id, direction, remote_jid, chat_jid, sender_jid, "
        "push_name, text, message_type, ts, raw_json "
        f"FROM {storage.table_name('messages')} {where} "
        f"ORDER BY ts DESC LIMIT {limit} OFFSET {offset}"
    )
    rows = storage.execute_raw(sql, params)
    total_sql = f"SELECT COUNT(*) AS n FROM {storage.table_name('messages')} {where}"
    total = storage.execute_raw(total_sql, params)
    return {"ok": True, "messages": rows, "total": total[0]["n"] if total else 0}


def _admin_events_get(host, storage, **filters):
    allowed = ("event_name", "connection_id")
    where, params = _where_clause(filters, allowed)
    limit = int(filters.get("limit") or 50)
    offset = int(filters.get("offset") or 0)
    sql = (
        "SELECT id, event_name, connection_id, user_id, received_at, raw_json "
        f"FROM {storage.table_name('events')} {where} "
        f"ORDER BY received_at DESC LIMIT {limit} OFFSET {offset}"
    )
    rows = storage.execute_raw(sql, params)
    total_sql = f"SELECT COUNT(*) AS n FROM {storage.table_name('events')} {where}"
    total = storage.execute_raw(total_sql, params)
    return {"ok": True, "events": rows, "total": total[0]["n"] if total else 0}


def _admin_connections_get(host, storage, **filters):
    allowed = ("status", "owner_id")
    where, params = _where_clause(filters, allowed)
    limit = int(filters.get("limit") or 100)
    offset = int(filters.get("offset") or 0)
    sql = (
        "SELECT id, name, engine, phone, status, owner_id, updated_at "
        f"FROM {storage.table_name('connections')} {where} "
        f"ORDER BY updated_at DESC LIMIT {limit} OFFSET {offset}"
    )
    rows = storage.execute_raw(sql, params)
    total_sql = f"SELECT COUNT(*) AS n FROM {storage.table_name('connections')} {where}"
    total = storage.execute_raw(total_sql, params)
    return {"ok": True, "connections": rows, "total": total[0]["n"] if total else 0}


def _admin_delete_row(storage, table: str, row_id):
    full = storage.table_name(table)
    if table == "events":
        try:
            row_id = int(row_id)
        except (TypeError, ValueError):
            return 0
    sql = f"DELETE FROM {full} WHERE id = :id"
    return storage.execute_write(sql, {"id": row_id})


def _admin_message_patch(host, storage, row_id: str, body: dict):
    """PATCH text/push_name fields on a single message row."""
    sets, params = [], []
    if "text" in body:
        sets.append("text = :text")
        params["text"] = str(body["text"] or "")
    if "push_name" in body:
        sets.append("push_name = :push_name")
        params["push_name"] = str(body["push_name"] or "")
    if not sets:
        return {"ok": True, "updated": 0}
    params["id"] = row_id
    sql = f"UPDATE {storage.table_name('messages')} SET {', '.join(sets)} WHERE id = :id"
    n = storage.execute_write(sql, params)
    return {"ok": True, "updated": n}


def _make_admin_handlers(host, storage):
    """Build FastAPI-compatible callables for the admin endpoints.

    Returns a dict ``{path: {method: handler}}``; the registry's
    ``register`` function iterates and registers each.
    """
    import json as _json
    from fastapi import Request, Body, Query, Header

    async def _read_json(request: Request) -> dict:
        try:
            raw = await request.body()
            return _json.loads(raw or b"{}")
        except Exception:
            return {}

    async def _get_config(request: Request):
        return _admin_config_get(host, storage)

    async def _post_config(request: Request):
        body = await _read_json(request)
        try:
            return _admin_config_post(host, storage, body)
        except ValueError as e:
            from fastapi import HTTPException
            raise HTTPException(400, str(e))

    async def _get_messages(request: Request):
        qp = dict(request.query_params)
        return _admin_messages_get(host, storage, **qp)

    async def _get_events(request: Request):
        return _admin_events_get(host, storage, **dict(request.query_params))

    async def _get_connections(request: Request):
        return _admin_connections_get(host, storage, **dict(request.query_params))

    async def _del_message(request: Request, message_id: str):
        _admin_delete_row(storage, "messages", message_id)
        return {"ok": True}

    async def _del_event(request: Request, event_id: str):
        _admin_delete_row(storage, "events", event_id)
        return {"ok": True}

    async def _del_connection(request: Request, connection_id: str):
        _admin_delete_row(storage, "connections", connection_id)
        return {"ok": True}

    async def _patch_message(request: Request, message_id: str):
        body = await _read_json(request)
        return _admin_message_patch(host, storage, message_id, body)

    # ── whats2api MCP (single integration) ───────────────────────────────
    # Two endpoints cover the entire surface area: ``GET /admin/mcp``
    # for state + ``PATCH /admin/mcp`` for token / active toggle.
    async def _get_mcp(request: Request):
        return await _list_w2a_mcp(host)

    async def _patch_mcp(request: Request):
        body = await _read_json(request)
        return await _update_w2a_mcp(host, body)

    # ── Conversation Analyzer ────────────────────────────────────────
    # GET for status (settings + scheduled-task row + last/next run).
    # PATCH for updating config (toggles fields + reconciles the
    # ScheduledTask row in the same call). POST run-now triggers an
    # immediate execution via the Odysseus task scheduler.
    async def _get_analyzer(request: Request):
        return _admin_analyzer_get(host, storage, _request_owner(request))

    async def _patch_analyzer(request: Request):
        body = await _read_json(request)
        # Authority is now derived automatically by the core —
        # ``restore_task_authority`` falls back to ``create_request_authority(prompt)``
        # when the OXS-created ScheduledTask has no sealed
        # ``request_authority_json``. We just hand the prompt (and the
        # operator) to the reconcile path; the core builds the
        # authority with grants derived from the prompt intent.
        try:
            return _admin_analyzer_patch(
                host, storage, body, _request_owner(request)
            )
        except ValueError as e:
            from fastapi import HTTPException
            raise HTTPException(400, str(e))

    async def _post_analyzer_run_now(request: Request):
        return await _admin_analyzer_run_now(host, request)

    async def _get_analyzer_runs(request: Request):
        qp = dict(request.query_params)
        try:
            limit = int(qp.get("limit", 25))
        except (TypeError, ValueError):
            limit = 25
        return _admin_analyzer_list_runs(
            host, storage, limit=limit, owner=_request_owner(request)
        )

    async def _get_analyzer_run(request: Request, run_id: str):
        return _admin_analyzer_get_run(
            host, storage, run_id, _request_owner(request)
        )

    return {
        "/ext-api/whats2api_bridge/admin/config": {"GET": _get_config, "POST": _post_config},
        "/ext-api/whats2api_bridge/admin/messages": {"GET": _get_messages},
        "/ext-api/whats2api_bridge/admin/messages/{message_id}": {
            "DELETE": _del_message, "PATCH": _patch_message,
        },
        "/ext-api/whats2api_bridge/admin/events": {"GET": _get_events},
        "/ext-api/whats2api_bridge/admin/events/{event_id}": {"DELETE": _del_event},
        "/ext-api/whats2api_bridge/admin/connections": {"GET": _get_connections},
        "/ext-api/whats2api_bridge/admin/connections/{connection_id}": {"DELETE": _del_connection},
        "/ext-api/whats2api_bridge/admin/mcp": {
            "GET": _get_mcp, "PATCH": _patch_mcp,
        },
        "/ext-api/whats2api_bridge/admin/analyzer": {
            "GET": _get_analyzer, "PATCH": _patch_analyzer,
        },
        "/ext-api/whats2api_bridge/admin/analyzer/run-now": {
            "POST": _post_analyzer_run_now,
        },
        "/ext-api/whats2api_bridge/admin/analyzer/runs": {
            "GET": _get_analyzer_runs,
        },
        "/ext-api/whats2api_bridge/admin/analyzer/runs/{run_id}": {
            "GET": _get_analyzer_run,
        },
    }


# ── Conversation Analyzer ──────────────────────────────────────────────────


def _analyzer_settings(host) -> dict:
    """Snapshot of the analyzer settings, with safe defaults."""
    ns = host.get_settings()
    out = {
        "enabled": bool(ns.get("analyzer_enabled", False)),
        "interval_minutes": _coerce_setting_int(ns.get("analyzer_interval_minutes", 30), 30, 5, 720),
        "lookback_hours":   _coerce_setting_int(ns.get("analyzer_lookback_hours",   24), 24, 1, 168),
        "min_messages":     _coerce_setting_int(ns.get("analyzer_min_messages",     3),  3, 1, 50),
        "endpoint_id":      str(ns.get("analyzer_endpoint_id", "") or "").strip(),
        "model":            str(ns.get("analyzer_model", "") or "").strip(),
        "prompt":           str(ns.get("analyzer_prompt", "") or "").strip() or DEFAULT_ANALYZER_PROMPT,
    }
    return out


def _coerce_setting_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


# ── Conversation-interval analysis queue ───────────────────────────────────
#
# The analyzer job re-runs every N minutes over a rolling window. Feeding
# the same raw messages to every run makes duplication the predictable
# outcome (the model has no memory of prior runs). Instead the harness
# partitions the window into conversation intervals (per chat, split on
# silent gaps), keeps state about which intervals were already analyzed,
# and hands the model only the unseen ones plus a report of what was done
# before. "Do nothing" is then a normal, correct outcome.

ANALYZER_INTERVAL_GAP_SECONDS = 45 * 60
_ANALYZER_CLAIM_TTL_SECONDS = 2 * 3600
_ANALYZER_RUN_SLACK_SECONDS = 120
_ANALYZER_QUEUE_MAX_INTERVALS = 10
_ANALYZER_QUEUE_MAX_MSGS = 40
_ANALYZER_MSG_TEXT_CHARS = 500


def _interval_id(chat_jid: str, start_ts: int, end_ts: int) -> str:
    return hashlib.sha1(f"{chat_jid}|{start_ts}|{end_ts}".encode()).hexdigest()[:16]


def _msg_ts(m) -> int:
    try:
        return int(m.get("ts") or 0)
    except (TypeError, ValueError):
        return 0


def compute_analysis_queue(messages, analyzed_rows, *, now,
                           gap_seconds=ANALYZER_INTERVAL_GAP_SECONDS):
    """Partition messages into sealed, unanalyzed conversation intervals.

    Pure function — the harness state layer feeds ``analyzed_rows`` in and
    gets determinism back:

    - a message is *covered* when its ts falls inside an already claimed or
      analyzed range of the same chat (late arrivals into an old interval
      are therefore never reprocessed);
    - the survivors are grouped per chat and split on silent gaps of
      ``gap_seconds``;
    - only *sealed* groups are returned: quiet for a full gap, so they
      cannot grow underneath the model while it reasons about them.

    Returns ``(new_intervals, done_intervals)``. Each new interval carries
    its messages inline and a stable ``id``.
    """
    def covered(chat, ts):
        return any(
            (r.get("chat_jid") or "") == chat
            and int(r.get("start_ts") or 0) <= ts <= int(r.get("end_ts") or 0)
            for r in analyzed_rows
        )

    by_chat = {}
    for m in messages:
        chat = m.get("chat_jid") or ""
        ts = _msg_ts(m)
        if covered(chat, ts):
            continue
        by_chat.setdefault(chat, []).append(m)

    new_intervals = []
    for chat in sorted(by_chat):
        msgs = sorted(by_chat[chat], key=_msg_ts)
        groups, cur = [], [msgs[0]]
        for prev, msg in zip(msgs, msgs[1:]):
            if _msg_ts(msg) - _msg_ts(prev) >= gap_seconds:
                groups.append(cur)
                cur = [msg]
            else:
                cur.append(msg)
        groups.append(cur)
        for g in groups:
            start_ts, end_ts = _msg_ts(g[0]), _msg_ts(g[-1])
            if now - end_ts < gap_seconds:
                continue  # not sealed yet
            new_intervals.append({
                "id": _interval_id(chat, start_ts, end_ts),
                "chat_jid": chat,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "msg_count": len(g),
                "messages": g,
            })
    return new_intervals, list(analyzed_rows)


# Extensions are AST-audited before enable and ``re.compile`` trips the
# forbidden-call list (it exists to block ``compile`` as an exec vector).
# Match the disclosure with plain string ops -- it is a fixed server string.
_EFFECT_DISCLOSURE_TAIL = (
    "reported success; any external change it made was not independently verified."
)


def _run_produced_an_answer(run) -> bool:
    """Whether a run demonstrably engaged with the analysis queue.

    ``status == "success"`` only means the agent loop did not error. What it
    does *not* mean is that the loop saw an interval, so it is not enough on
    its own (A-1).

    The second signal is the effect disclosure the agent loop appends for
    external calls. Its presence proves the model actually called
    ``get_analysis_queue``; a run that answers straight from the prompt
    leaves no disclosure at all, and that is exactly the A-1 case where
    sealing would drop the interval forever.

    Why prose is *not* the signal any more
    --------------------------------------
    Prose and work are independent. A live run analysed "Aman�� vamo tomar
    sorvete" from a real WhatsApp message, wrote a note AND a calendar
    event, then finished with six empty rounds -- result was the disclosure
    and nothing else. Judged on prose that reads as "did nothing", so the
    claim was released, and every subsequent run re-analysed the same
    interval and wrote the note and the event again.

    Both states are observationally identical from the run record: no prose,
    disclosure present. There is no tool-execution table to break the tie.
    So the tie is broken towards sealing, because the failure modes are not
    symmetric -- duplicates are visible and deletable, a silently dropped
    conversation is neither, and the retry loop makes duplication certain
    rather than possible.

    What this gives up: a run that reads the queue, decides nothing is worth
    persisting, and then stays silent still seals. The prompt asks for an
    explicit "no actionable items" line in that case, and prose is still
    honoured as a signal when present -- only the silence is forgiven.
    """
    result = str((run or {}).get("result") or "")
    if "get_analysis_queue" in result:
        return True
    kept = []
    for line in result.splitlines():
        marker = line.find("External operation")
        if marker >= 0 and _EFFECT_DISCLOSURE_TAIL in line[marker:]:
            # Keep anything the model wrote before the disclosure on this line.
            line = line[:marker]
        kept.append(line)
    return bool("".join(kept).strip())


def seal_claims(claim_rows, runs, *, now,
                ttl_seconds=_ANALYZER_CLAIM_TTL_SECONDS):
    """Decide what happens to claimed intervals once runs have finished.

    ``runs`` items are ``{id, started_at, finished_at, status, result}``
    with epoch seconds (``finished_at`` may be ``None`` while in flight):

    - a claim made during a run that later finished ``success`` *and produced
      an answer* is sealed (the interval is analyzed; the run's result becomes
      its summary). A success that only emitted the effect disclosure sealed
      nothing -- that run never looked at the interval;
    - a claim whose run finished without success is released so the
      interval re-enters the queue — no silent loss on failure;
    - a claim older than ``ttl_seconds`` is released the same way (crash
      safety: a run that never came back cannot black-hole content);
    - anything else is kept (the run is still executing).
    """
    decisions = {"seal": [], "release": [], "keep": []}
    for row in claim_rows:
        if (row.get("status") or "") != "claimed":
            continue
        try:
            claimed_at = int(row.get("claimed_at") or 0)
        except (TypeError, ValueError):
            claimed_at = 0
        sealed_by = None
        finished_after = False
        for run in runs:
            try:
                start = int(run.get("started_at") or 0)
                fin_raw = run.get("finished_at")
                fin = int(fin_raw) if fin_raw else None
            except (TypeError, ValueError):
                continue
            if fin is None or fin < claimed_at:
                continue
            finished_after = True
            if (
                (run.get("status") or "") == "success"
                and start - _ANALYZER_RUN_SLACK_SECONDS <= claimed_at <= fin
                and _run_produced_an_answer(run)
            ):
                sealed_by = run
                break
        if sealed_by is not None:
            decisions["seal"].append((row, sealed_by))
        elif finished_after or claimed_at < now - ttl_seconds:
            decisions["release"].append(row)
        else:
            decisions["keep"].append(row)
    return decisions


def _run_result_excerpt(result) -> str:
    return (str(result) if result else "").strip()[:600]


def _epoch(dt) -> int:
    if not dt:
        return 0
    if isinstance(dt, (int, float)):
        return int(dt)
    try:
        from datetime import timezone as _tz
        return int(dt.replace(tzinfo=_tz.utc).timestamp())
    except Exception:
        return 0


def _interval_table(storage) -> str:
    return storage.table_name("intervals")


def _load_interval_rows(storage) -> list:
    return storage.execute_raw(
        f"SELECT id, chat_jid, start_ts, end_ts, msg_count, status, "
        f"claimed_at, analyzed_at, run_id, findings_summary "
        f"FROM {_interval_table(storage)}",
        {},
    )


def _fetch_window_messages(storage, since: int, *, limit: int = 2000) -> list:
    return storage.execute_raw(
        f"SELECT id, chat_jid, sender_jid, push_name, text, message_type, ts "
        f"FROM {storage.table_name('messages')} WHERE ts >= :since "
        f"ORDER BY ts ASC LIMIT {int(limit)}",
        {"since": int(since)},
    )


def _apply_seal_decisions(storage, decisions) -> None:
    for row, run in decisions["seal"]:
        storage.execute_write(
            f"UPDATE {_interval_table(storage)} SET status='analyzed', "
            f"analyzed_at=:t, run_id=:rid, findings_summary=:s WHERE id=:id",
            {
                "t": int(run.get("finished_at") or 0),
                "rid": str(run.get("id") or ""),
                "s": _run_result_excerpt(run.get("result")),
                "id": row["id"],
            },
        )
    for row in decisions["release"]:
        storage.execute_write(
            f"DELETE FROM {_interval_table(storage)} WHERE id=:id",
            {"id": row["id"]},
        )


def _claim_intervals(storage, intervals, *, now) -> None:
    for iv in intervals:
        storage.execute_write(
            f"INSERT OR REPLACE INTO {_interval_table(storage)} "
            f"(id, chat_jid, start_ts, end_ts, msg_count, status, claimed_at) "
            f"VALUES (:id, :chat_jid, :start_ts, :end_ts, :msg_count, "
            f"'claimed', :claimed_at)",
            {
                "id": iv["id"],
                "chat_jid": iv["chat_jid"],
                "start_ts": iv["start_ts"],
                "end_ts": iv["end_ts"],
                "msg_count": iv["msg_count"],
                "claimed_at": int(now),
            },
        )


def _adopt_existing_intervals(storage, *, now,
                              gap_seconds=ANALYZER_INTERVAL_GAP_SECONDS,
                              tracking_since: int | None = None) -> int:
    """One-time migration: mark pre-tracking content as already analyzed.

    Content that predates interval tracking was processed by earlier runs;
    re-offering it on the first upgraded run would recreate every old
    finding. **Opt-in only** — see ``_adopt_is_enabled``.

    ``tracking_since`` is the boundary, and it is the whole reason this is
    safe. Without it, "the state table is empty" was read as "nothing has
    ever been tracked", so *whatever happened to be in the window right
    now* got sealed as analyzed — including messages that had just
    arrived. That is silent loss, the same shape as A-1.

    Note the queue only offers intervals that have been quiet for a full
    ``gap_seconds`` (45 min), so test or replayed content is necessarily
    backdated relative to any wall-clock boundary. That is why adoption
    cannot be on by default: it would swallow exactly the material it is
    supposed to leave alone.
    """
    if _load_interval_rows(storage):
        return 0
    if tracking_since is None:
        # No boundary known — refuse to adopt rather than guess. An
        # unbounded adoption can only ever lose data; a skipped one is
        # re-offered to the model, which is recoverable.
        logger.warning(
            "whats2api_bridge: interval adoption skipped — no tracking "
            "boundary recorded; existing content stays available to the "
            "analyzer instead of being sealed silently"
        )
        return 0
    msgs = _fetch_window_messages(storage, 0, limit=5000)
    pre_tracking = [m for m in msgs if _msg_ts(m) < int(tracking_since)]
    sealed, _ = compute_analysis_queue(pre_tracking, [], now=now, gap_seconds=gap_seconds)
    for iv in sealed:
        storage.execute_write(
            f"INSERT OR REPLACE INTO {_interval_table(storage)} "
            "(id, chat_jid, start_ts, end_ts, msg_count, status, analyzed_at, "
            "findings_summary) VALUES (:id, :chat_jid, :start_ts, :end_ts, "
            ":msg_count, 'analyzed', :t, 'adopted: pre-intervalization content')",
            {
                "id": iv["id"],
                "chat_jid": iv["chat_jid"],
                "start_ts": iv["start_ts"],
                "end_ts": iv["end_ts"],
                "msg_count": iv["msg_count"],
                "t": int(now),
            },
        )
    return len(sealed)


def _analyzer_owner() -> str:
    """Best-effort resolution of the current operator owner.

    Extensions are loaded in-process and don't have a request scope at
    register time, so we fall back to the first admin user (matches the
    ``_migrate_assign_legacy_owner`` behavior in ``core/database.py``).
    When the operator is logged in, ``run-now`` is invoked from an admin
    request and we read the real owner out of the request — but at
    scheduler time there is no request at all.

    Getting this right is not cosmetic. ``stream_agent_loop`` calls
    ``blocked_tools_for_owner(owner)``; an owner of ``None`` is treated as
    a *public/non-admin* caller, which (a) denies 55 builtin tools and
    (b) sets ``mcp_mgr = None`` — i.e. every ``mcp__*`` schema, including
    the four Whats2API Bridge tools, is dropped from the model's offered
    surface. The agent then answers "that tool is not exposed in this
    session" and the analyzer produces no findings.

    ``AuthManager`` exposes ``users`` / ``list_users()`` / ``is_admin()``
    but has **no** ``get_admin_user()`` — an earlier version of this
    helper called it, the AttributeError was swallowed, and every task
    row silently ended up with ``owner=NULL``.
    """
    # 1. Real admin from the auth store (same precedence as the core
    #    legacy-owner sweep: explicit ``is_admin``, else first user).
    try:
        from core.auth import AuthManager as _AM
        users = _AM().users or {}
        for uname, udata in users.items():
            if isinstance(udata, dict) and udata.get("is_admin") is True:
                return str(uname)
        if users:
            return str(next(iter(users)))
    except Exception:
        pass
    # 2. Auth-disabled / pre-setup single-user deploys: the admin username
    #    the operator set in the environment. ``owner_is_admin_or_single_user``
    #    already returns True for any owner when auth is off, so this only
    #    keeps the row identity stable across the two modes.
    try:
        import os as _os
        env_user = (_os.getenv("ODYSSEUS_ADMIN_USER") or "").strip()
        if env_user:
            return env_user
    except Exception:
        pass
    return ""


def _request_owner(request) -> str | None:
    """Operator behind an admin request, or ``None`` when unattributable.

    Only an *admin* request user is accepted: a bearer-token caller arrives
    as the sandboxed pseudo-user ``api`` (see ``src.auth_helpers``), and a
    non-admin would flip ``blocked_tools_for_owner`` into the public-user
    policy — which also hides every ``mcp__*`` schema from the analyzer.
    Anything unattributable falls back to :func:`_analyzer_owner`.
    """
    if request is None:
        return None
    try:
        from src.auth_helpers import effective_user as _eu
        user = (_eu(request) or "").strip()
    except Exception:
        return None
    if not user or user == "api":
        return None
    try:
        from core.auth import AuthManager as _AM
        if _AM().is_admin(user):
            return user
    except Exception:
        return None
    return None


def _analyzer_resolve_endpoint(db, settings: dict) -> tuple[str, str]:
    """Pick (endpoint_url, model) for the analyzer task.

    Resolution order (first non-empty wins per field):
      1. Operator overrides — ``settings.endpoint_id`` / ``settings.model``.
      2. The operator's configured "default" endpoint + model — the same
         path Odysseus uses for unscheduled chat. Empty ``model`` falls
         back to the first non-hidden model on the resolved endpoint.
      3. First enabled ``ModelEndpoint`` row (legacy safety net; the
         ``default`` resolver above should hit this on most installs).

    Returns ``("", "")`` if nothing is configured — caller should skip.
    """
    try:
        url, model = "", ""
        endpoint_id = (settings.get("endpoint_id") or "").strip()
        explicit_model = (settings.get("model") or "").strip()

        # Fast path — explicit override for both fields. Skips the
        # ``resolve_endpoint`` chain entirely (faster, and avoids the
        # headers-shape divergence the chain can introduce for in-process
        # callers — the scheduler injects headers on its end).
        if endpoint_id:
            from core.database import ModelEndpoint as _ME
            q = db.query(_ME).filter(
                _ME.id == endpoint_id,
                _ME.is_enabled == True,  # noqa: E712
            )
            me = q.first()
            if me is not None:
                base = (me.base_url or "").rstrip("/")
                if not base.endswith("/v1"):
                    base = base + "/v1"
                url = base
                if explicit_model:
                    model = explicit_model
                else:
                    try:
                        cached = me.cached_models
                        if isinstance(cached, str):
                            cached = json.loads(cached)
                        if isinstance(cached, list) and cached:
                            model = cached[0]
                    except Exception:
                        pass

        # Fall back to the user's configured "default" model when the
        # operator did not pin a specific endpoint.
        if not url or not model:
            owner = _analyzer_owner()
            try:
                from src.endpoint_resolver import resolve_endpoint
                r_url, r_model, _r_headers = resolve_endpoint(
                    setting_prefix="default", owner=owner or None,
                )
                if r_url and r_model:
                    url = url or r_url
                    model = model or r_model
            except Exception:
                pass

        # Last resort — first enabled endpoint, with cached_models[0].
        if not url or not model:
            from core.database import ModelEndpoint as _ME
            me = (
                db.query(_ME)
                .filter(_ME.is_enabled == True)  # noqa: E712
                .order_by(_ME.id.asc())
                .first()
            )
            if me is not None:
                base = (me.base_url or "").rstrip("/")
                if not base.endswith("/v1"):
                    base = base + "/v1"
                url = url or base
                if not model:
                    try:
                        cached = me.cached_models
                        if isinstance(cached, str):
                            cached = json.loads(cached)
                        if isinstance(cached, list) and cached:
                            model = cached[0]
                    except Exception:
                        pass

        return ((url or "").rstrip("/"), (model or ""))
    except Exception:
        return ("", "")


def _analyzer_ensure_crew(db, owner: str | None = None) -> str | None:
    """Create/refresh the CrewMember that enforces the analyzer's tool allowlist.

    Returns the ``crew_member_id`` (or None if the table isn't accessible).
    The crew member has ``enabled_tools`` set to :data:`ANALYZER_ALLOWED_TOOLS`
    so the scheduler inverts it into a ``disabled_tools`` set on every run,
    — hard-blocking every WhatsApp write tool regardless of what the LLM
    tries to call.

    The row is scoped by ``name`` rather than ``id`` because owners in
    multi-user deploys must not share the agent.
    """
    try:
        from core.database import CrewMember as _CM
        import uuid as _uuid
        owner = owner if owner is not None else _analyzer_owner()
        crew = (
            db.query(_CM)
            .filter(_CM.name == ANALYZER_CREW_NAME)
            .filter(_CM.owner == owner if owner else _CM.owner.is_(None))
            .first()
        )
        import json as _json
        tools_json = _json.dumps(list(ANALYZER_ALLOWED_TOOLS))
        if crew is None:
            crew = _CM(
                id=str(_uuid.uuid4()),
                name=ANALYZER_CREW_NAME,
                owner=owner or None,
                personality=DEFAULT_ANALYZER_PROMPT,
                enabled_tools=tools_json,
                is_default_assistant=False,
            )
            db.add(crew)
            db.commit()
            db.refresh(crew)
        else:
            # Refresh in case the allowed list grew.
            if crew.enabled_tools != tools_json:
                crew.enabled_tools = tools_json
                db.commit()
        return str(crew.id) if crew else None
    except Exception as e:
        logger.warning("whats2api_bridge: could not ensure analyzer crew: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
        return None


# Statuses that must never be picked up as "the" analyzer task again.
_ANALYZER_DEAD_STATUSES = ("completed", "cancelled", "failed")


def _analyzer_find_task(db, owner: str | None = None) -> "object | None":
    """Locate the analyzer's ScheduledTask row (any status).

    Prefers the live row (``active``/``paused``) over retired duplicates so a
    leftover ``completed`` row from a previous owner cannot shadow the one the
    scheduler actually runs.
    """
    try:
        from core.database import ScheduledTask as _ST
        owner = owner if owner is not None else _analyzer_owner()
        rows = (
            db.query(_ST)
            .filter(_ST.name == ANALYZER_TASK_NAME)
            .filter(
                _ST.owner == owner if owner else _ST.owner.is_(None)
            )
            .all()
        )
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None
    if not rows:
        return None
    live = [r for r in rows if (r.status or "") not in _ANALYZER_DEAD_STATUSES]
    pool = live or list(rows)
    # Oldest row wins so the task id (and its TaskRun history) stays stable.
    pool.sort(key=lambda r: str(getattr(r, "id", "") or ""))
    return pool[0]


def _analyzer_dedupe_tasks(db, owner: str | None = None) -> int:
    """Retire duplicate analyzer tasks left behind by the ``owner=NULL`` bug.

    Older builds resolved the operator owner through a nonexistent
    ``AuthManager.get_admin_user()``, so every reconcile minted a fresh
    ``owner=NULL`` task. The core boot sweep (``_migrate_assign_legacy_owner``)
    later reassigns those rows to the admin user, which leaves *several*
    ``active`` tasks with the same name — all of them firing on the same cron
    and burning one LLM run each. Keep exactly one live row per owner.

    Returns the number of rows retired.
    """
    try:
        from core.database import ScheduledTask as _ST
        owner = owner if owner is not None else _analyzer_owner()
        rows = (
            db.query(_ST)
            .filter(_ST.name == ANALYZER_TASK_NAME)
            .all()
        )
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    retired = 0
    if owner:
        # Unattributed rows are always our own leftovers: adopt the first one
        # when the owner has no task yet, otherwise retire it.
        orphans = [r for r in rows if not (r.owner or "")]
        owned = [r for r in rows if (r.owner or "") == owner]
        if owned:
            for row in orphans:
                row.status = "completed"
                retired += 1
        elif orphans:
            orphans.sort(key=lambda r: str(getattr(r, "id", "") or ""))
            for row in orphans[1:]:
                row.status = "completed"
                retired += 1
            orphans[0].owner = owner
            rows = [r for r in rows if r is not orphans[0]]
    live = [
        r for r in rows
        if (r.status or "") not in _ANALYZER_DEAD_STATUSES
        and (r.owner or "") == (owner or "")
    ]
    if len(live) > 1:
        live.sort(key=lambda r: str(getattr(r, "id", "") or ""))
        for row in live[1:]:
            row.status = "completed"
            retired += 1
    if retired:
        try:
            db.commit()
            logger.info(
                "whats2api_bridge: retired %d duplicate analyzer task row(s) "
                "for owner=%r", retired, owner or None,
            )
        except Exception as e:
            logger.warning("whats2api_bridge: task dedupe commit failed: %s", e)
            try:
                db.rollback()
            except Exception:
                pass
            return 0
    return retired


def _analyzer_set_all_tasks_status(db, status: str) -> int:
    """Force every analyzer task row (any owner) into ``status``.

    ``on_disable`` / ``on_uninstall`` must not depend on owner resolution:
    a stray ``owner=NULL`` row left by an older build would keep firing on
    the cron after the operator turned the extension off.
    """
    try:
        from core.database import ScheduledTask as _ST
        rows = (
            db.query(_ST)
            .filter(_ST.name == ANALYZER_TASK_NAME)
            .all()
        )
        changed = 0
        for row in rows:
            if (row.status or "") != status:
                row.status = status
                changed += 1
        if changed:
            db.commit()
        return changed
    except Exception as e:
        logger.warning(
            "whats2api_bridge: could not set analyzer tasks to %r: %s",
            status, e,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _analyzer_cron_for(interval_minutes: int) -> str:
    """Build a cron expression that fires every ``interval_minutes`` minutes.

    Cron has no ``*/N`` shorthand for N>59; for those we spread across the
    hour so the cron stays valid (e.g. every 90 min → at :00 and :30 every
    other hour). For intervals >= 60 we'll use ``M * * * *`` plus a
    ``*/N`` on the minute to keep it always-firing without alignment drift.
    """
    n = max(5, min(int(interval_minutes), 720))
    if n <= 59:
        return f"*/{n} * * * *"
    # 60..720 → pick a divisor d that fits 1..59 and use a coarser schedule.
    # Strategy: every n minutes from min 0 with N>=60 collapses into
    # an hour-aligned interval. We split into min step <=59 and an hour step.
    for step in (5, 6, 10, 15, 20, 30):
        if n % step == 0:
            hours = n // step
            if hours <= 23:
                return f"*/{step} */{hours} * * *"
    # Fallback: align to /30 minutes plus every 2h.
    return "*/30 */2 * * *"


def _analyzer_reconcile(host, db, owner: str | None = None) -> dict:
    """Apply current settings to the ScheduledTask row.

    - enabled + cron match → row active (no-op if already active).
    - disabled → row paused (preserve config; don't drop it).
    - missing row + enabled → create the row + ensure crew.

    Authority handling now lives entirely in core:
    ``restore_task_authority`` falls back to ``create_request_authority(prompt)``
    when the OXS-created task has no sealed ``request_authority_json``,
    so the agent loop gets intent-derived grants at run time without the
    extension needing to mint JSON.
    """
    s = _analyzer_settings(host)
    owner = owner if owner is not None else _analyzer_owner()
    # Retire the duplicate ``owner=NULL`` rows older builds used to mint.
    # Must run before the lookup, otherwise a leftover row shadows the one
    # the scheduler is supposed to run.
    try:
        _analyzer_dedupe_tasks(db, owner)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("whats2api_bridge: analyzer dedupe skipped: %s", exc)
    task = _analyzer_find_task(db, owner)
    if not s["enabled"]:
        if task and task.status != "paused":
            task.status = "paused"
            db.commit()
        return {
            "ok": True,
            "applied": "paused",
            "task_id": str(task.id) if task else None,
            "enabled": False,
        }

    endpoint_url, model = _analyzer_resolve_endpoint(db, s)
    if not endpoint_url or not model:
        return {
            "ok": False,
            "error": (
                "No model endpoint is configured (or the chosen "
                "endpoint_id is unavailable). Set one in Settings → "
                "Models, or pick a different analyzer_endpoint_id."
            ),
            "enabled": True,
        }

    cron = _analyzer_cron_for(s["interval_minutes"])
    # Substitute the documented non-live placeholders. We always do this
    # even when the resolved prompt is the DEFAULT_ANALYZER_PROMPT —
    # otherwise a downstream literal ``{min_messages}`` would reach
    # the LLM and confuse it.
    #
    # ``{since_ts}`` is deliberately left in the stored prompt. The task
    # scheduler expands it on every execution (see
    # ``src/task_scheduler._expand_live_prompt_placeholders``), so the
    # lookback window stays fresh across cron ticks instead of freezing
    # the cutoff at reconcile time.
    prompt = s["prompt"].replace(
        "{lookback_hours}",  str(s["lookback_hours"])
    ).replace(
        "{min_messages}",    str(s["min_messages"])
    ).replace(
        "{interval_minutes}", str(s["interval_minutes"])
    )
    # Stamp the live {since_ts} token with the configured window so the
    # scheduler's fire-time expansion honors lookback_hours instead of
    # assuming 24h. The token stays live (unexpanded) on purpose.
    prompt = re.sub(
        r"\{since_ts\}", "{since_ts:%d}" % s["lookback_hours"], prompt
    )
    crew_id = _analyzer_ensure_crew(db, owner)

    if task is None:
        from core.database import ScheduledTask as _ST
        import uuid as _uuid
        task = _ST(
            id=str(_uuid.uuid4()),
            owner=owner or None,
            name=ANALYZER_TASK_NAME,
            prompt=prompt,
            task_type="llm",
            action=None,
            schedule="cron",
            cron_expression=cron,
            trigger_type="schedule",
            status="active",
            output_target="session",
            endpoint_url=endpoint_url,
            model=model,
            crew_member_id=crew_id,
            notifications_enabled=True,
        )
        db.add(task)
        db.commit()
        action = "created"
    else:
        changed = False
        if task.status != "active":
            task.status = "active"
            changed = True
        if (task.cron_expression or "") != cron:
            task.cron_expression = cron
            changed = True
        if (task.prompt or "") != prompt:
            task.prompt = prompt
            changed = True
        if (task.endpoint_url or "") != endpoint_url:
            task.endpoint_url = endpoint_url
            changed = True
        if (task.model or "") != model:
            task.model = model
            changed = True
        if (task.crew_member_id or "") != (crew_id or ""):
            task.crew_member_id = crew_id
            changed = True
        if changed:
            db.commit()
        action = "updated" if changed else "noop"

    # Best-effort: nudge the scheduler so the new cron takes effect
    # within one tick instead of waiting up to 10s. The scheduler is
    # registered globally by app.py via ``src.event_bus.set_task_scheduler``
    # — import from there, not from ``src.task_scheduler`` (which only
    # exposes ``TaskScheduler`` itself, no registry).
    try:
        from src.event_bus import get_task_scheduler as _get_sched
        sched_obj = _get_sched()
        if sched_obj is not None and hasattr(sched_obj, "_loop"):
            # No public API to force a wake; the scheduler polls every
            # 1–60s, so a single tick is the worst-case lag anyway.
            pass
    except Exception:
        sched_obj = None
    return {
        "ok": True,
        "applied": action,
        "task_id": str(task.id),
        "enabled": True,
        "endpoint_url": endpoint_url,
        "model": model,
        "cron": cron,
        "interval_minutes": s["interval_minutes"],
        "lookback_hours": s["lookback_hours"],
        "min_messages": s["min_messages"],
    }


def _analyzer_status(host, db, owner: str | None = None) -> dict:
    """Status snapshot consumed by the UI admin endpoint."""
    s = _analyzer_settings(host)
    task = _analyzer_find_task(db, owner)
    out = {
        "ok": True,
        "settings": s,
        "task": None,
        "last_run": None,
        "next_run": None,
        "run_count": 0,
        "default_prompt": DEFAULT_ANALYZER_PROMPT,
        "placeholders": ["lookback_hours", "min_messages", "interval_minutes"],
    }
    if task is not None:
        out["task"] = {
            "id": str(task.id),
            "status": task.status,
            "cron": task.cron_expression,
            "endpoint_url": task.endpoint_url,
            "model": task.model,
            "session_id": getattr(task, "session_id", None),
            "schedule": task.schedule,
        }
        out["last_run"] = (
            task.last_run.isoformat() if getattr(task, "last_run", None) else None
        )
        out["next_run"] = (
            task.next_run.isoformat() if getattr(task, "next_run", None) else None
        )
        out["run_count"] = int(getattr(task, "run_count", 0) or 0)
    out["resolved"] = _analyzer_resolve_endpoint(db, s)
    return out


def _analyzer_list_runs(host, db, *, limit: int = 25, owner: str | None = None) -> dict:
    """Return recent ``TaskRun`` rows for this analyzer's task.

    Each row carries the LLM's final assistant message preview (the
    ``result`` column), tokens_used, model, timing, status — plus the
    ``session_id`` that ties the run to the long-lived chat session
    where the full conversation + tool calls land (look there for the
    ground truth).
    """
    try:
        from core.database import ScheduledTask, TaskRun
        task = _analyzer_find_task(db, owner)
        if task is None:
            return {
                "ok": True,
                "rows": [],
                "task_id": None,
                "session_id": None,
                "note": "no ScheduledTask row yet (reconcile first)",
            }
        q = (
            db.query(TaskRun)
            .filter(TaskRun.task_id == task.id)
            .order_by(TaskRun.started_at.desc())
        )
        try:
            limit_i = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit_i = 25
        runs = q.limit(limit_i).all()

        rows = []
        for r in runs:
            started = getattr(r, "started_at", None)
            finished = getattr(r, "finished_at", None)
            duration = None
            try:
                if started and finished:
                    duration = max(0, int((finished - started).total_seconds()))
            except Exception:
                pass
            result = getattr(r, "result", None) or ""
            result_preview = result[:320]
            result_more = len(result) > 320
            rows.append({
                "id": str(r.id),
                "started_at": started.isoformat() if started else None,
                "finished_at": finished.isoformat() if finished else None,
                "duration_seconds": duration,
                "status": r.status,
                "model": getattr(r, "model", None) or (task.model if task else None),
                "tokens_used": getattr(r, "tokens_used", None),
                "result_preview": result_preview,
                "result_truncated": result_more,
                "result_length": len(result),
                "error": (r.error or None) if r.status == "error" else None,
            })

        return {
            "ok": True,
            "rows": rows,
            "task_id": str(task.id),
            "session_id": getattr(task, "session_id", None),
            "session_name": task.name,
        }
    except Exception as e:
        logger.warning("whats2api_bridge: list_runs failed: %s", e)
        return {"ok": False, "error": str(e), "rows": []}


def _analyzer_get_run(host, db, run_id: str, owner: str | None = None) -> dict:
    """Single-run detail: full result (truncated for safety), timing, status."""
    try:
        from core.database import ScheduledTask, TaskRun
        task = _analyzer_find_task(db, owner)
        if task is None:
            return {"ok": False, "error": "no ScheduledTask row"}
        run = (
            db.query(TaskRun)
            .filter(TaskRun.id == run_id, TaskRun.task_id == task.id)
            .first()
        )
        if run is None:
            return {"ok": False, "error": "run not found", "run_id": run_id}
        started = run.started_at
        finished = run.finished_at
        duration = None
        try:
            if started and finished:
                duration = max(0, int((finished - started).total_seconds()))
        except Exception:
            pass
        result = getattr(run, "result", None) or ""
        return {
            "ok": True,
            "run": {
                "id": str(run.id),
                "task_id": str(run.task_id),
                "started_at": started.isoformat() if started else None,
                "finished_at": finished.isoformat() if finished else None,
                "duration_seconds": duration,
                "status": run.status,
                "model": getattr(run, "model", None) or (task.model if task else None),
                "tokens_used": getattr(run, "tokens_used", None),
                "result": result[:8000],
                "result_truncated": len(result) > 8000,
                "result_length": len(result),
                "error": run.error,
            },
            "task": {
                "id": str(task.id),
                "name": task.name,
                "session_id": getattr(task, "session_id", None),
            },
        }
    except Exception as e:
        logger.warning("whats2api_bridge: get_run failed: %s", e)
        return {"ok": False, "error": str(e)}


async def _analyzer_run_now(host, db, owner: str | None = None) -> dict:
    """Trigger an immediate execution of the analyzer's ScheduledTask.

    ``run_task_now`` is now synchronous (returns ``TaskRun.id | None``
    directly) — no ``await``, no coroutine unwrap. The async signature
    is preserved on this function so the route handler stays unchanged.

    ``owner`` is the request-scope operator when called from the admin
    route; the scheduled path passes nothing and falls back to
    :func:`_analyzer_owner`.
    """
    s = _analyzer_settings(host)
    if not s["enabled"]:
        return {"ok": False, "error": "analyzer is disabled — toggle it on first"}
    rec = _analyzer_reconcile(host, db, owner)
    if not rec.get("ok"):
        return rec
    task_id = rec.get("task_id")
    if not task_id:
        return {"ok": False, "error": "no task row to run"}
    try:
        from src.event_bus import get_task_scheduler as _get
        sched = _get()
        if sched is None:
            return {
                "ok": False,
                "task_id": task_id,
                "error": "scheduler is not initialised in this process",
            }
        run_id = sched.run_task_now(task_id, force=True)
        return {
            "ok": run_id is not None,
            "task_id": task_id,
            "run_id": run_id,
            "owner": owner or _analyzer_owner() or None,
            "triggered_at": int(time.time()),
            "error": None if run_id else "scheduler refused the trigger",
        }
    except Exception as e:
        logger.warning("whats2api_bridge: run_task_now failed: %s", e)
        return {"ok": False, "error": f"scheduler rejected run: {e}", "task_id": task_id}


def _admin_analyzer_get(host, storage, owner: str | None = None, **_kwargs) -> dict:
    SessionLocal, _ = _mcp_models()  # noqa: F841 — same DB access helper
    from core.database import SessionLocal as _SL
    db = _SL()
    try:
        return _analyzer_status(host, db, owner)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _admin_analyzer_list_runs(host, storage, *, limit: int = 25, owner: str | None = None) -> dict:
    from core.database import SessionLocal as _SL
    db = _SL()
    try:
        return _analyzer_list_runs(host, db, limit=limit, owner=owner)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _admin_analyzer_get_run(host, storage, run_id: str, owner: str | None = None) -> dict:
    from core.database import SessionLocal as _SL
    db = _SL()
    try:
        return _analyzer_get_run(host, db, run_id, owner)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _admin_analyzer_patch(host, storage, body: dict, owner: str | None = None) -> dict:
    """Update analyzer settings + reconcile into the ScheduledTask row.

    Authority mint is no longer needed: ``restore_task_authority`` in
    core now derives grants from the prompt intent when the OXS-created
    ScheduledTask has no sealed ``request_authority_json``.
    """
    ns = host.get_settings()
    changed = False
    if "enabled" in body:
        ns.set("analyzer_enabled", bool(body["enabled"]))
        changed = True
    if "interval_minutes" in body:
        ns.set("analyzer_interval_minutes", _coerce_setting_int(body["interval_minutes"], 30, 5, 720))
        changed = True
    if "lookback_hours" in body:
        ns.set("analyzer_lookback_hours", _coerce_setting_int(body["lookback_hours"], 24, 1, 168))
        changed = True
    if "min_messages" in body:
        ns.set("analyzer_min_messages", _coerce_setting_int(body["min_messages"], 3, 1, 50))
        changed = True
    if "endpoint_id" in body:
        ns.set("analyzer_endpoint_id", str(body["endpoint_id"] or "").strip())
        changed = True
    if "model" in body:
        ns.set("analyzer_model", str(body["model"] or "").strip())
        changed = True
    if "prompt" in body:
        prompt = str(body["prompt"] or "").strip()
        if len(prompt) > 8192:
            raise ValueError("prompt too long (max 8192 chars)")
        ns.set("analyzer_prompt", prompt)
        changed = True
    if not changed:
        return {"ok": True, "applied": "noop"}

    from core.database import SessionLocal as _SL
    db = _SL()
    try:
        rec = _analyzer_reconcile(host, db, owner)
    finally:
        try:
            db.close()
        except Exception:
            pass
    return rec


async def _admin_analyzer_run_now(host, request: Request) -> dict:
    """Trigger an immediate analyzer run.

    Lives at module level so it can be unit-tested without the OXS
    request layer; the route wrapper (``_post_analyzer_run_now``)
    forwards ``host`` from its closure scope. Owner resolution is the
    only thing we have to do at request scope (vs the scheduled path
    which has no request at all).

    ``AuthManager`` has no ``get_current_user()`` — the request-scope
    user comes from ``src.auth_helpers.effective_user`` (set by the auth
    middleware) and is only trusted when it is an admin.
    """
    owner = _request_owner(request)
    from core.database import SessionLocal as _SL
    db = _SL()
    try:
        return await _analyzer_run_now(host, db, owner)
    finally:
        try:
            db.close()
        except Exception:
            pass


# ── register(host) ─────────────────────────────────────────────────────────


def register(host):
    """OXS entry point."""

    logger = host.get_logger()

    # Build storage eagerly so on_enable has it ready.
    storage = PluginStorage(EXTENSION_ID, engine=engine)

    # Snapshot of bool-ish settings used inside the webhook hot path.
    # We refresh it on every enable.
    try:
        _settings_value_cache["log_inbound_events"] = bool(
            _s(host, "log_inbound_events", True)
        )
    except Exception:
        _settings_value_cache["log_inbound_events"] = True

    @host.on_install
    def _install():
        # Seed defaults into the settings namespace so the admin form
        # has somewhere to write into. Always idempotent — never
        # overwrites a value the user has actually typed.
        _ensure_settings(host)
        logger.info("whats2api_bridge installed")

    @host.on_enable
    def _enable():
        # ── Defensive: re-apply defaults on enable so pre-existing
        # installs whose ``on_install`` predated the defaults (or whose
        # settings were cleared manually) still surface the bridge's
        # base URL and other sane defaults to the UI / settings form.
        _ensure_settings(host)

        # ── Apply data_schema ourselves on the live DB engine.
        # The registry's pre-enable migration passes neither ``engine``
        # nor ``database_url`` to ``apply_for_extension``, which makes
        # PluginStorage fall back to ``sqlite:///:memory:`` and the
        # tables vanish into a private instance. Doing it here keeps
        # the tables on the same engine the rest of Odysseus uses.
        try:
            storage.apply_data_schema({
                "connections": {
                    "columns": {
                        "id":         "TEXT PRIMARY KEY",
                        "name":       "TEXT",
                        "engine":     "TEXT",
                        "phone":      "TEXT",
                        "status":     "TEXT",
                        "owner_id":   "TEXT",
                        "raw_json":   "TEXT",
                        "updated_at": "INTEGER NOT NULL",
                    },
                    "indexes": [["status"], ["owner_id"]],
                },
                "messages": {
                    "columns": {
                        "id":            "TEXT PRIMARY KEY",
                        "connection_id": "TEXT NOT NULL",
                        "direction":     "TEXT NOT NULL",
                        "remote_jid":    "TEXT",
                        "from_me":       "TEXT",
                        "chat_jid":      "TEXT",
                        "sender_jid":    "TEXT",
                        "push_name":     "TEXT",
                        "text":          "TEXT",
                        "message_type":  "TEXT",
                        "ts":            "INTEGER NOT NULL",
                        "raw_json":      "TEXT",
                    },
                    "indexes": [["connection_id", "ts"], ["chat_jid", "ts"], ["direction"]],
                    "unique":  [["id"]],
                },
                "events": {
                    "columns": {
                        "id":            "INTEGER PRIMARY KEY AUTOINCREMENT",
                        "event_name":    "TEXT NOT NULL",
                        "connection_id": "TEXT",
                        "user_id":       "TEXT",
                        "received_at":   "INTEGER NOT NULL",
                        "raw_json":      "TEXT",
                    },
                    "indexes": [["event_name", "received_at"], ["connection_id", "received_at"]],
                },
                "intervals": {
                    "columns": {
                        "id":               "TEXT PRIMARY KEY",
                        "chat_jid":         "TEXT NOT NULL",
                        "start_ts":         "INTEGER NOT NULL",
                        "end_ts":           "INTEGER NOT NULL",
                        "msg_count":        "INTEGER NOT NULL",
                        "status":           "TEXT NOT NULL",
                        "claimed_at":       "INTEGER",
                        "analyzed_at":      "INTEGER",
                        "run_id":           "TEXT",
                        "findings_summary": "TEXT",
                    },
                    "indexes": [["status"], ["chat_jid", "start_ts"]],
                },
            })
        except Exception:
            logger.exception("whats2api_bridge: data_schema apply failed (continuing)")

        # One-time adoption: content that predates interval tracking must
        # not be re-offered to the analyzer (it would recreate every old
        # finding on the first upgraded run).
        try:
            _adopt_existing_intervals(storage, now=int(time.time()))
        except Exception:
            logger.exception("whats2api_bridge: interval adoption failed")

        # ── UI ────────────────────────────────────────────────────────
        # Cache-bust the iframe URL on every enable so the browser
        # *always* re-fetches the static HTML after a Reload/Uninstall
        # cycle (some browsers cling to the cached version otherwise).
        cache_bust = int(time.time())
        host.add_static("/ext-static/whats2api_bridge", "ui")
        host.add_panel(
            id="whats2api_bridge",
            title="Whats2API Bridge",
            source_url=f"/ext-static/whats2api_bridge/index.html?v={cache_bust}",
        )

        # ── Event subscription: log rescan ────────────────────────────
        host.subscribe_event("oxs.rescan", _on_rescan)

        # ── MCP tools ─────────────────────────────────────────────────
        # Each tool ships with a ``description_rich`` so the tool_index
        # RAG pass returns them when the prompt mentions WhatsApp
        # messages, conversations, or recent activity — without it the
        # framework would fall back to ``"[<ext_id>] <name>"`` which
        # the semantic retriever can't match.
        #
        # ``read_only=True`` declares every one of these to the capability
        # / approval gate as a brokered private read. All four are pure
        # local SELECTs over the extension's own tables (no outbound
        # whats2api call, no mutation). Without the declaration the gate
        # classifies them as unknown and refuses the call as soon as any
        # untrusted context is in the transcript — which the tool catalog
        # injection itself guarantees — so a scheduled analyzer task could
        # never actually read a message.
        for tool_name, schema, handler, description_rich in _read_tools(host, storage):
            host.register_mcp_tool(
                TOOL_NS, tool_name, schema, handler,
                description_rich=description_rich,
                read_only=True,
            )

        # Transport tools: the outbound half of the bridge. Registered
        # ``read_only=False`` on purpose — they change WhatsApp state, so the
        # capability/approval gate must treat them as unclassified rather
        # than let a transcript full of untrusted inbound messages talk them
        # through a post-external-context approval a scheduled run can't give.
        #
        # Each is wrapped in ``_refuse_if_analyzer`` because the crew
        # allowlist cannot do it: the scheduler inverts ``enabled_tools``
        # over ``BUILTIN_TOOL_DESCRIPTIONS`` only, which contains no
        # ``mcp__*`` names, so an allowlisted-MCP force-include exists with
        # no matching deny. The analyzer stays read-only over WhatsApp
        # because the handler checks, not because the prompt asks nicely.
        for tool_name, schema, handler, description_rich in _transport_tools(host, storage):
            _base_handler = handler

            async def _guarded(args, _h=_base_handler, _name=tool_name):
                refusal = _refuse_if_analyzer(_name)
                if refusal is not None:
                    return refusal
                return await _h(args)

            host.register_mcp_tool(
                TOOL_NS, tool_name, schema, _guarded,
                description_rich=description_rich,
                read_only=False,
            )

        # ── Inbound webhook for whats2api events ──────────────────────
        # The framework's ``register_webhook`` always generates a fresh
        # 32-byte token on every enable. For an inbound webhook that
        # ``whats2api.com`` has configured with a fixed URL, that means a
        # container restart would silently rotate the credential out from
        # under the operator and start returning 404s — exactly the bug we
        # shipped and forgot. Persist the token in the extension's data
        # directory (which is on the bind mount, so it survives restart)
        # and rewrite the record's ``token_hash`` so the dispatcher keeps
        # accepting the original token across enable cycles.
        host.register_webhook(
            path_suffix="/whats2api/events",
            event_filter=[],
            handler=_build_webhook_handler(host, storage),
        )
        # The framework's ``_wire`` step (which actually installs the
        # webhook into ``_webhooks[ext_id]`` and assigns the random
        # token) runs AFTER ``on_enable`` returns, on the same call
        # stack. Schedule the pinner for the next event-loop tick so it
        # sees the installed record.
        def _pin_now() -> None:
            try:
                pinned = _pin_webhook_token(host)
                if pinned:
                    logger.info(
                        "whats2api_bridge inbound webhook token pinned to disk"
                    )
            except Exception:
                logger.exception("whats2api_bridge: webhook token pin failed")

        # ``_wire`` installs the webhook record on the same call stack
        # AFTER ``on_enable`` returns, so the pin must run a tick later —
        # never inline here (the previous inline fallback raced the
        # install and silently left a freshly-rotated token in place,
        # breaking the operator's whats2api.com URL on every restart).
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, _pin_now)
        except RuntimeError:
            threading.Thread(target=_pin_now, daemon=True).start()

        # ── Public status route (no session auth) ────────────────────
        host.add_route(
            "/ext-api/whats2api_bridge/status",
            _make_status_route(host, storage),
            auth="public",
        )

        # ── Admin routes (config + data browser) — auth-gated by the
        #    route_mount layer (``auth="admin"``) so the iframe can call
        #    them with the same session cookie the operator is already
        #    using on the parent SPA.
        for path, method_map in _make_admin_handlers(host, storage).items():
            for method, handler in method_map.items():
                host.add_route(
                    path,
                    handler,
                    auth="admin",
                    methods=(method,),
                )

        # ── Fire a hello event so subscribers see we came up ──────────
        host.fire_event(
            "whats2api.bridge.enabled",
            {"connection_id": None, "info": "whats2api_bridge online"},
            owner=None,
        )

        # ── Conversation Analyzer: reconcile the ScheduledTask row ────
        # Best-effort. If Settings → Models has nothing configured yet
        # the operator will see a clear error in the next UI status
        # call. The DB session is short-lived because the scheduler
        # might be far away from this hook's call site.
        try:
            from core.database import SessionLocal as _SL_enable
            db_e = _SL_enable()
            try:
                rec = _analyzer_reconcile(host, db_e)
                logger.info("whats2api_bridge analyzer reconcile: %s", rec.get("applied"))
            finally:
                try:
                    db_e.close()
                except Exception:
                    pass
        except Exception as e:
            logger.warning("whats2api_bridge: analyzer reconcile skipped: %s", e)

        logger.info("whats2api_bridge enabled")

    @host.on_disable
    def _disable():
        # Pause the analyzer's ScheduledTask (preserve config; do not
        # drop the row so the operator can re-enable with one click).
        try:
            from core.database import SessionLocal as _SL_disable
            db_d = _SL_disable()
            try:
                _analyzer_set_all_tasks_status(db_d, "paused")
            finally:
                try:
                    db_d.close()
                except Exception:
                    pass
        except Exception as e:
            logger.warning("whats2api_bridge: analyzer pause on disable failed: %s", e)
        logger.info("whats2api_bridge disabled")

    @host.on_uninstall
    def _uninstall():
        # Mark the analyzer's ScheduledTask completed so the scheduler
        # stops picking it up. We do not delete the CrewMember so a
        # reinstall with the same owner keeps the operator's voice.
        try:
            from core.database import SessionLocal as _SL_uninst
            db_u = _SL_uninst()
            try:
                _analyzer_set_all_tasks_status(db_u, "completed")
            finally:
                try:
                    db_u.close()
                except Exception:
                    pass
        except Exception as e:
            logger.warning("whats2api_bridge: analyzer uninstall cleanup failed: %s", e)
        logger.info("whats2api_bridge uninstalled")

    def _on_rescan(payload):
        logger.info("oxs.rescan observed by whats2api_bridge: %s", payload)