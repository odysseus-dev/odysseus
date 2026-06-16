"""Companion bridge — /api/companion/*.

A thin, additive layer so a LAN client (e.g. a phone) can discover what a server
offers and pair to it, without duplicating any LLM logic.

Auth is enforced globally by AuthMiddleware (app.py), so reaching a handler here
means the caller is authenticated by either a cookie session or a Bearer `ody_`
API token. Ping/info accept either credential type, models requires a chat-
scoped API token for bearer callers, and the pairing endpoints are admin-cookie
only.

Pairing CSRF posture: minting happens ONLY on POST. The session cookie is
SameSite=Lax (routes/auth_routes.py), which a browser does not send on a
cross-site POST, so an admin's cookie can't be used by a malicious page to mint
a token -- the same protection the existing POST /api/tokens relies on. Minting
on a GET would be unsafe (Lax cookies ride top-level GET navigations), so GET
/pair only renders a form.
"""

import asyncio
import html
import json
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field


from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing
from companion import push as _push

# Mirrors routes/research_routes._SESSION_ID_RE: research session IDs are opaque
# tokens, so reject anything that isn't a short alphanumeric/hyphen string before
# it touches the handler or a file path.
_RESEARCH_SID_RE = re.compile(r"^[a-zA-Z0-9-]{1,128}$")


def token_owner(request: Request) -> str | None:
    """The real owner to attribute a request to, for read-scoping.

    Cookie sessions resolve to the logged-in username via get_current_user.
    Bearer-token callers come through as the sandboxed pseudo-user "api"; their
    real owner is stamped on request.state.api_token_owner by the auth
    middleware. Returns None when no owner can be resolved.
    """
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None)
    return get_current_user(request)


def owner_can_see(row_owner, owner) -> bool:
    """Owner-scope rule for read endpoints.

    A caller sees a row when it is their own, or when it is a legacy null-owner
    ("shared") row. A caller must NEVER see another owner's row. Mirrors the
    `owner_filter` rule used elsewhere, expressed as a pure predicate so it can
    be tested directly and used as a defensive in-Python check alongside the
    SQL filter.
    """
    return row_owner is None or row_owner == owner


def require_models_scope(request: Request) -> None:
    """Require the companion chat scope for bearer-token model inventory."""
    if not getattr(request.state, "api_token", False):
        return
    scopes = getattr(request.state, "api_token_scopes", None) or []
    if isinstance(scopes, str):
        scopes = [scope.strip() for scope in scopes.split(",")]
    scope_set = {str(scope).strip() for scope in scopes if str(scope).strip()}
    if _pairing.COMPANION_SCOPE not in scope_set:
        raise HTTPException(403, "API token requires chat scope")


def has_companion_scope(request: Request) -> bool:
    """Whether the caller may read the companion DATA views (notes/tasks/memory).

    A cookie session (the logged-in user) always may. A bearer token must carry
    the explicit ``companion`` scope: a plain ``chat`` token cannot read your
    private notes or memory. This keeps these reads strictly NARROWER than chat,
    per review. Pure + testable.
    """
    if not getattr(request.state, "api_token", False):
        return True
    scopes = getattr(request.state, "api_token_scopes", None) or []
    if isinstance(scopes, str):
        scopes = [scope.strip() for scope in scopes.split(",")]
    return "companion" in {str(scope).strip() for scope in scopes if str(scope).strip()}


def _auth_disabled() -> bool:
    """True only when the operator explicitly turned auth off (AUTH_ENABLED=false).

    Mirrors ``src.auth_helpers._auth_disabled`` so the companion reads agree with
    the rest of the app on what the supported single-user, no-login mode is.
    """
    import os
    return os.getenv("AUTH_ENABLED", "true").lower() == "false"


def read_owner(request: Request) -> str | None:
    """Resolve the owner for a companion DATA read, failing closed.

    Notes / tasks / memory are PRIVATE, so — like their owning routes — they are
    filtered by EXACT owner and never widen to legacy null-owner "shared" rows
    (that sharing is only appropriate for documents/gallery, not private data).

    Returns the resolved owner, or ``None`` for the supported single-user
    (``AUTH_ENABLED=false``) mode, where the owning routes also skip the owner
    filter. If no owner resolves while auth is ON, the request is rejected rather
    than treated as single-user — so an auth-middleware regression can't hand a
    tokenless caller a blanket view of every account's private data.
    """
    owner = token_owner(request)
    if owner:
        return owner
    if _auth_disabled():
        return None
    raise HTTPException(403, "No owner could be resolved for this request.")


