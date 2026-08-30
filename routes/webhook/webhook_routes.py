"""Webhook, API Token, and sync chat routes."""

import uuid
import logging
import json
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Form
from pydantic import BaseModel, Field

from core.database import SessionLocal, Webhook, ModelEndpoint
from src.auth_helpers import (
    is_bearer_principal,
    owner_filter,
    request_capability,
    require_chat_scope,
)
from src.url_security import validate_public_http_url
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
    rows. Fails closed when token_owner is absent; the sync endpoint requires
    an owner-scoped bearer before this helper is reached.
    Does not validate base_url — admin-configured local/LAN endpoints remain allowed.
    """
    query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
    if not token_owner:
        return None
    query = owner_filter(query, ModelEndpoint, token_owner)
    return query.order_by(ModelEndpoint.owner.desc(), ModelEndpoint.created_at).first()


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
    """
    if not caller:
        return False
    return sess_owner == caller


def _cached_endpoint_model_ids(endpoint) -> list[str]:
    """Return model IDs already stored for a configured endpoint.

    The synchronous bearer integration may use a cached model or the provider's
    ``auto`` alias, but it must not turn an ordinary chat request into a remote
    catalog probe. Malformed/legacy cache shapes are treated as empty.
    """
    try:
        from routes.model_routes import _effective_endpoint_kind, _picker_models_for_endpoint

        base_url = getattr(endpoint, "base_url", "") or ""
        kind = _effective_endpoint_kind(endpoint, base_url)
        models, _ = _picker_models_for_endpoint(endpoint, base_url, kind)
        return models
    except Exception:
        raw = getattr(endpoint, "cached_models", None)
        pinned_raw = getattr(endpoint, "pinned_models", None)
        hidden_raw = getattr(endpoint, "hidden_models", None)
        if not raw and not pinned_raw:
            return []
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            pinned = json.loads(pinned_raw) if isinstance(pinned_raw, str) else pinned_raw
            hidden = json.loads(hidden_raw) if isinstance(hidden_raw, str) else hidden_raw
        except (TypeError, ValueError):
            return []
        if isinstance(value, dict):
            value = value.get("data") or value.get("models") or []
        if isinstance(pinned, dict):
            pinned = pinned.get("data") or pinned.get("models") or []
        if isinstance(hidden, dict):
            hidden = hidden.get("data") or hidden.get("models") or []
        if not isinstance(value, list):
            value = []
        if not isinstance(pinned, list):
            pinned = []
        if not isinstance(hidden, list):
            hidden = []
        raw_ids = value + pinned
        hidden_ids = {str(item).strip() for item in hidden if str(item).strip()}
        ids = []
        for item in raw_ids:
            if isinstance(item, str) and item.strip():
                model_id = item.strip()
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                if not isinstance(model_id, str) or not model_id.strip():
                    continue
                model_id = model_id.strip()
            else:
                continue
            if model_id not in hidden_ids and model_id not in ids:
                ids.append(model_id)
        return ids


def _validate_bearer_sync_model(endpoint, requested_model: str) -> str:
    """Validate a configured sync model without probing its provider."""
    try:
        from routes.model_routes import _validate_bearer_model_selection

        return _validate_bearer_model_selection(endpoint, requested_model)
    except ImportError:
        # Keep the lightweight webhook test/import seam usable when optional
        # route modules are deliberately stubbed. Production uses the
        # central picker validator above; this fallback remains cache-only.
        models = _cached_endpoint_model_ids(endpoint)
        requested = str(requested_model or "").strip()
        if requested and requested in models:
            return requested
        if not requested and models:
            return models[0]
        if (
            requested
            and not models
            and not getattr(endpoint, "cached_models", None)
            and not getattr(endpoint, "pinned_models", None)
            and "localhost" in str(getattr(endpoint, "base_url", "")).lower()
        ):
            return requested
        raise HTTPException(400, "Model is not permitted for this endpoint")


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

    class SyncChatRequest(BaseModel):
        message: str = Field(..., max_length=MAX_MESSAGE_LEN)
        model: Optional[str] = Field(None, max_length=200)
        session: Optional[str] = Field(None, max_length=100)
        api_key: Optional[str] = Field(None, max_length=256)
        base_url: Optional[str] = Field(None, max_length=MAX_URL_LEN)
        provider: Optional[str] = Field(None, max_length=50)

    @router.post("/v1/chat")
    async def sync_chat(request: Request, body: SyncChatRequest):
        if getattr(request.state, "api_token", False) is not True:
            raise HTTPException(403, "This endpoint requires an API token")
        token_owner = require_chat_scope(request)
        capability = request_capability(request)
        if not token_owner:
            raise HTTPException(403, "API token has no owner")

        from core.models import ChatMessage
        from src.llm_core import llm_call_async
        from src.endpoint_resolver import build_chat_url, build_headers, normalize_base

        message = body.message.strip()
        if not message:
            raise HTTPException(400, "Message is required")

        session_id = body.session
        sess = None

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

        # --- Case 2: Direct API key + model (no pre-configured endpoint needed) ---
        if not sess and body.api_key:
            api_key = body.api_key.strip()
            model = body.model or "deepseek-chat"

            # Validate only token-supplied direct base_url; auto-resolved known-provider
            # URLs are not subject to extra local/LAN blocking beyond existing provider logic.
            direct_base_url = body.base_url.strip().rstrip("/") if body.base_url else None
            if direct_base_url:
                try:
                    base_url = validate_public_http_url(direct_base_url)
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
            model = body.model or ""
            api_key = ep.api_key
            if getattr(ep, "provider_auth_id", None):
                try:
                    from src.endpoint_resolver import resolve_endpoint_runtime
                    runtime_kwargs = {}
                    if not capability.allow_live_probes:
                        runtime_kwargs["allow_live_probes"] = False
                    base_url, api_key = resolve_endpoint_runtime(
                        ep,
                        owner=token_owner,
                        **runtime_kwargs,
                    )
                    endpoint_url = build_chat_url(base_url)
                except Exception:
                    raise HTTPException(500, "Could not resolve endpoint credentials")

            # This route is bearer-only. Explicit and empty selections both
            # use the same server-owned, cache-only picker inventory; an empty
            # inventory is an error rather than an implicit provider alias.
            model = _validate_bearer_sync_model(ep, model)

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

        llm_kwargs = {}
        if not capability.allow_live_probes:
            llm_kwargs["allow_live_probes"] = False
        reply = await llm_call_async(
            sess.endpoint_url, sess.model, messages,
            headers=sess.headers, timeout=120,
            **llm_kwargs,
        )
        sess.add_message(ChatMessage("assistant", reply))
        session_manager.save_sessions()

        # /api/v1/chat remains a synchronous bearer integration: the response
        # is returned normally, but the token must not fan that content out to
        # an owner-configured asynchronous callback after authorization ends.
        if not is_bearer_principal(request):
            webhook_manager.fire_and_forget("chat.completed", {
                "session_id": session_id, "model": sess.model,
                "user_message": message[:2000], "response": reply[:2000],
            })

        return {"response": reply, "session_id": session_id, "model": sess.model}

    return router
