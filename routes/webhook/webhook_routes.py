"""Webhook, API Token, and sync chat routes."""

import uuid
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Form
from pydantic import BaseModel, Field

from core.database import SessionLocal, Webhook, ModelEndpoint
from src.auth_helpers import owner_filter
from src.url_security import resolve_public_http_url
from src.webhook_manager import WebhookManager, validate_webhook_url, validate_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["webhooks"])

# Input limits
MAX_NAME_LEN = 100
MAX_URL_LEN = 2048
MAX_SECRET_LEN = 256
MAX_MESSAGE_LEN = 32_000


from core.middleware import require_admin as _require_admin


def _select_api_chat_fallback_endpoint(db, token_owner: Optional[str]):
    """First enabled ModelEndpoint visible to token_owner — their own rows plus
    legacy null-owner ("shared") rows. Owner-scoped: an unscoped .first() would
    let a chat-scoped token fall back onto another user's private endpoint and
    silently spend that owner's API key/quota. Prefer owner rows before shared
    rows. Fails closed to null-owner rows only when token_owner is absent.
    Does not validate base_url — admin-configured local/LAN endpoints remain allowed.
    """
    query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
    if token_owner:
        query = owner_filter(query, ModelEndpoint, token_owner)
        return query.order_by(ModelEndpoint.owner.desc(), ModelEndpoint.created_at).first()
    return query.filter(ModelEndpoint.owner == None).order_by(ModelEndpoint.created_at).first()  # noqa: E711


def _pinned_llm_client(ips) -> httpx.AsyncClient:
    """AsyncClient that connects only to ``ips[0]``, the address that passed the
    SSRF check. Host and TLS SNI still come from the request URL, so the
    provider's certificate is verified against its hostname. Redirects and
    environment proxies are off: either would send the request somewhere the
    check never saw."""
    from src.webhook_manager import _PinnedAsyncTransport

    return httpx.AsyncClient(
        transport=_PinnedAsyncTransport(ips[0]),
        follow_redirects=False,
        trust_env=False,
    )


def _caller_owns_session(sess_owner, caller) -> bool:
    """Strict session-ownership gate for the token-authenticated sync-chat
    endpoint (`POST /api/v1/chat`).

    Mirrors ``_verify_session_owner`` in session_routes.py and the null-owner
    gates in notes/calendar/gallery: a caller may resume a session ONLY when
    its owner matches them exactly. A null/empty session owner (legacy or
    migrated rows) is deliberately NOT resumable by an arbitrary token — the
    old ``sess_owner and sess_owner != caller`` form skipped the check whenever
    ``sess_owner`` was falsy, so any chat-scoped token (e.g. a paired mobile
    device) could resume such a session, inject a message, and read back its
    history and reuse the owner's endpoint credentials. Fail closed: an
    unresolvable caller also returns False.

    A session started with a direct ``api_key`` needs ``api_key`` on every
    turn: the key is used for that request only and is never stored, so a
    resume without it reaches the provider with no key. A resume ``api_key``
    is applied to the session's own endpoint; ``base_url`` and ``provider``
    cannot change it.
    """
    if not caller:
        return False
    return sess_owner == caller