def _default_memory_manager():
    """Fallback ``MemoryManager`` when the app didn't inject one.

    ``setup_companion_routes`` is passed the app's live manager in app.py; this
    only covers standalone/tooling callers, reading the same memory.json.
    """
    from src.memory import MemoryManager
    from src.constants import DATA_DIR
    return MemoryManager(DATA_DIR)


def require_companion_scope(request: Request) -> None:
    """Raise 403 unless the caller may touch the companion data views. The
    write handlers gate on this exactly like the reads gate on
    has_companion_scope, so a plain ``chat`` token can neither read nor mutate
    notes/memory."""
    if not has_companion_scope(request):
        raise HTTPException(403, "This token is not allowed to access companion data.")


def writer_owner(request: Request) -> str | None:
    """Owner to stamp on a new/mutated row.

    Cookie sessions and single-user mode resolve to a username or None (None =
    legacy shared row, the long-standing behaviour). A BEARER token, however,
    must have a resolvable owner: a null-owner token would otherwise fall
    through to mutating shared/null-owner rows it doesn't own, so we refuse it
    (401) rather than widen its scope. Mirrors the reasoning in the desktop
    note/memory routes.
    """
    owner = token_owner(request)
    if owner is None and getattr(request.state, "api_token", False):
        raise HTTPException(401, "Token owner could not be resolved.")
    return owner


# Categories the mobile composer offers; anything else coerces to "fact". Mirrors
# the server allowlist in src/request_models.py so companion-created memories are
# indistinguishable from desktop ones.
_MEMORY_CATEGORIES = {"fact", "identity", "preference", "contact", "project", "goal", "task"}


def _serialize_note(n) -> dict:
    """The exact shape GET /notes returns, so a created/updated note round-trips
    into the mobile list without a refetch."""
    try:
        items = json.loads(n.items) if n.items else None
    except (ValueError, TypeError):
        items = None
    return {
        "id": n.id, "title": n.title, "content": n.content,
        "items": items, "pinned": bool(n.pinned),
    }


class CompanionResearchStart(BaseModel):
    """Body for POST /api/companion/research/start.

    The mobile client already knows its endpoints+models (from
    /api/companion/models), so it normally passes endpoint_id+model explicitly.
    Both are optional: with neither, the server resolves its own research/default
    endpoint. `max_rounds=0` means "Auto" (let the AI decide, capped at 20),
    matching the stock research panel.
    """

    query: str
    endpoint_id: str | None = None
    model: str | None = None
    max_rounds: int = Field(default=0, ge=0, le=20)
    max_time: int = Field(default=300, ge=60, le=1800)
    category: str | None = None
    search_provider: str | None = None


def research_owns(research_handler, session_id: str, owner) -> bool:
    """Ownership gate for a research session — mirrors
    routes/research_routes._owns_in_memory so the bridge enforces the SAME rule:
    an in-flight task is owned per its in-memory entry; a finished one per the
    persisted JSON's `owner`. Returns False when neither resolves to `owner`, so
    callers can 404 (never 403 — don't leak that someone else's run exists).
    Pure given the handler, so it's unit-testable with a fake handler.
    """
    entry = research_handler._active_tasks.get(session_id)
    if entry is not None:
        return entry.get("owner", "") == owner
    path = Path("data/deep_research") / f"{session_id}.json"
    if not path.exists():
        return False
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("owner") == owner
    except Exception:
        return False


