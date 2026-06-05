"""Companion bridge — /api/companion/*.

A thin, additive layer so a LAN client (e.g. a phone) can discover what a server
offers and pair to it, without duplicating any LLM logic.

Auth is enforced globally by AuthMiddleware (app.py), so reaching a handler here
means the caller is authenticated by either a cookie session or a Bearer `ody_`
API token. The read endpoints (ping/info/models) accept either; the pairing
endpoints are admin-cookie only.

Pairing CSRF posture: minting happens ONLY on POST. The session cookie is
SameSite=Lax (routes/auth_routes.py), which a browser does not send on a
cross-site POST, so an admin's cookie can't be used by a malicious page to mint
a token -- the same protection the existing POST /api/tokens relies on. Minting
on a GET would be unsafe (Lax cookies ride top-level GET navigations), so GET
/pair only renders a form.
"""

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

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


_fallback_session_manager = None


def _resolve_session_manager(injected):
    """Use the app-shared SessionManager when wired in (so the live in-memory
    sessions match the owner's desktop UI), falling back to a lazily-built one
    for standalone/test contexts that call setup_companion_routes() with no
    args. Kept module-level so it can be monkeypatched in tests."""
    global _fallback_session_manager
    if injected is not None:
        return injected
    if _fallback_session_manager is None:
        from core.session_manager import SessionManager
        _fallback_session_manager = SessionManager()
    return _fallback_session_manager