def setup_webhook_routes(
    webhook_manager: WebhookManager,
    auth_manager,
    session_manager=None,
    api_key_manager=None,
) -> APIRouter:

    @router.get("/webhooks")
    def list_webhooks(request: Request):
        _require_admin(request)
        db = SessionLocal()
        try:
            hooks = db.query(Webhook).all()
            return [
                {
                    "id": w.id,
                    "name": w.name,
                    "url": w.url,
                    "has_secret": bool(w.secret),
                    "events": w.events.split(",") if w.events else [],
                    "is_active": w.is_active,
                    "last_triggered_at": w.last_triggered_at.isoformat() if w.last_triggered_at else None,
                    "last_status_code": w.last_status_code,
                    "last_error": w.last_error,
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                }
                for w in hooks
            ]
        finally:
            db.close()

    @router.post("/webhooks")
    def create_webhook(
        request: Request,
        name: str = Form(""),
        url: str = Form(""),
        secret: str = Form(""),
        events: str = Form(""),
    ):
        _require_admin(request)
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            raise HTTPException(400, "Webhook name is required")
        try:
            url = validate_webhook_url(url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            events = validate_events(events)
        except ValueError as e:
            raise HTTPException(400, str(e))

        secret_val = secret.strip()[:MAX_SECRET_LEN] or None
        # Encrypt the secret at rest using the same Fernet key as API keys
        encrypted_secret = None
        if secret_val and api_key_manager:
            encrypted_secret = api_key_manager.encrypt_api_key(secret_val)
        elif secret_val:
            encrypted_secret = secret_val  # Fallback if no encryption available

        webhook_id = str(uuid.uuid4())[:8]
        db = SessionLocal()
        try:
            db.add(Webhook(
                id=webhook_id,
                name=name,
                url=url,
                secret=encrypted_secret,
                events=events,
                is_active=True,
            ))
            db.commit()
        finally:
            db.close()

        return {"id": webhook_id, "name": name}

    @router.post("/webhooks/{webhook_id}/test")
    async def test_webhook(request: Request, webhook_id: str):
        _require_admin(request)
        db = SessionLocal()
        try:
            wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            if not wh:
                raise HTTPException(404, "Webhook not found")
            url, secret = wh.url, wh.secret
        finally:
            db.close()

        await webhook_manager.deliver_test(webhook_id, url, secret)
        return {"status": "sent"}

    @router.patch("/webhooks/{webhook_id}")
    def toggle_webhook(request: Request, webhook_id: str):
        _require_admin(request)
        db = SessionLocal()
        try:
            wh = db.query(Webhook).filter(Webhook.id == webhook_id).first()
            if not wh:
                raise HTTPException(404, "Webhook not found")
            wh.is_active = not wh.is_active
            db.commit()
            return {"id": webhook_id, "is_active": wh.is_active}
        finally:
            db.close()

    @router.delete("/webhooks/{webhook_id}")
    def delete_webhook(request: Request, webhook_id: str):
        _require_admin(request)
        db = SessionLocal()
        try:
            deleted = db.query(Webhook).filter(Webhook.id == webhook_id).delete()
            db.commit()
            if not deleted:
                raise HTTPException(404, "Webhook not found")
        finally:
            db.close()
        return {"status": "deleted"}

    # ================================================================
    # Sync Chat Endpoint (for n8n / Make / Activepieces)
    # ================================================================

    # Known provider base URLs — auto-resolved from api_key prefix or model name
    KNOWN_PROVIDERS = {
        "deepseek": "https://api.deepseek.com/v1",
        "openai": "https://api.openai.com/v1",
        "mistral": "https://api.mistral.ai/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "ollama": "https://ollama.com/api",
        "opencode-zen": "https://opencode.ai/zen/v1",
        "opencode-go": "https://opencode.ai/zen/go/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "venice": "https://api.venice.ai/api/v1",
        "kimi-code": "https://api.kimi.com/coding/v1",
        "kimicode": "https://api.kimi.com/coding/v1",
    }

    # Model prefix → provider mapping for auto-detection
    MODEL_PROVIDER_MAP = {
        "deepseek": "deepseek",
        "gpt-": "openai",
        "o1": "openai",
        "o3": "openai",
        "o4": "openai",
        "mistral": "mistral",
        "llama": "groq",
        "mixtral": "groq",
        "kimi-for-coding": "kimi-code",
        "kimi": "kimi-code",
    }

    def _resolve_base_url(model: Optional[str], provider: Optional[str]) -> Optional[str]:
        """Try to auto-resolve a base URL from provider name or model prefix."""
        if provider and provider.lower() in KNOWN_PROVIDERS:
            return KNOWN_PROVIDERS[provider.lower()]
        if model:
            model_lower = model.lower()
            for prefix, prov in MODEL_PROVIDER_MAP.items():
                if model_lower.startswith(prefix):
                    return KNOWN_PROVIDERS[prov]
        return None

    def _resumed_endpoint_is_trusted(endpoint_url: Optional[str], owner: Optional[str]) -> bool:
        """True when a resumed session's endpoint is one the server chose rather
        than one a token supplied: a KNOWN_PROVIDERS URL, or a ModelEndpoint the
        owner can see (admin-configured, so local/LAN targets stay allowed).
        Sessions have no column recording where their URL came from, so any
        other URL is treated as token-supplied and re-checked on every resume."""
        from src.endpoint_resolver import build_chat_url, normalize_base

        if not endpoint_url:
            return False
        if endpoint_url in {build_chat_url(normalize_base(u)) for u in KNOWN_PROVIDERS.values()}:
            return True
        db = SessionLocal()
        try:
            query = db.query(ModelEndpoint)
            if owner:
                query = owner_filter(query, ModelEndpoint, owner)
            else:
                query = query.filter(ModelEndpoint.owner == None)  # noqa: E711
            for ep in query.all():
                if ep.base_url and build_chat_url(normalize_base(ep.base_url)) == endpoint_url:
                    return True
        finally:
            db.close()
        return False

    class SyncChatRequest(BaseModel):
        message: str = Field(..., max_length=MAX_MESSAGE_LEN)
        model: Optional[str] = Field(None, max_length=200)
        session: Optional[str] = Field(None, max_length=100)
        api_key: Optional[str] = Field(None, max_length=256)
        base_url: Optional[str] = Field(None, max_length=MAX_URL_LEN)
        provider: Optional[str] = Field(None, max_length=50)

    @router.post("/v1/chat")
    async def sync_chat(request: Request, body: SyncChatRequest):
        if not getattr(request.state, "api_token", False):
            raise HTTPException(403, "This endpoint requires an API token")
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if "chat" not in scopes:
            raise HTTPException(403, "API token is not scoped for chat")
        token_owner = getattr(request.state, "api_token_owner", None)

        from core.models import ChatMessage
        from src.llm_core import llm_call_async
        from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, normalize_base

        message = body.message.strip()
        if not message:
            raise HTTPException(400, "Message is required")

        session_id = body.session
        sess = None
        # Addresses a token-supplied endpoint resolved to when it was checked;
        # when set, every connection for this request is pinned to them.
        pinned_ips = None

        # --- Case 1: Resume an existing session ---
        if session_id and session_manager:
            try:
                sess = session_manager.get_session(session_id)
            except (KeyError, Exception):
                raise HTTPException(404, "Session not found")
            # SECURITY: verify the API-token's user owns this session — without
            # this any token holder could resume any user's chat by passing its
            # ID. The token's user is on request.state.user (set by API-token
            # middleware); fall back to require_user if not present.
            try:
                from src.auth_helpers import get_current_user as _gcu
                _tok_user = token_owner or getattr(request.state, "user", None) or _gcu(request)
            except Exception:
                _tok_user = None
            # Strict ownership (see _caller_owns_session): fail closed so a
            # null-owner / cross-owner session can't be resumed by an arbitrary
            # chat-scoped token.
            _sess_owner = getattr(sess, "owner", None)
            if not _caller_owns_session(_sess_owner, _tok_user):
                raise HTTPException(404, "Session not found")
            # A session created from a token-supplied base_url keeps that URL;
            # its DNS may now point somewhere private, so check it again.
            if not _resumed_endpoint_is_trusted(sess.endpoint_url, _tok_user):
                try:
                    _, pinned_ips = resolve_public_http_url(sess.endpoint_url or "")
                except ValueError:
                    raise HTTPException(
                        400, "Session endpoint must point to a public HTTP(S) endpoint"
                    )
            # Session headers are never written to the DB, and get_session()
            # refreshes them from it, so a session started with a direct
            # api_key has no key on later turns. Take the key again for this
            # request only. Headers come from the session's own endpoint;
            # base_url and provider are ignored on resume.
            resume_key = (body.api_key or "").strip()
            if resume_key:
                sess.headers = build_headers(resume_key, normalize_base(sess.endpoint_url or ""))

        # --- Case 2: Direct API key + model (no pre-configured endpoint needed) ---
        if not sess and body.api_key:
            api_key = body.api_key.strip()
            model = body.model or "deepseek-chat"

            # Validate only token-supplied direct base_url; auto-resolved known-provider
            # URLs are not subject to extra local/LAN blocking beyond existing provider logic.
            direct_base_url = body.base_url.strip().rstrip("/") if body.base_url else None
            if direct_base_url:
                try:
                    base_url, pinned_ips = resolve_public_http_url(direct_base_url)
                except ValueError as e:
                    detail = str(e).replace("URL", "base_url", 1)
                    raise HTTPException(400, detail)
            else:
                base_url = _resolve_base_url(model, body.provider)
            if not base_url:
                raise HTTPException(400,
                    "Could not auto-detect provider. Pass base_url (e.g. 'https://api.deepseek.com/v1') "
                    "or provider ('deepseek', 'openai', 'groq', etc.)")
            base_url = normalize_base(base_url)
            endpoint_url = build_chat_url(base_url)

            if not session_manager:
                raise HTTPException(500, "Session manager not available")

            sid = str(uuid.uuid4())
            sess = session_manager.create_session(
                session_id=sid, name="API Chat", endpoint_url=endpoint_url,
                model=model, owner=token_owner,
            )
            sess.headers = build_headers(api_key, base_url)
            session_manager.save_sessions()
            session_id = sid

        # --- Case 3: Fall back to first configured ModelEndpoint ---
        if not sess:
            db = SessionLocal()
            try:
                ep = _select_api_chat_fallback_endpoint(db, token_owner)
            finally:
                db.close()

            if not ep:
                raise HTTPException(400,
                    "No session, api_key, or configured endpoints. "
                    "Pass api_key + model, or configure an endpoint in Admin.")

            base_url = normalize_base(ep.base_url)
            endpoint_url = build_chat_url(base_url)
            model = body.model or "auto"
            api_key = ep.api_key
            if getattr(ep, "provider_auth_id", None):
                try:
                    from src.endpoint_resolver import resolve_endpoint_runtime
                    base_url, api_key = resolve_endpoint_runtime(ep, owner=token_owner)
                    endpoint_url = build_chat_url(base_url)
                except Exception:
                    raise HTTPException(500, "Could not resolve endpoint credentials")

            if model == "auto":
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        models_url = build_models_url(base_url)
                        hdrs = build_headers(api_key, base_url)
                        if models_url:
                            resp = await client.get(models_url, headers=hdrs)
                            resp.raise_for_status()
                            data = resp.json()
                            items = data if isinstance(data, list) else (data.get("data") or [])
                            ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
                            if not ids and isinstance(data, dict):
                                ids = [
                                    m.get("name") or m.get("model")
                                    for m in (data.get("models") or [])
                                    if m.get("name") or m.get("model")
                                ]
                        else:
                            import json as _json
                            ids = _json.loads(ep.cached_models or "[]")
                        model = ids[0] if ids else "auto"
                except Exception:
                    raise HTTPException(500, "Could not discover models from endpoint")

            if not session_manager:
                raise HTTPException(500, "Session manager not available")

            sid = str(uuid.uuid4())
            sess = session_manager.create_session(
                session_id=sid, name="API Chat", endpoint_url=endpoint_url,
                model=model, owner=token_owner,
            )
            if api_key:
                sess.headers = build_headers(api_key, base_url)
                session_manager.save_sessions()
            session_id = sid

        # --- Send message and get response ---
        sess.add_message(ChatMessage("user", message))

        messages = [{"role": m.role, "content": m.content} for m in sess.history]

        pinned_client = _pinned_llm_client(pinned_ips) if pinned_ips else None
        try:
            reply = await llm_call_async(
                sess.endpoint_url, sess.model, messages,
                headers=sess.headers, timeout=120,
                pinned_client=pinned_client,
            )
        finally:
            if pinned_client is not None:
                await pinned_client.aclose()
        sess.add_message(ChatMessage("assistant", reply))
        session_manager.save_sessions()

        webhook_manager.fire_and_forget("chat.completed", {
            "session_id": session_id, "model": sess.model,
            "user_message": message[:2000], "response": reply[:2000],
        })

        return {"response": reply, "session_id": session_id, "model": sess.model}

    return router
