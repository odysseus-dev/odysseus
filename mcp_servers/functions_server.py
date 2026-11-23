#!/usr/bin/env python3
"""odysseus-functions — unified MCP bridge (stdio or HTTP) for an Odysseus instance.

Exposes the instance's REAL REST surface as 1:1 deterministic MCP tools:
chat with the full agent, plus native passthroughs for email (list/read/
draft/send), todos, memory, calendar, documents and file uploads. No LLM
mediation anywhere outside odysseus_chat.

Lane map (chosen from upstream auth semantics, verified 2026-11-09):
  - GET /api/tools, /api/search, POST /api/chat, /api/session — attribute
    bearer callers to the token owner (effective_user).
  - POST /api/upload — same owner attribution.
  - /api/codex/* — the scope-aware wrappers: they enforce ApiToken scopes
    AND re-invoke the real handlers with owner attribution. This is the
    ONLY correct bearer lane for email/memory/calendar/todos/documents:
    the raw /api/email/* etc. routes resolve bearer callers to the
    sandboxed pseudo-user "api" (isolated, empty data).

Deliberately NOT exposed (stay behind odysseus_chat):
  - agent/sandbox tools (bash, apply_patch, ask_user, ...) — no REST.
  - raw /api/email write/mutate routes (archive/move/mark/delete/scheduled/
    pending/unsubscribe) — no scope-aware wrapper exists, bearer lane is
    pseudo-user isolated upstream.
  - LLM-mediated helpers (/ai-reply, /summarize, /translate, quick-parse,
    memory /extract) — non-deterministic by design.
  - cookbook (host topology/model serving), contacts CRUD (admin-session
    only), account/config management, admin surfaces.

Config via environment:
  ODYSSEUS_BASE_URL     default http://127.0.0.1:7001
  ODYSSEUS_TOKEN        REQUIRED — ody_* API token. Native tools need the
                        codex scopes (see README); a chat-only token keeps
                        odysseus_chat/search/tools/upload working and makes
                        the scoped tools fail with the upstream 403 naming
                        the missing scope.
  ODYSSEUS_ENDPOINT_ID  endpoint id used when auto-creating a session
                        (no default; falls back to the instance's default endpoint)
  ODYSSEUS_MODEL        used when auto-creating a session (default qwen3.5:9b)
  ODYSSEUS_CHAT_TIMEOUT seconds for a chat roundtrip (default 300)

Transport:
  ODYSSEUS_MCP_TRANSPORT  "stdio" (default) or "http"
  ODYSSEUS_MCP_HOST       http bind host (default 127.0.0.1 — loopback only;
                          containers set 0.0.0.0 and publish on host loopback)
  ODYSSEUS_MCP_PORT       http bind port (default 3014)
  ODYSSEUS_MCP_TOKEN      REQUIRED in http mode — static gateway bearer token,
                          constant-time checked on EVERY request (MCP path and
                          /health alike), brain-mcp style. In stdio mode the
                          transport is a private pipe, no gateway token needed.

Session semantics: `session_id` is the human-friendly session NAME as shown in
the Odysseus UI. The API requires the session UUID, so the bridge resolves
name -> UUID via GET /api/sessions and auto-creates missing sessions (POST
/api/session, form-encoded) with the endpoint/model above. Sessions created
here are the owner's real sessions — they appear in the desktop UI too, because
API tokens act as their owner (effective_user).
"""
import base64
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

__version__ = "1.3.0"

BASE_URL = os.environ.get("ODYSSEUS_BASE_URL", "http://127.0.0.1:7001").rstrip("/")
TOKEN = os.environ.get("ODYSSEUS_TOKEN", "")
ENDPOINT_ID = os.environ.get("ODYSSEUS_ENDPOINT_ID", "")
MODEL = os.environ.get("ODYSSEUS_MODEL", "qwen3.5:9b")
CHAT_TIMEOUT = int(os.environ.get("ODYSSEUS_CHAT_TIMEOUT", "300"))