def resolve_research_endpoint(body: "CompanionResearchStart", owner) -> tuple:
    """(url, model, headers) for a companion research run.

    With an explicit endpoint_id, use that endpoint ONLY if the caller owns it
    (or it's a legacy shared row) — never another owner's endpoint, so a bearer
    token can't research through a stranger's API key. With no endpoint_id, fall
    back to the server's resolved research → default → chat endpoint, the same
    spirit as /api/research/start's chain (kept short here on purpose). Returns
    ("", "", {}) when nothing is configured.
    """
    from src.endpoint_resolver import resolve_endpoint

    if body.endpoint_id:
        from core.database import ModelEndpoint, SessionLocal
        from src.endpoint_resolver import build_chat_url, build_headers, normalize_base

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == body.endpoint_id,
                ModelEndpoint.is_enabled == True,  # noqa: E712
            ).first()
            if not ep or not owner_can_see(ep.owner, owner):
                raise HTTPException(404, "Endpoint not found or disabled")
            base = normalize_base(ep.base_url)
            model = body.model or ""
            if not model and ep.cached_models:
                try:
                    ids = json.loads(ep.cached_models)
                    model = ids[0] if ids else ""
                except (ValueError, TypeError):
                    model = ""
            return build_chat_url(base), model, build_headers(ep.api_key, base)
        finally:
            db.close()

    for kind in ("research", "default", "chat"):
        url, model, headers = resolve_endpoint(kind)
        if url:
            return url, (body.model or model), headers
    return "", "", {}


def mint_pairing_token(owner: str, invalidate=None) -> tuple[str, str]:
    """Mint a pairing token AND invalidate the auth middleware's in-memory token
    cache, so the new token is accepted on the very next request without a server
    restart. Returns (token_id, raw_token); the raw token is shown once.

    `invalidate` is the app's request.app.state.invalidate_token_cache callable
    (passed in so this stays a pure, testable unit).
    """
    token_id, raw_token = _pairing.mint_token(owner)
    if callable(invalidate):
        invalidate()
    return token_id, raw_token