def session_summary(sess, active: bool, status, public_model=None) -> dict:
    """Shape one session row for a companion client. Pure so it's unit-testable.

    `active`/`status` come from agent_runs (is_active/get_status) so the phone
    knows which sessions are still generating and can reconnect to the live
    stream without a probe request. `public_model` lets the caller blank a
    blind-compare session's real model (issue #1285); defaults to the stored
    model.
    """
    return {
        "id": sess.id,
        "name": sess.name,
        "model": sess.model if public_model is None else public_model,
        "message_count": getattr(sess, "message_count", 0) or 0,
        "is_important": bool(getattr(sess, "is_important", False)),
        "active": bool(active),
        "status": status,
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


def setup_companion_routes(session_manager=None) -> APIRouter:
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

    @router.get("/sessions")
    def list_sessions(request: Request):
        """The caller's own sessions, annotated with live run state.

        Owner-scoped via token_owner: a paired phone sees exactly the sessions
        the owner sees on the desktop UI -- never another user's. An unresolved
        owner (no cookie user, ownerless token) gets an EMPTY list, never the
        global set -- session_manager.get_sessions_for_user(None) returns ALL
        sessions, so we must not pass None through.

        Each row carries agent_runs is_active/get_status so the client knows
        which sessions are still generating and can reconnect to
        /sessions/{id}/stream without a probe request. Read-only; accepts a
        cookie session or a chat-scoped bearer token (same as /info, /models).
        """
        from src import agent_runs
        from routes.session_routes import _public_model, _HIDDEN_SYSTEM_SESSION_NAMES

        owner = token_owner(request)
        if not owner:
            return {"sessions": []}

        sm = _resolve_session_manager(session_manager)
        out = []
        for sess in sm.get_sessions_for_user(owner).values():
            if getattr(sess, "archived", False):
                continue
            name = (sess.name or "").strip()
            if name in ("Nobody", "Incognito") or name in _HIDDEN_SYSTEM_SESSION_NAMES:
                continue
            out.append(session_summary(
                sess,
                agent_runs.is_active(sess.id),
                agent_runs.get_status(sess.id),
                public_model=_public_model(sess.name, sess.model),
            ))
        # Live runs first so the phone surfaces in-flight work at the top; the
        # client is free to re-sort.
        out.sort(key=lambda s: not s["active"])
        return {"sessions": out}

    @router.post("/sessions")
    async def start_session(request: Request):
        """Start a NEW chat from the phone and return its session id.

        Creates an owner-scoped session, then kicks off the SAME detached run the
        desktop uses by making an internal loopback POST to /api/chat_stream. That
        handler calls agent_runs.start BEFORE it streams, so by the time we get
        response headers the run is registered and self-draining -- we close the
        loopback immediately (closing an SSE never stops a detached run) and the
        phone watches via /sessions/{id}/stream. We reuse the real chat pipeline
        (models, tools, reasoning, message saving) rather than duplicate it; no
        new LLM logic lives here.

        Body (JSON): {message, endpoint_id, model}. endpoint_id/model come from
        /api/companion/models. Owner-scoped: the session is owned by the token's
        real owner, so it shows in the caller's list and passes ownership checks.
        """
        import uuid as _uuid

        import httpx

        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Could not resolve an owner for this token")

        try:
            body = await request.json()
        except Exception:
            body = {}
        message = (body.get("message") or "").strip()
        model = (body.get("model") or "").strip()
        endpoint_id = (body.get("endpoint_id") or "").strip()
        if not message:
            raise HTTPException(400, "message is required")
        if not endpoint_id or not model:
            raise HTTPException(400, "endpoint_id and model are required")

        # Resolve the endpoint to a chat URL, owner-scoped (own or shared rows).
        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import build_chat_url, normalize_base
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == endpoint_id,
                ModelEndpoint.is_enabled == True,  # noqa: E712
            ).first()
            if ep is None or not owner_can_see(ep.owner, owner):
                raise HTTPException(404, "Model endpoint not found")
            endpoint_url = build_chat_url(normalize_base(ep.base_url))
        finally:
            db.close()

        # Create the (empty) session first; chat_stream adds the user message.
        sm = _resolve_session_manager(session_manager)
        sid = str(_uuid.uuid4())
        name = (message[:40] or "New chat").strip()
        sm.create_session(sid, name, endpoint_url, model, owner=owner)

        # Internal-tool loopback: trusted because it originates from 127.0.0.1
        # with no proxy headers; X-Odysseus-Owner attributes the run to the owner.
        from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
        port = request.url.port or _pairing.default_port()
        url = f"http://127.0.0.1:{port}/api/chat_stream"
        headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN, "X-Odysseus-Owner": owner}
        data = {"message": message, "session": sid}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                # Opening the stream sends the request and waits for headers; that
                # is enough to guarantee the run started. We never read the body.
                async with client.stream("POST", url, data=data, headers=headers) as resp:
                    if resp.status_code >= 400:
                        raise HTTPException(502, f"chat_stream returned {resp.status_code}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"could not start the run: {e}")

        return {"session_id": sid, "name": name}

    @router.get("/sessions/{session_id}/messages")
    def session_messages(request: Request, session_id: str):
        """Saved conversation for one session, so the phone can open ANY session
        -- not only one with a live run. Owner-checked. Returns user/assistant
        turns; reasoning is already stripped from saved messages (same as the
        desktop), so this is the finished answer text."""
        from routes.session_routes import _verify_session_owner, _content_to_text

        sm = _resolve_session_manager(session_manager)
        _verify_session_owner(request, session_id, sm)
        try:
            sess = sm.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        out = []
        for m in getattr(sess, "history", []) or []:
            role = getattr(m, "role", None)
            if role not in ("user", "assistant"):
                continue
            content = getattr(m, "content", "")
            if not isinstance(content, str):
                content = _content_to_text(content)
            out.append({"role": role, "content": content})
        return {"messages": out}

    @router.get("/sessions/{session_id}/stream")
    async def stream_session(request: Request, session_id: str):
        """Live SSE for one session -- the phone's "watch what it's doing" view.

        Thin wrapper over the SAME detached-run machinery the desktop UI uses
        (agent_runs.subscribe replays the buffer so far, then streams live), so
        a phone that connects mid-run picks up where it is, and disconnecting
        does NOT stop the run. Owner-checked via _verify_session_owner (which
        resolves the bearer token's real owner), so a paired phone can only
        watch its own sessions. 404 if no run is active for this session.
        """
        from src import agent_runs
        from routes.session_routes import _verify_session_owner

        _verify_session_owner(request, session_id, _resolve_session_manager(session_manager))
        if not agent_runs.is_active(session_id):
            raise HTTPException(404, "No active run for this session")
        return StreamingResponse(agent_runs.subscribe(session_id), media_type="text/event-stream")

    @router.post("/sessions/{session_id}/stop")
    async def stop_session(request: Request, session_id: str):
        """The phone's Stop/interrupt button. Cancels the detached run server-
        side (closing the SSE alone does not, by design). Owner-checked the same
        way as the stream. Returns {stopped: bool} -- false if nothing was
        running, which the client can treat as already-stopped."""
        from src import agent_runs
        from routes.session_routes import _verify_session_owner

        _verify_session_owner(request, session_id, _resolve_session_manager(session_manager))
        return {"stopped": agent_runs.stop(session_id)}

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

    return router