TRANSPORT = os.environ.get("ODYSSEUS_MCP_TRANSPORT", "stdio").lower()
GW_HOST = os.environ.get("ODYSSEUS_MCP_HOST", "127.0.0.1")
GW_PORT = int(os.environ.get("ODYSSEUS_MCP_PORT", "3014"))
GW_TOKEN = os.environ.get("ODYSSEUS_MCP_TOKEN", "")

mcp = FastMCP("odysseus-functions")

_session_ids: dict[str, str] = {}  # name -> UUID, filled lazily


def _api(method: str, path: str, payload: dict | None = None,
         form: dict | None = None, multipart: tuple[str, bytes] | None = None,
         timeout: int = 30):
    """One HTTP call to the Odysseus API; returns decoded JSON.

    `multipart` = (content_type, body) for pre-built multipart/form-data.
    Raises RuntimeError with the response body on HTTP errors so tool callers
    see the API's own error message instead of a bare status code.
    """
    headers = {"Authorization": f"Bearer {TOKEN}"}
    # Cloudflare 403/1010s the default python-urllib User-Agent on proxied
    # hosts; always announce ourselves (callers may still override).
    headers.setdefault("User-Agent", f"odysseus-mcp-bridge/{__version__}")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif multipart is not None:
        ctype, data = multipart
        headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {body}") from None


def _qs(params: dict) -> str:
    """Query string from non-empty values (bools as true/false)."""
    clean = {}
    for k, v in params.items():
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            v = "true" if v else "false"
        clean[k] = v
    return urllib.parse.urlencode(clean)


def _json_body(params: dict) -> dict:
    """JSON body from non-empty values (bools kept as bools, None dropped)."""
    return {k: v for k, v in params.items() if v not in (None, "", [])}


def _resolve_session(name: str) -> str:
    """Session NAME -> UUID, auto-creating the session on first use."""
    if name in _session_ids:
        return _session_ids[name]
    sessions = _api("GET", "/api/sessions")
    if isinstance(sessions, dict):  # tolerate a wrapped response
        sessions = sessions.get("sessions", [])
    for s in sessions:
        if s.get("name") == name:
            _session_ids[name] = s["id"]
            return s["id"]
    form = {"name": name, "model": MODEL}
    if ENDPOINT_ID:
        form["endpoint_id"] = ENDPOINT_ID
    created = _api("POST", "/api/session", form=form)
    _session_ids[name] = created["id"]
    return created["id"]


# ── Agent lane (unchanged since 1.0) ──────────────────────────────────────

@mcp.tool
def odysseus_chat(message: str, session_id: str = "mcp-bridge") -> dict:
    """Talk to the full Odysseus agent (tools, memory, the lot).

    Returns dict with the agent reply under "response" plus the model /
    endpoint actually used. Each session_id keeps its own history, visible in
    the Odysseus UI. A chat roundtrip can take tens of seconds. This is the
    ONLY LLM-mediated tool: everything else in this server is a deterministic
    passthrough.
    """
    sid = _resolve_session(session_id)
    return _api("POST", "/api/chat", {"message": message, "session": sid},
                timeout=CHAT_TIMEOUT)


@mcp.tool
def odysseus_search(q: str, limit: int = 10) -> list:
    """Full-text search over the owner's past Odysseus chat messages.

    Returns a list of hits (message_id, session_id, session_name, role,
    content_snippet, ...). limit: 1-100.
    """
    return _api("GET", f"/api/search?q={urllib.parse.quote(q)}&limit={limit}")


@mcp.tool
def odysseus_tools() -> dict:
    """List the Odysseus agent's tools with their enabled/disabled status."""
    return _api("GET", "/api/tools")


# ── Capabilities (any valid token) ────────────────────────────────────────

@mcp.tool
def odysseus_capabilities() -> dict:
    """Report which surfaces the CURRENT upstream token may use.

    Passthrough of GET /api/codex/capabilities: token_scopes plus a
    read/write boolean per surface (todos, email, memory, calendar,
    documents, cookbook). Call this first to learn what the scoped
    tools can do before touching them.
    """
    return _api("GET", "/api/codex/capabilities", timeout=15)