def setup_companion_routes(memory_manager=None, research_handler=None) -> APIRouter:
    """Build the companion router.

    `memory_manager` is the app's live MemoryManager (memory.json) — the store
    the app actually persists memories to — used by the /memory views.

    `research_handler` is the app's Deep Research task manager. When provided,
    the /api/companion/research/* launcher endpoints are mounted (start, watch,
    cancel, read — all re-scoped to the token's real owner). When None, those
    routes are simply absent, so the base bridge (ping/info/models/pair) stays
    usable without research wired in.
    """
    router = APIRouter(prefix="/api/companion", tags=["companion"])

    @router.get("/ping")
    def ping(request: Request):
        """Cheap, auth-validated health check. A 200 with ok=true confirms the
        host/port and credential are valid; middleware returns 401 otherwise."""
        from core.constants import APP_VERSION
        return {
            "ok": True,
            "name": "odysseus",
            "version": APP_VERSION,
            "auth": "token" if getattr(request.state, "api_token", False) else "session",
        }

    @router.get("/info")
    def info(request: Request):
        """Server identity + coarse capability flags. `owner` is the caller's own
        identity (the token's owner for bearer callers)."""
        from core.constants import APP_VERSION
        return {
            "name": "odysseus",
            "version": APP_VERSION,
            "owner": token_owner(request),
            "capabilities": {"chat": True, "streaming": True},
        }

    @router.get("/models")
    def models(request: Request):
        """LLM model endpoints the CALLER can use.

        The stock /api/models route scopes to get_current_user, which for a
        bearer token is the sandboxed pseudo-user "api" (owns nothing). Here we
        scope to the token's real owner instead, plus legacy null-owner shared
        rows -- the same rule as owner_filter. Read-only; never returns api_key
        material.
        """
        require_models_scope(request)

        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import build_chat_url

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.is_enabled == True,  # noqa: E712
                (ModelEndpoint.model_type == "llm") | (ModelEndpoint.model_type == None),  # noqa: E711
            )
            if owner:
                q = q.filter((ModelEndpoint.owner == owner) | (ModelEndpoint.owner == None))  # noqa: E711
            for ep in q.all():
                if not owner_can_see(ep.owner, owner):
                    continue
                try:
                    model_ids = json.loads(ep.cached_models) if ep.cached_models else []
                except (ValueError, TypeError):
                    model_ids = []
                try:
                    hidden = set(json.loads(ep.hidden_models)) if ep.hidden_models else set()
                except (ValueError, TypeError):
                    hidden = set()
                model_ids = [m for m in model_ids if m not in hidden]
                try:
                    chat_url = build_chat_url(ep.base_url)
                except Exception:
                    chat_url = ep.base_url
                out.append({
                    "endpoint_id": ep.id,
                    "name": ep.name,
                    "endpoint_url": chat_url,
                    "models": model_ids,
                    "supports_tools": ep.supports_tools,
                })
        finally:
            db.close()
        return {"endpoints": out}

    async def _push_token_from_body(request: Request) -> str:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        return ((body or {}).get("token") or "").strip()

    @router.post("/push/register")
    async def push_register(request: Request):
        """Register this device's Expo push token for the calling owner.

        Owner-scoped: bearer callers resolve to their token's real owner, so a
        paired phone registers under that account and never another's. The token
        is a device handle, not a chat credential."""
        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Unknown caller")
        token = await _push_token_from_body(request)
        try:
            _push.register_push_token(owner, token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "devices": len(_push.list_push_tokens(owner))}

    @router.post("/push/unregister")
    async def push_unregister(request: Request):
        """Drop one Expo push token for the calling owner (e.g. on sign-out)."""
        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Unknown caller")
        token = await _push_token_from_body(request)
        _push.unregister_push_token(owner, token)
        return {"ok": True, "devices": len(_push.list_push_tokens(owner))}

    @router.post("/push/test")
    async def push_test(request: Request):
        """Send a test notification to the caller's registered devices."""
        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Unknown caller")
        sent = await _push.send_test_push(owner)
        return {"ok": True, "sent": sent}

    @router.get("/pair")
    def pair_page(request: Request):
        """Admin-only pairing page. Renders a form that POSTs to mint a code.

        A GET never mints a credential: SameSite=Lax session cookies ride
        top-level GET navigations, so minting on GET would be triggerable by a
        link or <img> (CSRF). The actual mint is the POST handler below.
        """
        require_admin(request)
        from src.settings import get_setting

        admin_on = bool(get_setting("companion_admin_enabled", False))
        # Reflect the current state and offer the inverse action. The values are
        # fixed (booleans → constant strings), so this f-string carries no user
        # input — nothing to escape.
        admin_section = f"""
  <hr style="border:none;border-top:1px solid #2c2c35;margin:26px 0">
  <h3 style="margin:0 0 6px">Admin tools</h3>
  <p style="color:#8a8a96;font-size:13px;margin:0 0 14px;text-align:left">
    Status: <strong style="color:{'#5fd08a' if admin_on else '#e0a85e'}">{'On' if admin_on else 'Off'}</strong>.
    Lets a paired <em>admin</em> device use this server's Terminal, Vault, Contacts,
    MCP and Cookbook. This grants full shell access to admin-owned devices &mdash;
    leave it off unless you need it.
  </p>
  <form method="POST" action="/api/companion/admin-access">
    <input type="hidden" name="enabled" value="{'false' if admin_on else 'true'}">
    <button type="submit" class="toggle">{'Turn off admin tools' if admin_on else 'Turn on admin tools'}</button>
  </form>"""
        toggle_style = (
            "background:#3a3a44;color:#e8e8e8" if admin_on else "background:#e0a85e;color:#0e0e12"
        )
        page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair a device</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:48px auto;padding:0 20px;color:#e8e8e8;background:#16161a}}
  .card{{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:28px;text-align:center}}
  button{{background:#7c9cff;color:#0e0e12;border:none;border-radius:10px;padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}}
  button.toggle{{{toggle_style}}}
</style></head>
<body><div class="card">
  <h2>Pair a device</h2>
  <p>Generate a one-time pairing code (a chat-scoped API token) for a LAN client.</p>
  <form method="POST" action="/api/companion/pair">
    <button type="submit">Generate pairing code</button>
  </form>
  <p style="color:#8a8a96;font-size:12px;margin-top:18px">Admin only. Each code mints a new token, shown once. Manage or revoke under Settings &rarr; API tokens.</p>
{admin_section}
</div></body></html>"""
        return HTMLResponse(page)

    @router.post("/pair")
    def pair_create(request: Request):
        """Mint a pairing code. Admin-cookie only; CSRF-safe because the
        SameSite=Lax session cookie is not sent on a cross-site POST (same
        protection as POST /api/tokens). Minting invalidates the token cache so
        the code works immediately, no restart. `?format=json` returns the
        payload for an in-app pairing screen."""
        require_admin(request)
        owner = get_current_user(request)
        invalidate = getattr(request.app.state, "invalidate_token_cache", None)
        token_id, raw_token = mint_pairing_token(owner, invalidate)

        hosts = _pairing.lan_ip_candidates()
        host = hosts[0] if hosts else "127.0.0.1"
        port = request.url.port or _pairing.default_port()
        payload = _pairing.pairing_payload(host, port, raw_token)
        qr = _pairing.pairing_qr_png_data_uri(payload)
        qr_ok = bool(qr and qr.startswith("data:image/png;base64,"))

        if (request.query_params.get("format") or "").lower() == "json":
            return {
                "host": host,
                "port": port,
                "token": raw_token,
                "token_id": token_id,
                "hosts": hosts,
                "payload": payload,
                "qr": qr if qr_ok else None,
            }

        payload_json = json.dumps(payload, separators=(",", ":"))
        # Only ever emit a known PNG data-URI into the src; every other value is
        # html.escaped.
        qr_block = (
            f'<img src="{html.escape(qr)}" alt="Pairing QR" width="260" height="260">'
            if qr_ok else "<p><em>QR rendering unavailable -- enter the details manually.</em></p>"
        )
        page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pairing code</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 20px;color:#e8e8e8;background:#16161a}}
  .card{{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:24px;text-align:center}}
  code{{background:#0e0e12;padding:2px 6px;border-radius:6px;word-break:break-all}}
  .row{{text-align:left;margin:10px 0;font-size:14px;color:#bdbdc7}}
  .warn{{color:#e0a85e;font-size:13px;margin-top:18px}}
</style></head>
<body><div class="card">
  <h2>Pairing code</h2>
  {qr_block}
  <div class="row"><strong>Host:</strong> <code>{html.escape(host)}</code></div>
  <div class="row"><strong>Port:</strong> <code>{html.escape(str(port))}</code></div>
  <div class="row"><strong>Token:</strong> <code>{html.escape(raw_token)}</code></div>
  <div class="row"><strong>Payload:</strong> <code>{html.escape(payload_json)}</code></div>
  <p class="warn">Shown once. This grants chat access to your Odysseus; revoke it
  in Settings &rarr; API tokens (id <code>{html.escape(token_id)}</code>). The
  device must be on the same network, and the server must bind to your LAN.</p>
</div></body></html>"""
        return HTMLResponse(page)

    @router.get("/notes")
    def notes(request: Request):
        """List the caller's own notes. Requires the companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read notes.")
        from core.database import SessionLocal, Note

        owner = read_owner(request)
        out = []
        db = SessionLocal()
        try:
            # EXACT-owner filter (never null-owner "shared") — matches the owning
            # note routes. Single-user (AUTH_ENABLED=false → owner None) skips the
            # filter and shows the local user's rows, same as list_notes.
            q = db.query(Note).filter(Note.archived == False)  # noqa: E712
            if owner is not None:
                q = q.filter(Note.owner == owner)
            for n in q.all():
                if owner is not None and n.owner != owner:
                    continue
                try:
                    items = json.loads(n.items) if n.items else None
                except (ValueError, TypeError):
                    items = None
                out.append({
                    "id": n.id, "title": n.title, "content": n.content,
                    "items": items, "pinned": bool(n.pinned),
                })
        finally:
            db.close()
        return {"items": out}

    @router.get("/tasks")
    def tasks(request: Request):
        """The caller's own scheduled tasks (read-only). Requires the companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read tasks.")
        from core.database import SessionLocal, ScheduledTask

        owner = read_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(ScheduledTask)
            if owner is not None:
                q = q.filter(ScheduledTask.owner == owner)
            for t in q.all():
                if owner is not None and t.owner != owner:
                    continue
                out.append({
                    "id": t.id, "name": t.name, "schedule": t.schedule,
                    "enabled": t.status == "active",
                    "last_run": t.last_run.isoformat() + "Z" if t.last_run else None,
                })
        finally:
            db.close()
        return {"items": out}

    @router.get("/memory")
    def memory(request: Request):
        """The caller's own long-term memories (read-only). Requires the companion scope.

        Reads through the active ``MemoryManager`` (memory.json) — the store the
        app actually writes to — not the ORM ``Memory`` table, which the running
        app does not populate. ``load(owner=...)`` is exact-owner (no null-owner
        sharing); single-user (owner None) returns the local user's memories.
        """
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read memory.")

        owner = read_owner(request)
        mm = memory_manager or _default_memory_manager()
        entries = mm.load(owner=owner) if owner is not None else mm.load()
        out = [
            {"id": e.get("id"), "text": e.get("text"), "category": e.get("category")}
            for e in (entries or [])
        ]
        return {"items": out}

    # ---- Writes -----------------------------------------------------------
    # The reads above are the established pattern; these add the phone's
    # create/delete/toggle affordances. Each write requires the companion scope
    # and a resolvable owner, stamps that owner on new rows, and enforces strict
    # ownership on mutate/delete (404 — never confirm a row's existence to a
    # non-owner), exactly like the desktop note/memory routes. Writes hit the
    # same tables the matching GET reads, so the mobile list stays consistent.

    def _owned_note(db, note_id: str, owner):
        from core.database import Note
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note or note.owner != owner:
            raise HTTPException(404, "Note not found")
        return note

    @router.post("/notes")
    def add_note(
        request: Request,
        title: str = Form(""),
        content: str = Form(None),
        items: str = Form(None),
        pinned: bool = Form(False),
    ):
        """Create a note or checklist. `items`, when given, is a JSON array of
        {text, done} objects (a checklist); otherwise it's a plain text note."""
        require_companion_scope(request)
        owner = writer_owner(request)

        checklist_json = None
        note_type = "note"
        if items:
            try:
                parsed = json.loads(items)
            except (ValueError, TypeError):
                raise HTTPException(400, "items must be a JSON array")
            if not isinstance(parsed, list):
                raise HTTPException(400, "items must be a JSON array")
            norm = [
                {"text": str(it.get("text", "")), "done": bool(it.get("done", False))}
                for it in parsed if isinstance(it, dict)
            ]
            checklist_json = json.dumps(norm)
            note_type = "checklist"

        if not (title or "").strip() and not (content or "") and not checklist_json:
            raise HTTPException(400, "empty note")

        from core.database import SessionLocal, Note
        db = SessionLocal()
        try:
            note = Note(
                id=str(uuid.uuid4()), owner=owner, title=title or "", content=content,
                items=checklist_json, note_type=note_type, pinned=bool(pinned), source="mobile",
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return _serialize_note(note)
        finally:
            db.close()

    @router.delete("/notes/{note_id}")
    def delete_note(request: Request, note_id: str):
        """Delete one of the caller's notes."""
        require_companion_scope(request)
        owner = writer_owner(request)
        from core.database import SessionLocal
        db = SessionLocal()
        try:
            db.delete(_owned_note(db, note_id, owner))
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/notes/{note_id}/pin")
    def toggle_note_pin(request: Request, note_id: str):
        """Flip a note's pinned flag."""
        require_companion_scope(request)
        owner = writer_owner(request)
        from core.database import SessionLocal
        db = SessionLocal()
        try:
            note = _owned_note(db, note_id, owner)
            note.pinned = not note.pinned
            db.commit()
            return {"ok": True, "pinned": note.pinned}
        finally:
            db.close()

    @router.post("/notes/{note_id}/items/{index}/toggle")
    def toggle_note_item(request: Request, note_id: str, index: int):
        """Toggle the done state of one checklist item by index."""
        require_companion_scope(request)
        owner = writer_owner(request)
        from core.database import SessionLocal
        from sqlalchemy.orm.attributes import flag_modified
        db = SessionLocal()
        try:
            note = _owned_note(db, note_id, owner)
            try:
                checklist = json.loads(note.items) if note.items else None
            except (ValueError, TypeError):
                checklist = None
            if not isinstance(checklist, list):
                raise HTTPException(400, "Note has no checklist items")
            if index < 0 or index >= len(checklist):
                raise HTTPException(400, f"Item index {index} out of range")
            checklist[index]["done"] = not checklist[index].get("done", False)
            note.items = json.dumps(checklist)
            flag_modified(note, "items")
            db.commit()
            return {"ok": True, "items": checklist}
        finally:
            db.close()

    @router.post("/memory")
    def add_memory(request: Request, text: str = Form(...), category: str = Form("fact")):
        """Create a memory owned by the caller. Writes the same `memories` table
        GET /memory reads, so it appears in the mobile list immediately."""
        require_companion_scope(request)
        owner = writer_owner(request)
        text = (text or "").strip()
        if not text:
            raise HTTPException(400, "empty memory")
        cat = category if category in _MEMORY_CATEGORIES else "fact"

        from core.database import SessionLocal, Memory
        db = SessionLocal()
        try:
            row = Memory(
                id=str(uuid.uuid4()), text=text, category=cat,
                source="mobile", owner=owner, timestamp=int(time.time()),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return {"id": row.id, "text": row.text, "category": row.category}
        finally:
            db.close()

    @router.delete("/memory/{memory_id}")
    def delete_memory(request: Request, memory_id: str):
        """Delete one of the caller's memories."""
        require_companion_scope(request)
        owner = writer_owner(request)
        from core.database import SessionLocal, Memory
        db = SessionLocal()
        try:
            row = db.query(Memory).filter(Memory.id == memory_id).first()
            if not row or row.owner != owner:
                raise HTTPException(404, "Memory not found")
            db.delete(row)
            db.commit()
            return {"ok": True}
        finally:
            db.close()


    # ------------------------------------------------------------------
    # Deep Research launcher — owner-scoped bridge over research_handler.
    #
    # The stock /api/research/* routes resolve a bearer caller to the sandboxed
    # pseudo-user "api" (get_current_user), so a run started there would be owned
    # by "api": invisible in the owner's web-UI library and gated by "api"'s
    # privileges. These mirror the stock endpoints but re-scope every run to the
    # token's REAL owner (token_owner), exactly like /api/companion/models. No
    # extra `companion` scope is required: research is a chat-class generation
    # capability (web-searching chat), and a caller only ever sees their own runs.
    # ------------------------------------------------------------------
    if research_handler is not None:

        def _require_owner(request: Request) -> str:
            owner = token_owner(request)
            if not owner:
                raise HTTPException(401, "Not authenticated")
            return owner

        def _validate_sid(session_id: str) -> None:
            if not _RESEARCH_SID_RE.fullmatch(session_id):
                raise HTTPException(400, "Invalid session ID format")

        @router.get("/research/active")
        def research_active(request: Request):
            """The caller's own currently-running research runs."""
            owner = _require_owner(request)
            active = []
            for sid, entry in research_handler._active_tasks.items():
                if entry.get("owner", "") != owner:
                    continue
                if entry.get("status") == "running":
                    active.append({
                        "session_id": sid,
                        "query": entry.get("query", ""),
                        "status": "running",
                        "progress": entry.get("progress", {}),
                        "started_at": entry.get("started_at", 0),
                    })
            return {"active": active}

        @router.post("/research/start")
        def research_start(body: CompanionResearchStart, request: Request):
            """Launch a research run attributed to the token's real owner."""
            owner = _require_owner(request)
            # Best-effort privilege gate, mirroring /api/research/start: honor an
            # explicit can_use_research=false for this owner; default allow.
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
                try:
                    privs = auth_mgr.get_privileges(owner) or {}
                    if not privs.get("can_use_research", True):
                        raise HTTPException(403, "Your account is not allowed to use research.")
                except HTTPException:
                    raise
                except Exception:
                    pass

            ep_url, ep_model, ep_headers = resolve_research_endpoint(body, owner)
            if not ep_url:
                raise HTTPException(400, "No endpoints configured. Add one in Settings first.")

            session_id = f"rp-{uuid.uuid4().hex[:12]}"
            # max_rounds=0 → "Auto"; pass 20 as the safety cap (matches the panel).
            effective_max_rounds = body.max_rounds if body.max_rounds > 0 else 20
            research_handler.start_research(
                session_id=session_id,
                query=body.query,
                llm_endpoint=ep_url,
                llm_model=ep_model,
                max_time=body.max_time,
                llm_headers=ep_headers,
                max_rounds=effective_max_rounds,
                search_provider=body.search_provider or None,
                category=body.category or None,
                owner=owner,
            )
            return {"session_id": session_id, "status": "running", "query": body.query}

        @router.get("/research/stream/{session_id}")
        async def research_stream(session_id: str, request: Request):
            """SSE progress for one run (owner-checked). Emits a progress event on
            change and a final `{status, final:true}` when the run leaves
            running, then closes — same shape as /api/research/stream."""
            owner = _require_owner(request)
            _validate_sid(session_id)
            if not research_owns(research_handler, session_id, owner):
                raise HTTPException(404, "No research found for this session")

            async def _generate():
                last_progress = None
                while True:
                    status = research_handler.get_status(session_id)
                    if status is None:
                        yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                        return
                    st = status.get("status", "")
                    progress = status.get("progress", {})
                    if progress != last_progress:
                        last_progress = progress
                        yield f"data: {json.dumps({**progress, 'status': st})}\n\n"
                    if st != "running":
                        final = {'status': st, 'final': True}
                        task = research_handler._active_tasks.get(session_id, {})
                        if st == "error" and task.get("result"):
                            final['error'] = str(task["result"])[:500]
                        yield f"data: {json.dumps(final)}\n\n"
                        return
                    await asyncio.sleep(1.5)

            return StreamingResponse(
                _generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @router.post("/research/cancel/{session_id}")
        def research_cancel(session_id: str, request: Request):
            """Cancel one of the caller's running runs."""
            owner = _require_owner(request)
            _validate_sid(session_id)
            if not research_owns(research_handler, session_id, owner):
                raise HTTPException(404, "No research found for this session")
            return {"cancelled": research_handler.cancel_research(session_id)}

        @router.post("/research/result/{session_id}")
        def research_result(session_id: str, request: Request):
            """Read a run's report + sources WITHOUT clearing it (so the phone can
            re-open it). Prefers the in-memory result, falls back to the persisted
            JSON for a finished run."""
            owner = _require_owner(request)
            _validate_sid(session_id)
            if not research_owns(research_handler, session_id, owner):
                raise HTTPException(404, "No research result available")
            result = research_handler.get_result(session_id)
            if result is None:
                p = Path("data/deep_research") / f"{session_id}.json"
                if p.exists():
                    try:
                        d = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        raise HTTPException(404, "No research result available")
                    return {
                        "result": d.get("result", ""),
                        "sources": d.get("sources", []),
                        "query": d.get("query", ""),
                        "status": d.get("status", "done"),
                    }
                raise HTTPException(404, "No research result available")
            return {
                "result": result,
                "sources": research_handler.get_sources(session_id) or [],
                "query": "",
                "status": "done",
            }

    @router.get("/admin-access")
    def get_admin_access(request: Request):
        """Admin-only (cookie): current state of the admin-tools opt-in, for the
        Settings UI toggle to reflect on load."""
        require_admin(request)
        from src.settings import get_setting

        return {"enabled": bool(get_setting("companion_admin_enabled", False))}

    @router.post("/admin-access")
    def set_admin_access(request: Request, enabled: str = Form("")):
        """Flip the `companion_admin_enabled` server setting.

        This is the deliberate opt-in that unlocks a paired admin device's admin
        tools (Terminal/Vault/Contacts/MCP/Cookbook) — lock #1 of the
        require_companion_admin triple-lock. Admin-cookie only, and CSRF-safe for
        the same reason as POST /pair: the SameSite=Lax session cookie is not sent
        on a cross-site POST. Enabling grants admin-owned companion tokens full
        shell access, so it stays off until an admin sets it here on purpose.

        `?format=json` (the Settings UI toggle) gets the new state back as JSON;
        a plain HTML form post redirects to the pairing page to re-render it."""
        require_admin(request)
        from src.settings import load_settings, save_settings

        val = str(enabled).strip().lower() in ("1", "true", "on", "yes")
        current = load_settings()
        current["companion_admin_enabled"] = val
        save_settings(current)
        if (request.query_params.get("format") or "").lower() == "json":
            return {"ok": True, "enabled": val}
        return RedirectResponse("/api/companion/pair", status_code=303)

    return router
