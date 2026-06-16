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
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing

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


def setup_companion_routes(research_handler=None) -> APIRouter:
    """Build the companion router.

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
  <p>Generate a one-time pairing code (a chat-scoped API token) for a LAN client.</p>
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

    return router