# ── Email (scope-aware codex lane; requires email:read / email:draft /
#    email:send on the upstream token) ─────────────────────────────────────

@mcp.tool
def odysseus_list_emails(folder: str = "INBOX", limit: int = 50, offset: int = 0,
                         filter: str = "all", from_addr: str = "",
                         account_id: str = "", has_attachments: bool = False,
                         refresh: bool = False) -> dict:
    """DETERMINISTIC read-only email listing (no agent in the loop).

    Returns {emails, folder, offset, total} from the token owner's real
    mailboxes. filter: all | unread | unanswered. limit clamped 1-50
    (upstream codex wrapper caps at 50 — page with offset). Multi-account:
    account_id from odysseus-capabilities/email accounts, empty = default.
    refresh=True forces a live IMAP read (upstream refresh=1: invalidates the
    8s memory cache + pool, skips stale-serve) — use it when freshness
    matters; needs upstream codex wrapper to forward `refresh` (added
    2026-11-23; silently ignored by older upstreams).
    """
    params = {
        "folder": folder,
        "limit": max(1, min(50, int(limit))),
        "offset": max(0, int(offset)),
        "filter": filter,
        "from_addr": from_addr,
        "account_id": account_id,
        "has_attachments": 1 if has_attachments else 0,
    }
    if refresh:
        params["refresh"] = 1
    return _api("GET", f"/api/codex/emails?{_qs(params)}", timeout=60)


@mcp.tool
def odysseus_read_email(uid: str, folder: str = "INBOX", account_id: str = "",
                        mark_seen: bool = False) -> dict:
    """DETERMINISTIC fetch of ONE email body by uid (no agent).

    mark_seen defaults FALSE (read-state untouched). Set it True only when
    you really want the message flagged \\Seen in the mailbox. Use uids from
    odysseus_list_emails.
    """
    params = {"folder": folder, "account_id": account_id,
              "mark_seen": "true" if mark_seen else "false"}
    return _api("GET",
                f"/api/codex/emails/{urllib.parse.quote(str(uid))}?{_qs(params)}",
                timeout=60)


@mcp.tool
def odysseus_send_email(to: str, subject: str, body: str, cc: str = "",
                        bcc: str = "", body_html: str = "", in_reply_to: str = "",
                        references: str = "", attachments: list[str] | None = None,
                        account_id: str = "", source_uid: str = "",
                        source_folder: str = "",
                        wait_for_delivery: bool = False) -> dict:
    """WRITE — send an email as the token owner via SMTP. Real side effects.

    Reply semantics: set in_reply_to (+ references when available) and
    source_uid/source_folder of the message being answered — on delivery the
    source is marked answered and leaves reply-soon views. attachments takes
    compose-upload tokens. wait_for_delivery=True blocks until SMTP + Sent
    append finish and returns the sent uid (slower).
    """
    payload = _json_body({
        "to": to, "subject": subject, "body": body, "cc": cc, "bcc": bcc,
        "body_html": body_html, "in_reply_to": in_reply_to,
        "references": references, "attachments": attachments,
        "account_id": account_id, "source_uid": source_uid,
        "source_folder": source_folder, "wait_for_delivery": wait_for_delivery,
    })
    return _api("POST", "/api/codex/emails/send", payload, timeout=90)


@mcp.tool
def odysseus_save_email_draft(to: str, subject: str, body: str, cc: str = "",
                              bcc: str = "", body_html: str = "",
                              in_reply_to: str = "", references: str = "",
                              attachments: list[str] | None = None,
                              account_id: str = "") -> dict:
    """WRITE — save an email as an IMAP draft in the owner's Drafts folder.

    Nothing is sent. body_html is sanitized server-side and stored as the
    HTML part (body stays the plain-text fallback); without it the server
    renders markdown from body.
    """
    payload = _json_body({
        "to": to, "subject": subject, "body": body, "cc": cc, "bcc": bcc,
        "body_html": body_html, "in_reply_to": in_reply_to,
        "references": references, "attachments": attachments,
        "account_id": account_id,
    })
    return _api("POST", "/api/codex/emails/draft", payload, timeout=90)


