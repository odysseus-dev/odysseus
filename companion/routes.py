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

import html
import json as _json
import time
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing


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
    # Require the individual "chat" scope rather than the compound
    # COMPANION_SCOPE string: a paired token mints COMPANION_SCOPE
    # ("chat,companion"), which the auth layer splits into {"chat","companion"},
    # so the whole string is never an element of scope_set.
    if "chat" not in scope_set:
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
    return "companion" in scopes


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
        items = _json.loads(n.items) if n.items else None
    except (ValueError, TypeError):
        items = None
    return {
        "id": n.id, "title": n.title, "content": n.content,
        "items": items, "pinned": bool(n.pinned),
    }


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


def setup_companion_routes() -> APIRouter:
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
        import json as _json

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
                    model_ids = _json.loads(ep.cached_models) if ep.cached_models else []
                except (ValueError, TypeError):
                    model_ids = []
                try:
                    hidden = set(_json.loads(ep.hidden_models)) if ep.hidden_models else set()
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

    @router.get("/pair")
    def pair_page(request: Request):
        """Admin-only pairing page. Renders a form that POSTs to mint a code.

        A GET never mints a credential: SameSite=Lax session cookies ride
        top-level GET navigations, so minting on GET would be triggerable by a
        link or <img> (CSRF). The actual mint is the POST handler below.
        """
        require_admin(request)
        page = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair a device</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:48px auto;padding:0 20px;color:#e8e8e8;background:#16161a}
  .card{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:28px;text-align:center}
  button{background:#7c9cff;color:#0e0e12;border:none;border-radius:10px;padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}
</style></head>
<body><div class="card">
  <h2>Pair a device</h2>
  <p>Generate a one-time pairing code (a chat + companion scoped API token) for a LAN client.</p>
  <form method="POST" action="/api/companion/pair">
    <button type="submit">Generate pairing code</button>
  </form>
  <p style="color:#8a8a96;font-size:12px;margin-top:18px">Admin only. Each code mints a new token, shown once. Manage or revoke under Settings &rarr; API tokens.</p>
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

        import json as _json
        payload_json = _json.dumps(payload, separators=(",", ":"))
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
        import json as _json
        from core.database import SessionLocal, Note

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(Note).filter(Note.archived == False)  # noqa: E712
            if owner:
                q = q.filter((Note.owner == owner) | (Note.owner == None))  # noqa: E711
            for n in q.all():
                if not owner_can_see(n.owner, owner):
                    continue
                try:
                    items = _json.loads(n.items) if n.items else None
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

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(ScheduledTask)
            if owner:
                q = q.filter((ScheduledTask.owner == owner) | (ScheduledTask.owner == None))  # noqa: E711
            for t in q.all():
                if not owner_can_see(t.owner, owner):
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
        """List the caller's own long-term memories. Requires the companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read memory.")
        from core.database import SessionLocal, Memory

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(Memory)
            if owner:
                q = q.filter((Memory.owner == owner) | (Memory.owner == None))  # noqa: E711
            for m in q.all():
                if not owner_can_see(m.owner, owner):
                    continue
                out.append({"id": m.id, "text": m.text, "category": m.category})
        finally:
            db.close()
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
                parsed = _json.loads(items)
            except (ValueError, TypeError):
                raise HTTPException(400, "items must be a JSON array")
            if not isinstance(parsed, list):
                raise HTTPException(400, "items must be a JSON array")
            norm = [
                {"text": str(it.get("text", "")), "done": bool(it.get("done", False))}
                for it in parsed if isinstance(it, dict)
            ]
            checklist_json = _json.dumps(norm)
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
                checklist = _json.loads(note.items) if note.items else None
            except (ValueError, TypeError):
                checklist = None
            if not isinstance(checklist, list):
                raise HTTPException(400, "Note has no checklist items")
            if index < 0 or index >= len(checklist):
                raise HTTPException(400, f"Item index {index} out of range")
            checklist[index]["done"] = not checklist[index].get("done", False)
            note.items = _json.dumps(checklist)
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

    return router
