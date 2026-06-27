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
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing

COMPANION_CONTRACT_VERSION = 1


def require_companion_scope(request: Request) -> None:
    """Require the chat scope for bearer-token companion callers."""
    if not getattr(request.state, "api_token", False):
        return
    scopes = getattr(request.state, "api_token_scopes", None) or []
    if isinstance(scopes, str):
        scopes = [scope.strip() for scope in scopes.split(",")]
    scope_set = {str(scope).strip() for scope in scopes if str(scope).strip()}
    if _pairing.COMPANION_SCOPE not in scope_set:
        raise HTTPException(403, "API token requires chat scope")


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


def require_companion_owner(request: Request) -> str:
    """Require a resolved owner for companion model/session actions."""
    require_companion_scope(request)
    owner = token_owner(request)
    if not owner:
        raise HTTPException(401, "Companion owner could not be resolved")
    return owner


def owner_can_see(row_owner, owner) -> bool:
    """Owner-scope rule for read endpoints.

    A caller sees a row when it is their own, or when it is a legacy null-owner
    ("shared") row. A caller must NEVER see another owner's row. Mirrors the
    `owner_filter` rule used elsewhere, expressed as a pure predicate so it can
    be tested directly and used as a defensive in-Python check alongside the
    SQL filter.
    """
    return row_owner is None or row_owner == owner


def companion_manifest(request: Request) -> dict:
    """Stable discovery contract for thin private companion clients."""
    from core.constants import APP_VERSION

    api_token = bool(getattr(request.state, "api_token", False))
    token_scopes = getattr(request.state, "api_token_scopes", []) or []
    return {
        "name": "odysseus",
        "version": APP_VERSION,
        "contract_version": COMPANION_CONTRACT_VERSION,
        "owner": token_owner(request),
        "auth": {
            "mode": "token" if api_token else "session",
            "required_bearer_scope": _pairing.COMPANION_SCOPE,
            "token_scopes": sorted({str(scope) for scope in token_scopes})
            if api_token
            else [],
            "pairing": {
                "method": "admin_cookie_post",
                "path": "/api/companion/pair",
                "payload_version": _pairing.PAIRING_VERSION,
                "scopes": [_pairing.COMPANION_SCOPE],
            },
        },
        "endpoints": {
            "ping": {"method": "GET", "path": "/api/companion/ping"},
            "info": {"method": "GET", "path": "/api/companion/info"},
            "manifest": {"method": "GET", "path": "/api/companion/manifest"},
            "models": {"method": "GET", "path": "/api/companion/models"},
            "sessions": {"method": "GET", "path": "/api/companion/sessions"},
            "create_session": {"method": "POST", "path": "/api/companion/sessions"},
            "chat_stream": {"method": "POST", "path": "/api/chat_stream"},
            "chat_resume": {"method": "GET", "path": "/api/chat/resume/{session_id}"},
            "chat_stop": {"method": "POST", "path": "/api/chat/stop/{session_id}"},
            "chat_stream_status": {
                "method": "GET",
                "path": "/api/chat/stream_status/{session_id}",
            },
        },
        "features": {
            "chat": {
                "available": True,
                "streaming": True,
                "required_bearer_scope": _pairing.COMPANION_SCOPE,
                "request_body": "multipart_form_data",
            },
            "models": {"read": True},
            "sessions": {"list": True, "create": True},
        },
    }


def _companion_session_manager():
    from core import models as core_models

    manager = getattr(core_models, "_session_manager", None)
    if manager is None:
        raise HTTPException(503, "Session manager is not ready")
    return manager


def _json_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item.strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str) and item.strip()]


def _endpoint_model_ids(endpoint) -> list[str]:
    hidden = set(_json_list(getattr(endpoint, "hidden_models", None)))
    pinned = _json_list(getattr(endpoint, "pinned_models", None))
    cached = _json_list(getattr(endpoint, "cached_models", None))
    out: list[str] = []
    for model_id in [*pinned, *cached]:
        if model_id in hidden or model_id in out:
            continue
        out.append(model_id)
    return out


def _session_summary(session) -> dict:
    return {
        "id": session.id,
        "name": session.name,
        "model": session.model,
        "endpoint_url": session.endpoint_url,
        "rag": bool(session.rag),
        "archived": bool(session.archived),
        "message_count": int(getattr(session, "message_count", 0) or 0),
    }


