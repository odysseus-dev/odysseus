"""Companion bridge routes — /api/companion/*.

These endpoints are reachable with either a logged-in cookie session or, more
usefully for the mobile app, a Bearer `ody_` API token. Auth is already enforced
globally by AuthMiddleware in app.py, so reaching a handler here means the caller
is authenticated.

Surface (all owner-scoped to the token's real owner, NOT the "api" pseudo-user):
  - ping / info                      pairing health + capability discovery
  - GET models                       the owner's LLM endpoints (for new chats)
  - GET/POST/DELETE memory           the owner's memories (read + write)
  - GET/POST/DELETE notes, pin,
    items/{i}/toggle                 the owner's notes/checklists (read + write)
  - GET tasks                        the owner's scheduled tasks (read-only)
  - GET pair                         admin-only token minting + QR page

So the pairing token is NOT read-only: it grants chat access plus create/delete
of the owner's notes and memories. Chat/session traffic itself uses the stock
/api/sessions, /api/session, /api/history, /api/chat_stream endpoints, which are
owner-aware for bearer tokens via effective_user (see src/auth_helpers.py).
Mutating endpoints here require the companion scope (_require_companion_scope)
and a resolvable owner (_require_owner).
"""

import html
import json as _json
import time
import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.middleware import require_admin
from src.auth_helpers import effective_user, get_current_user
from companion import pairing as _pairing

# Categories the phone's memory composer offers. Mirrors the set the desktop
# memory UI uses (routes/memory_routes.py); anything else falls back to "fact".
_MEMORY_CATEGORIES = {"fact", "identity", "preference", "contact", "project", "goal"}

# Scope a token must carry to use the companion bridge. The pairing helper mints
# tokens with exactly this scope (companion/pairing.py), as does the default
# token route. Enforced on the write endpoints below so a token minted WITHOUT
# it (e.g. a future read-only scope) can't create/delete the owner's notes or
# memories even though it can still authenticate.
_COMPANION_SCOPE = "chat"


def _token_owner(request: Request) -> str | None:
    """Real owner behind the request (shared effective_user logic).

    Cookie sessions resolve to the username; bearer-token callers come through
    as the pseudo-user "api" but their real owner is stamped on
    request.state.api_token_owner by the auth middleware (app.py).
    """
    return effective_user(request)


def _require_owner(request: Request) -> str:
    """Resolve the real owner, or 401 if it can't be determined.

    Guards against a bearer token whose owner is null (the ApiToken.owner
    column is nullable). Without this, the `if owner:` filters below would fall
    through to "no owner filter" and expose / allow mutation of every
    null-owner shared row to such a token. A token with no resolvable owner is
    an auth fault — refuse rather than widen scope.
    """
    owner = _token_owner(request)
    if not owner:
        raise HTTPException(401, "Token owner could not be resolved")
    return owner


def _require_companion_scope(request: Request) -> None:
    """Bearer tokens must carry the companion scope to mutate data.

    Cookie sessions (the admin/owner in a browser) are exempt — they're already
    the authenticated owner. Bearer tokens are checked against the scope list
    the middleware stamped on request.state.api_token_scopes.
    """
    if not getattr(request.state, "api_token", False):
        return
    scopes = getattr(request.state, "api_token_scopes", None) or []
    if _COMPANION_SCOPE not in scopes:
        raise HTTPException(403, f"Token lacks the '{_COMPANION_SCOPE}' scope")