# ── Uploads (owner-attributed raw lane; no scope model upstream) ──────────

@mcp.tool
def odysseus_upload_file(filename: str, content_b64: str,
                         content_type: str = "application/octet-stream",
                         session_id: str = "") -> dict:
    """WRITE — upload ONE file to the owner's upload store (base64 payload).

    Returns {files: [{id, name, mime, size, hash, ...}]} — the id is what
    chat/vision features reference. filename is sanitized to its basename.
    Keep files small-ish: the whole base64 blob travels through the MCP call.
    """
    fname = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(filename.strip())) or "upload.bin"
    try:
        blob = base64.b64decode(content_b64, validate=True)
    except Exception as e:
        raise RuntimeError(f"content_b64 is not valid base64: {e}") from None
    boundary = f"----odysseusmcp{time.time_ns():x}"
    parts = []
    if session_id:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="session_id"\r\n\r\n'
            f"{session_id}\r\n".encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; '
        f'filename="{fname}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
        + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    ctype = f"multipart/form-data; boundary={boundary}"
    return _api("POST", "/api/upload", multipart=(ctype, body), timeout=120)


# ── Todos / notes (codex lane; todos:read / todos:write) ──────────────────

@mcp.tool
def odysseus_list_todos(archived: bool = False, label: str = "") -> dict:
    """DETERMINISTIC listing of the owner's notes/checklists (todos).

    archived=True shows archived notes instead; label filters by label.
    """
    return _api("GET", f"/api/codex/todos?{_qs({'archived': archived, 'label': label})}",
                timeout=30)


@mcp.tool
def odysseus_manage_todos(action: str, note_title: str = "", text: str = "",
                          content: str = "", label: str = "", note_id: str = "",
                          query: str = "", due_date: str = "",
                          items: list[str] | None = None) -> dict:
    """Act on the owner's notes/checklists — 1:1 mirror of POST /api/codex/todos.

    action: list | search | view | add (aliases create/new/save/remind) |
    update | delete (alias remove) | toggle_item (alias remove_item).
    note_id accepts an id prefix (as printed by list). add: note_title or
    text, optional content, items (makes it a checklist), label, due_date
    (natural language ok). Write actions need todos:write on the upstream
    token. (The upstream body field is "title"; the tool param is
    note_title because MCP schema reserves "title".)
    """
    payload = _json_body({
        "action": action.strip().lower(), "title": note_title, "text": text,
        "content": content, "label": label, "note_id": note_id or None,
        "id": note_id or None, "query": query, "due_date": due_date,
        "items": items,
    })
    return _api("POST", "/api/codex/todos", payload, timeout=30)


# ── Memory (codex lane; memory:read / memory:write) ───────────────────────

@mcp.tool
def odysseus_list_memories() -> dict:
    """DETERMINISTIC listing of ALL the owner's long-term memory entries."""
    return _api("GET", "/api/codex/memory", timeout=30)


@mcp.tool
def odysseus_add_memory(text: str, category: str = "fact", source: str = "user",
                        session_id: str = "") -> dict:
    """WRITE — add one long-term memory entry for the owner.

    Duplicate text is a no-op (upstream dedupes). category is free-form
    (fact/preference/...); source attributes who added it; session_id
    optionally ties it to an Odysseus session.
    """
    payload = _json_body({
        "text": text, "category": category, "source": source,
        "session_id": session_id,
    })
    return _api("POST", "/api/codex/memory", payload, timeout=30)