def _persist_companion_session_headers(session_id: str, headers: dict | None) -> None:
    from core.database import Session as DbSession, SessionLocal

    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == session_id).first()
        if row:
            row.headers = headers or {}
            row.updated_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _pick_companion_session_endpoint(*, owner: str, endpoint_id: str, model: str):
    from core.database import ModelEndpoint, SessionLocal
    from src.endpoint_resolver import build_chat_url, normalize_base

    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(
            ModelEndpoint.is_enabled == True,  # noqa: E712
            (ModelEndpoint.model_type == "llm") | (ModelEndpoint.model_type == None),  # noqa: E711
        )
        if owner:
            q = q.filter((ModelEndpoint.owner == owner) | (ModelEndpoint.owner == None))  # noqa: E711
        if endpoint_id:
            q = q.filter(ModelEndpoint.id == endpoint_id)
        for endpoint in q.all():
            if not owner_can_see(getattr(endpoint, "owner", None), owner):
                continue
            models = _endpoint_model_ids(endpoint)
            selected_model = model.strip() if model else ""
            if selected_model:
                if models and selected_model not in models:
                    continue
            elif models:
                selected_model = models[0]
            else:
                continue
            base_url = normalize_base(endpoint.base_url or "")
            return {
                "endpoint_id": endpoint.id,
                "endpoint_url": build_chat_url(base_url),
                "endpoint_base_url": base_url,
                "model": selected_model,
                "api_key": endpoint.api_key or "",
            }
    finally:
        db.close()
    return None


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
        require_companion_scope(request)
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
        require_companion_scope(request)
        from core.constants import APP_VERSION
        return {
            "name": "odysseus",
            "version": APP_VERSION,
            "owner": token_owner(request),
            "capabilities": {"chat": True, "streaming": True},
            "client_contract": {
                "version": COMPANION_CONTRACT_VERSION,
                "manifest": "/api/companion/manifest",
            },
        }

    @router.get("/manifest")
    def manifest(request: Request):
        """Machine-readable contract for private mobile/PWA companion clients."""
        require_companion_scope(request)
        return companion_manifest(request)

    @router.get("/models")
    def models(request: Request):
        """LLM model endpoints the CALLER can use.

        The stock /api/models route scopes to get_current_user, which for a
        bearer token is the sandboxed pseudo-user "api" (owns nothing). Here we
        scope to the token's real owner instead, plus legacy null-owner shared
        rows -- the same rule as owner_filter. Read-only; never returns api_key
        material.
        """
        require_companion_scope(request)
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
        """List the caller's mobile-visible chat sessions."""
        owner = require_companion_owner(request)
        manager = _companion_session_manager()
        sessions = manager.get_sessions_for_user(owner)
        return {
            "sessions": [
                _session_summary(session)
                for session in sessions.values()
                if not getattr(session, "archived", False)
            ]
        }

    @router.post("/sessions")
    def create_session(request: Request, body: dict = Body(default_factory=dict)):
        """Create a chat session from an owner-visible saved model endpoint."""
        owner = require_companion_owner(request)
        manager = _companion_session_manager()
        body = body or {}
        name = str(body.get("name") or "Companion").strip()[:120] or "Companion"
        endpoint_id = str(body.get("endpoint_id") or "").strip()
        model = str(body.get("model") or "").strip()
        rag = bool(body.get("rag", False))
        selected = _pick_companion_session_endpoint(
            owner=owner,
            endpoint_id=endpoint_id,
            model=model,
        )
        if not selected:
            raise HTTPException(400, "No owner-visible companion model endpoint found")

        sid = str(uuid.uuid4())
        session = manager.create_session(
            session_id=sid,
            name=name,
            endpoint_url=selected["endpoint_url"],
            model=selected["model"],
            rag=rag,
            owner=owner,
        )
        if selected["api_key"]:
            from src.endpoint_resolver import build_headers

            session.headers = build_headers(
                selected["api_key"],
                selected["endpoint_base_url"],
            )
            _persist_companion_session_headers(sid, session.headers)

        return {
            "session": _session_summary(session),
            "endpoint_id": selected["endpoint_id"],
        }

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