def _iso_utc(dt) -> str | None:
    """Serialize a datetime as a UTC ISO-8601 string with a trailing 'Z'.

    The DB stores last_run as a naive (assumed-UTC) datetime, so plain
    `isoformat() + "Z"` is right for those — but if a value ever carries tz
    info, that concatenation produces a malformed `...+00:00Z`. Normalise via
    timezone-awareness instead so the phone always gets a valid timestamp.
    """
    if dt is None:
        return None
    from datetime import timezone

    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def setup_companion_routes() -> APIRouter:
    router = APIRouter(prefix="/api/companion", tags=["companion"])

    @router.get("/ping")
    def ping(request: Request):
        """Pairing health check. The phone hits this right after scanning a
        pairing code to confirm host + port + token are all valid. A 200 with
        ok=true means the token was accepted; a 401 (from middleware) means it
        wasn't."""
        from core.constants import APP_VERSION
        return {
            "ok": True,
            "name": "odysseus",
            "version": APP_VERSION,
            "auth": "token" if getattr(request.state, "api_token", False) else "session",
        }

    @router.get("/info")
    def info(request: Request):
        """Server identity + capability flags so the client can tailor its UI
        without probing every feature route."""
        from core.constants import APP_VERSION
        return {
            "name": "odysseus",
            "version": APP_VERSION,
            "owner": _token_owner(request),
            "capabilities": {
                "chat": True,
                "agent": True,
                "web_search": True,
                "research": True,
                "streaming": True,
                # The phone can now create/delete these, not just read them.
                "notes_write": True,
                "memory_write": True,
            },
            # The mobile client uses the existing endpoints below; surfaced here
            # so the contract is discoverable from one place.
            "endpoints": {
                "models": "/api/companion/models",
                "sessions": "/api/sessions",
                "create_session": "/api/session",
                "chat_stream": "/api/chat_stream",
                "chat_stop": "/api/chat/stop/{session_id}",
                "history": "/api/history/{session_id}",
            },
        }

    @router.get("/models")
    def models(request: Request):
        """LLM models the TOKEN OWNER can use, so the phone can create a session
        and chat.

        The normal /api/models route scopes to get_current_user, which for a
        bearer token is the sandboxed pseudo-user "api" (app.py) — that user owns
        no endpoints, so it would come back empty. Here we scope to the token's
        real owner (api_token_owner) instead, plus null-owner shared endpoints,
        mirroring the owner_filter rule used everywhere else. Read-only; never
        returns api_key material."""
        import json as _json

        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import build_chat_url

        owner = _require_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.is_enabled == True,  # noqa: E712
                (ModelEndpoint.model_type == "llm") | (ModelEndpoint.model_type == None),  # noqa: E711
            )
            q = q.filter((ModelEndpoint.owner == owner) | (ModelEndpoint.owner == None))  # noqa: E711
            for ep in q.all():
                try:
                    model_ids = _json.loads(ep.cached_models) if ep.cached_models else []
                except (ValueError, TypeError):
                    model_ids = []
                hidden = set()
                try:
                    hidden = set(_json.loads(ep.hidden_models)) if ep.hidden_models else set()
                except (ValueError, TypeError):
                    hidden = set()
                model_ids = [m for m in model_ids if m not in hidden]
                # Return the provider-correct chat URL (…/v1/chat/completions,
                # Ollama /api/chat, Anthropic /v1/messages), not the bare base —
                # the phone passes this straight to /api/session, which POSTs to
                # it verbatim. Falls back to base_url if resolution ever fails.
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

    @router.get("/notes")
    def notes(request: Request):
        """The TOKEN OWNER's notes (Google Keep-style notes / checklists).

        Same owner-scoping rationale as /models: bearer-token callers arrive as
        the pseudo-user "api", so we resolve the real owner and filter the notes
        table to rows they own (plus legacy null-owner shared rows). Read-only;
        skips archived notes. Degrades to {"items": []} on any error — never
        500s the phone."""
        import json as _json

        items = []
        try:
            from core.database import SessionLocal, Note

            owner = _require_owner(request)
            db = SessionLocal()
            try:
                q = db.query(Note).filter(Note.archived == False)  # noqa: E712
                q = q.filter((Note.owner == owner) | (Note.owner == None))  # noqa: E711
                rows = q.order_by(
                    Note.pinned.desc(), Note.sort_order.asc(), Note.updated_at.desc()
                ).all()
                for n in rows:
                    checklist = None
                    if n.items:
                        try:
                            checklist = _json.loads(n.items)
                        except (ValueError, TypeError):
                            checklist = None
                    items.append({
                        "id": n.id,
                        "title": n.title,
                        "content": n.content,
                        "items": checklist,
                        "pinned": bool(n.pinned),
                    })
            finally:
                db.close()
        except Exception:
            return {"items": []}
        return {"items": items}

    @router.get("/tasks")
    def tasks(request: Request):
        """The TOKEN OWNER's scheduled tasks.

        Owner-scoped like /models (real owner behind the bearer token, plus
        legacy null-owner rows). Read-only summary — id, name, schedule, enabled
        flag, last_run. Degrades to {"items": []} on any error."""
        items = []
        try:
            from core.database import SessionLocal, ScheduledTask

            owner = _require_owner(request)
            db = SessionLocal()
            try:
                q = db.query(ScheduledTask)
                q = q.filter(
                    (ScheduledTask.owner == owner) | (ScheduledTask.owner == None)  # noqa: E711
                )
                rows = q.order_by(ScheduledTask.created_at.desc()).all()
                for t in rows:
                    items.append({
                        "id": t.id,
                        "name": t.name,
                        # The DB stores the recurrence under `schedule` (daily/
                        # weekly/…) with the raw cron under `cron_expression`;
                        # surface the cron when that's the mode so the phone has
                        # a meaningful schedule string either way.
                        "schedule": t.cron_expression if t.schedule == "cron" else t.schedule,
                        "enabled": (t.status == "active"),
                        "last_run": _iso_utc(t.last_run),
                    })
            finally:
                db.close()
        except Exception:
            return {"items": []}
        return {"items": items}

    @router.get("/memory")
    def memory(request: Request):
        """The TOKEN OWNER's memories.

        Memories live in the `memories` SQL table (core.database.Memory),
        owner-scoped like everything else. Read-only — id, text, category.
        Degrades to {"items": []} on any error."""
        items = []
        try:
            from core.database import SessionLocal, Memory

            owner = _require_owner(request)
            db = SessionLocal()
            try:
                q = db.query(Memory)
                q = q.filter((Memory.owner == owner) | (Memory.owner == None))  # noqa: E711
                rows = q.order_by(Memory.timestamp.desc()).all()
                for m in rows:
                    items.append({
                        "id": m.id,
                        "text": m.text,
                        "category": m.category,
                    })
            finally:
                db.close()
        except Exception:
            return {"items": []}
        return {"items": items}

    # ---- Writes -----------------------------------------------------------
    # The reads above are the established pattern; these mirror it for the
    # phone's create/delete affordances. Each write resolves the real token
    # owner and stamps it on new rows, and every mutate/delete enforces strict
    # ownership (404 — never confirm existence to a non-owner), exactly like the
    # desktop note/memory routes. Writes go to the same SQL tables the matching
    # GET reads, so the mobile list stays self-consistent.

    @router.post("/memory")
    def add_memory(request: Request, text: str = Form(...), category: str = Form("fact")):
        """Create a memory owned by the token owner. Writes the `memories` table
        the GET /memory above reads, so it shows up immediately on the phone."""
        _require_companion_scope(request)
        owner = _require_owner(request)
        text = (text or "").strip()
        if not text:
            raise HTTPException(400, "empty memory")
        cat = category if category in _MEMORY_CATEGORIES else "fact"

        from core.database import SessionLocal, Memory

        db = SessionLocal()
        try:
            row = Memory(
                id=str(uuid.uuid4()),
                text=text,
                category=cat,
                source="mobile",
                owner=owner,
                timestamp=int(time.time()),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return {"id": row.id, "text": row.text, "category": row.category}
        finally:
            db.close()

    @router.delete("/memory/{memory_id}")
    def delete_memory(request: Request, memory_id: str):
        """Delete one of the token owner's memories."""
        _require_companion_scope(request)
        owner = _require_owner(request)

        from core.database import SessionLocal, Memory

        db = SessionLocal()
        try:
            row = db.query(Memory).filter(Memory.id == memory_id).first()
            if not row:
                raise HTTPException(404, "Memory not found")
            if row.owner != owner:
                raise HTTPException(404, "Memory not found")
            db.delete(row)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    def _serialize_note(n) -> dict:
        """Same shape the GET /notes endpoint returns."""
        checklist = None
        if n.items:
            try:
                checklist = _json.loads(n.items)
            except (ValueError, TypeError):
                checklist = None
        return {
            "id": n.id,
            "title": n.title,
            "content": n.content,
            "items": checklist,
            "pinned": bool(n.pinned),
        }

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
        _require_companion_scope(request)
        owner = _require_owner(request)

        checklist_json = None
        note_type = "note"
        if items:
            try:
                parsed = _json.loads(items)
            except (ValueError, TypeError):
                raise HTTPException(400, "items must be a JSON array")
            if not isinstance(parsed, list):
                raise HTTPException(400, "items must be a JSON array")
            # Normalise to the stored {text, done} shape.
            norm = [
                {"text": str(it.get("text", "")), "done": bool(it.get("done", False))}
                for it in parsed
                if isinstance(it, dict)
            ]
            checklist_json = _json.dumps(norm)
            note_type = "checklist"

        if not (title or "").strip() and not (content or "") and not checklist_json:
            raise HTTPException(400, "empty note")

        from core.database import SessionLocal, Note

        db = SessionLocal()
        try:
            note = Note(
                id=str(uuid.uuid4()),
                owner=owner,
                title=title or "",
                content=content,
                items=checklist_json,
                note_type=note_type,
                pinned=bool(pinned),
                source="mobile",
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return _serialize_note(note)
        finally:
            db.close()

    def _owned_note(db, note_id: str, owner):
        from core.database import Note

        note = db.query(Note).filter(Note.id == note_id).first()
        if not note:
            raise HTTPException(404, "Note not found")
        if note.owner != owner:
            raise HTTPException(404, "Note not found")
        return note

    @router.delete("/notes/{note_id}")
    def delete_note(request: Request, note_id: str):
        """Delete one of the token owner's notes."""
        _require_companion_scope(request)
        owner = _require_owner(request)
        from core.database import SessionLocal

        db = SessionLocal()
        try:
            note = _owned_note(db, note_id, owner)
            db.delete(note)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/notes/{note_id}/pin")
    def toggle_note_pin(request: Request, note_id: str):
        """Flip a note's pinned flag."""
        _require_companion_scope(request)
        owner = _require_owner(request)
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
        _require_companion_scope(request)
        owner = _require_owner(request)
        from core.database import SessionLocal
        from sqlalchemy.orm.attributes import flag_modified

        db = SessionLocal()
        try:
            note = _owned_note(db, note_id, owner)
            if not note.items:
                raise HTTPException(400, "Note has no checklist items")
            try:
                items = _json.loads(note.items)
            except (ValueError, TypeError):
                raise HTTPException(400, "Note has no checklist items")
            if index < 0 or index >= len(items):
                raise HTTPException(400, f"Item index {index} out of range")
            items[index]["done"] = not items[index].get("done", False)
            note.items = _json.dumps(items)
            flag_modified(note, "items")
            db.commit()
            return {"ok": True, "items": items}
        finally:
            db.close()

    @router.get("/pair")
    def pair(request: Request):
        """Admin-only pairing page. Open this in the browser where you're logged
        into Odysseus (e.g. http://localhost:7000/api/companion/pair), then scan
        the QR with the odysseus-mobile app.

        Minting happens in-process so we can invalidate the auth middleware's
        token cache immediately — the new token works on the very next request,
        no server restart needed. Returns a dependency-free HTML page (QR is a
        server-rendered <img>, no inline JS, so CSP stays happy)."""
        require_admin(request)
        owner = get_current_user(request)

        token_id, raw_token = _pairing.mint_token(owner)

        # The new token row is invisible to the middleware's in-memory cache
        # until we flip its dirty flag. Do that now so pairing is instant.
        invalidate = getattr(request.app.state, "invalidate_token_cache", None)
        if invalidate:
            invalidate()

        hosts = _pairing.lan_ip_candidates()
        host = hosts[0] if hosts else "127.0.0.1"
        port = request.url.port or _pairing.default_port()
        payload = _pairing.pairing_payload(host, port, raw_token)
        qr = _pairing.pairing_qr_png_data_uri(payload)

        import json as _json
        payload_json = _json.dumps(payload, separators=(",", ":"))
        alt_hosts = ", ".join(hosts[1:]) if len(hosts) > 1 else "—"

        # JSON variant for the in-app "Mobile App" settings tab, which renders
        # the QR itself. Same minting + cache-invalidation as the HTML page.
        if (request.query_params.get("format") or "").lower() == "json":
            return {
                "host": host,
                "port": port,
                "token": raw_token,
                "token_id": token_id,
                "hosts": hosts,
                "payload": payload,
                "qr": qr if (qr and qr.startswith("data:image/png;base64,")) else None,
            }
        # Only ever emit a known PNG data-URI into the src; never trust the
        # helper's output shape implicitly (defense in depth vs. the rest of the
        # page, which html.escapes every interpolation).
        qr_block = (
            f'<img src="{html.escape(qr)}" alt="Pairing QR" width="280" height="280">'
            if qr and qr.startswith("data:image/png;base64,")
            else "<p><em>QR rendering unavailable — enter the details manually.</em></p>"
        )
        page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair Odysseus Mobile</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 20px;color:#e8e8e8;background:#16161a}}
  .card{{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:24px;text-align:center}}
  code{{background:#0e0e12;padding:2px 6px;border-radius:6px;word-break:break-all}}
  .row{{text-align:left;margin:10px 0;font-size:14px;color:#bdbdc7}}
  .warn{{color:#e0a85e;font-size:13px;margin-top:18px}}
</style></head>
<body><div class="card">
  <h2>Pair Odysseus Mobile</h2>
  <p>Scan with the <strong>odysseus-mobile</strong> app, or type the details in.</p>
  {qr_block}
  <div class="row"><strong>Host:</strong> <code>{html.escape(host)}</code></div>
  <div class="row"><strong>Port:</strong> <code>{port}</code></div>
  <div class="row"><strong>Token:</strong> <code>{html.escape(raw_token)}</code></div>
  <div class="row"><strong>Other addresses:</strong> {html.escape(alt_hosts)}</div>
  <div class="row"><strong>Payload:</strong> <code>{html.escape(payload_json)}</code></div>
  <p class="warn">This token grants chat access to your Odysseus. It's shown once.
  Revoke it anytime in Settings &rarr; API tokens (id <code>{html.escape(token_id)}</code>).
  The phone must be on the same network, and the server must bind to your LAN
  (APP_BIND=0.0.0.0 / --host 0.0.0.0).</p>
</div></body></html>"""
        return HTMLResponse(page)

    return router