@mcp.tool
def odysseus_delete_memory(memory_id: str) -> dict:
    """WRITE — delete ONE memory entry by id (ids from odysseus_list_memories)."""
    return _api("DELETE",
                f"/api/codex/memory/{urllib.parse.quote(str(memory_id))}",
                timeout=30)


# ── Calendar (codex lane; calendar:read / calendar:write) ─────────────────

@mcp.tool
def odysseus_list_calendar_events(start: str, end: str, calendar: str = "") -> dict:
    """DETERMINISTIC calendar listing — events overlapping [start, end).

    start/end are ISO 8601 (e.g. 2026-11-09T00:00:00Z). Recurring events are
    expanded into their occurrences. calendar filters by calendar id or name,
    empty = all of the owner's calendars.
    """
    return _api("GET", f"/api/codex/calendar/events?{_qs({'start': start, 'end': end, 'calendar': calendar})}",
                timeout=30)


@mcp.tool
def odysseus_create_calendar_event(summary: str, dtstart: str, dtend: str = "",
                                   all_day: bool = False, description: str = "",
                                   location: str = "", calendar_href: str = "",
                                   rrule: str = "", color: str = "") -> dict:
    """WRITE — create a calendar event. Returns {ok, uid}.

    dtstart ISO 8601 (offsets honored, stored UTC). dtend optional: defaults
    to +1 day when all_day, else +1 hour. rrule: RFC 5545 recurrence
    (e.g. FREQ=WEEKLY;BYDAY=MO,WE). calendar_href targets a calendar id
    (from the events payload); empty = default calendar.
    """
    payload = _json_body({
        "summary": summary, "dtstart": dtstart, "dtend": dtend,
        "all_day": all_day, "description": description, "location": location,
        "calendar_href": calendar_href, "rrule": rrule, "color": color,
    })
    payload["all_day"] = bool(all_day)
    return _api("POST", "/api/codex/calendar/events", payload, timeout=30)


@mcp.tool
def odysseus_delete_calendar_event(uid: str) -> dict:
    """WRITE — delete ONE calendar event by uid (uids from the listing tool)."""
    return _api("DELETE",
                f"/api/codex/calendar/events/{urllib.parse.quote(str(uid))}",
                timeout=30)


# ── Documents (codex lane, READ ONLY here; documents:read) ────────────────

@mcp.tool
def odysseus_list_documents(search: str = "", language: str = "",
                            sort: str = "recent", offset: int = 0,
                            limit: int = 50, archived: bool = False) -> dict:
    """DETERMINISTIC listing of the owner's document library.

    sort: recent | title. Response carries next_offset for paging.
    """
    return _api("GET", f"/api/codex/documents?{_qs({'search': search, 'language': language, 'sort': sort, 'offset': offset, 'limit': limit, 'archived': archived})}",
                timeout=30)


@mcp.tool
def odysseus_get_document(doc_id: str) -> dict:
    """DETERMINISTIC fetch of ONE document (metadata + content) by id."""
    return _api("GET", f"/api/codex/documents/{urllib.parse.quote(str(doc_id))}",
                timeout=30)


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    """Static bearer token on every route (tools, health, everything)."""

    async def dispatch(self, request: Request, call_next):
        expected = f"Bearer {GW_TOKEN}".encode()
        got = request.headers.get("authorization", "").encode()
        if not GW_TOKEN or not hmac.compare_digest(got, expected):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness for the HTTP gateway (also behind the bearer token)."""
    return JSONResponse(
        {"status": "ok", "version": __version__, "base_url": BASE_URL,
         "transport": "http", "ts": int(time.time())}
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("ODYSSEUS_TOKEN env var is required (ody_* API token)")
    if TRANSPORT == "http":
        if not GW_TOKEN:
            raise SystemExit("ODYSSEUS_MCP_TOKEN is required for HTTP mode")
        app = mcp.http_app(middleware=[Middleware(GatewayAuthMiddleware)])
        import uvicorn

        uvicorn.run(app, host=GW_HOST, port=GW_PORT, log_level="info")
    else:
        mcp.run()  # stdio default
