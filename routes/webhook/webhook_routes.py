"""Webhook, API Token, and sync chat routes."""

import json
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Form
from pydantic import BaseModel, Field

from core.database import SessionLocal, Webhook, ModelEndpoint
from src.auth_helpers import owner_filter
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
    # Authenticated governed conversation. Never a completions gateway.
    # ================================================================

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
        from services.agents.legacy_bridge import stream_governed_agent
        from services.agents.session_binding import (
            DEFAULT_AGENT_PROFILE,
            SessionBinding,
            conversation_kwarg,
            normalize_agent_profile_id,
        )

        message = body.message.strip()
        if not message:
            raise HTTPException(400, "Message is required")
        if body.api_key or body.base_url:
            raise HTTPException(
                400,
                "Provider credentials are not accepted. This is not a completions gateway.",
            )
        session_id = body.session
        if not session_id:
            raise HTTPException(400, "Session is required")
        if not session_manager:
            raise HTTPException(500, "Session manager not available")

        try:
            sess = session_manager.get_session(session_id)
        except (KeyError, Exception):
            raise HTTPException(404, "Session not found")
        try:
            from src.auth_helpers import get_current_user as _gcu
            _tok_user = token_owner or getattr(request.state, "user", None) or _gcu(request)
        except Exception:
            _tok_user = None
        if not _caller_owns_session(getattr(sess, "owner", None), _tok_user):
            raise HTTPException(404, "Session not found")

        sess.add_message(ChatMessage("user", message))
        bound_cid = getattr(sess, "openhands_conversation_id", None)
        bound_profile = normalize_agent_profile_id(
            getattr(sess, "agent_profile_id", None)
        )
        reply_parts = []
        async for chunk in stream_governed_agent(
            messages=[{"role": "user", "content": message}],
            session_id=session_id,
            history_session=sess,
            owner=_tok_user,
            conversation_id=conversation_kwarg(
                session_id,
                SessionBinding(
                    conversation_id=bound_cid,
                    agent_profile_id=bound_profile or DEFAULT_AGENT_PROFILE,
                ),
            ),
            turn_id=uuid.uuid4().hex,
            agent_profile_id=bound_profile or DEFAULT_AGENT_PROFILE,
            bound_agent_profile_id=bound_profile,
            archetype="chat",
        ):
            if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
                continue
            try:
                data = json.loads(chunk[6:])
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") == "execution" and data.get("conversation_id"):
                sess.openhands_conversation_id = data.get("conversation_id")
                sess.agent_profile_id = bound_profile or DEFAULT_AGENT_PROFILE
                continue
            if data.get("thinking"):
                continue
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                reply_parts.append(delta)
        reply = "".join(reply_parts)
        sess.add_message(ChatMessage("assistant", reply))
        session_manager.save_sessions()

        webhook_manager.fire_and_forget("chat.completed", {
            "session_id": session_id, "model": getattr(sess, "model", None),
            "user_message": message[:2000], "response": reply[:2000],
        })

        return {"response": reply, "session_id": session_id, "model": getattr(sess, "model", None)}

    return router
