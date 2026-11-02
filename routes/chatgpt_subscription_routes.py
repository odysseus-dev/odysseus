"""ChatGPT Subscription device-flow setup, multi-account and usage routes.

One Odysseus owner may connect several independent ChatGPT subscriptions. Each
connection is its own ``ProviderAuthSession`` + ``ModelEndpoint`` pair; the
endpoint id decides which account a request is billed to. Labels are cosmetic
only — stable ids drive lookup, reconnect, deletion and usage reads.
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, Response

from core.database import ModelEndpoint, ProviderAuthSession, SessionLocal, utcnow_naive
from core.middleware import require_admin
from routes.device_flow import (
    DeviceFlowPoll,
    DeviceFlowStart,
    PendingDeviceFlowStore,
    create_device_flow_router,
)
from src.auth_helpers import effective_user, get_current_user
from src import chatgpt_subscription

logger = logging.getLogger(__name__)

_DEVICE_FLOW_STORE = PendingDeviceFlowStore()

_PROVIDER = chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER


def _owner_scope(query, model_cls, owner: Optional[str]):
    return query.filter(model_cls.owner == owner)


def _owner_chatgpt_auths(db, owner: Optional[str]):
    q = db.query(ProviderAuthSession).filter(ProviderAuthSession.provider == _PROVIDER)
    return _owner_scope(q, ProviderAuthSession, owner).order_by(ProviderAuthSession.created_at).all()


def _endpoints_for_auth(db, auth_id: str):
    return db.query(ModelEndpoint).filter(ModelEndpoint.provider_auth_id == auth_id).all()


def _display_label(auth, ep) -> str:
    """Display label from persisted metadata (never from credentials)."""
    label = chatgpt_subscription.account_label_from_name(getattr(auth, "label", None))
    if not label and ep is not None:
        label = chatgpt_subscription.account_label_from_name(getattr(ep, "name", None))
    return label


def _assert_label_available(db, owner: Optional[str], label: str, *, exclude_auth_id: Optional[str] = None) -> None:
    """Reject a label already used by another ChatGPT account of this owner."""
    if not label:
        return
    for auth in _owner_chatgpt_auths(db, owner):
        if exclude_auth_id and auth.id == exclude_auth_id:
            continue
        existing = _display_label(auth, None)
        if not existing:
            for ep in _endpoints_for_auth(db, auth.id):
                existing = _display_label(auth, ep)
                if existing:
                    break
        if chatgpt_subscription.labels_conflict(existing, label):
            raise ValueError(f"A ChatGPT subscription labelled '{label}' is already connected.")


def _default_new_label(db, owner: Optional[str]) -> str:
    """Label for a new connection when the user did not supply one.

    The first account keeps the legacy unlabelled name so existing single
    account setups look unchanged; later accounts get a distinguishable
    ``account N`` label that is unique for this owner.
    """
    existing = _owner_chatgpt_auths(db, owner)
    if not existing:
        return ""
    taken = set()
    for auth in existing:
        label = _display_label(auth, None)
        if not label:
            for ep in _endpoints_for_auth(db, auth.id):
                label = _display_label(auth, ep)
                if label:
                    break
        if label:
            taken.add(label.casefold())
    n = len(existing) + 1
    while f"account {n}".casefold() in taken:
        n += 1
    return f"account {n}"


def _new_id(db, model_cls) -> str:
    for _ in range(8):
        candidate = str(uuid.uuid4())[:8]
        if db.query(model_cls).filter(model_cls.id == candidate).first() is None:
            return candidate
    return uuid.uuid4().hex[:12]


def _provision_endpoint(
    tokens: Dict,
    owner: Optional[str],
    *,
    label: str = "",
    reconnect_auth_id: Optional[str] = None,
    reconnect_endpoint_id: Optional[str] = None,
) -> Dict:
    """Create a new ChatGPT account (auth + endpoint) or refresh exactly one.

    Without ``reconnect_auth_id`` a brand-new ``ProviderAuthSession`` and
    ``ModelEndpoint`` are created even when the owner already has other ChatGPT
    subscriptions. With it, only that owner-scoped auth row (and its endpoint)
    is updated; every other account is left untouched.
    """
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token or not refresh_token:
        raise ValueError("ChatGPT token response was missing access_token or refresh_token")

    base = chatgpt_subscription.DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL
    models = chatgpt_subscription.fetch_available_models(access_token)
    if not models:
        raise ValueError("ChatGPT Subscription connected, but no usable Codex models were discovered for this account.")
    label = chatgpt_subscription.normalize_account_label(label)
    db = SessionLocal()
    try:
        auth = None
        if reconnect_auth_id:
            auth = chatgpt_subscription.find_owned_auth_session(db, reconnect_auth_id, owner)
            if auth is None:
                raise chatgpt_subscription.ChatGPTSubscriptionAuthNotFound(
                    "The ChatGPT subscription being reconnected no longer exists for this user."
                )
            # A reconnect keeps the existing label unless a new one was given.
            if label:
                _assert_label_available(db, owner, label, exclude_auth_id=auth.id)
        else:
            if not label:
                label = _default_new_label(db, owner)
            _assert_label_available(db, owner, label)
            auth = ProviderAuthSession(
                id=_new_id(db, ProviderAuthSession),
                provider=_PROVIDER,
                owner=owner,
                label=chatgpt_subscription.endpoint_name_for_label(label),
                base_url=base,
                auth_mode="chatgpt",
            )
            db.add(auth)
        auth.base_url = base
        auth.access_token = access_token
        auth.refresh_token = refresh_token
        auth.last_refresh = utcnow_naive()
        auth.auth_mode = "chatgpt"
        if label:
            auth.label = chatgpt_subscription.endpoint_name_for_label(label)

        ep = None
        if reconnect_auth_id:
            ep_q = db.query(ModelEndpoint).filter(ModelEndpoint.provider_auth_id == auth.id)
            ep_q = _owner_scope(ep_q, ModelEndpoint, owner)
            if reconnect_endpoint_id:
                ep = ep_q.filter(ModelEndpoint.id == reconnect_endpoint_id).first()
                if ep is None:
                    raise chatgpt_subscription.ChatGPTSubscriptionAuthNotFound(
                        "The ChatGPT subscription endpoint no longer exists for this user."
                    )
            else:
                ep = ep_q.order_by(ModelEndpoint.created_at).first()
        if ep is None:
            ep = ModelEndpoint(
                id=_new_id(db, ModelEndpoint),
                name=chatgpt_subscription.endpoint_name_for_label(label),
                base_url=base,
                model_type="llm",
                endpoint_kind="api",
                owner=owner,
            )
            db.add(ep)
        if label or not (ep.name or "").strip():
            ep.name = chatgpt_subscription.endpoint_name_for_label(label)
        ep.base_url = base
        ep.api_key = None
        ep.provider_auth_id = auth.id
        ep.is_enabled = True
        # ChatGPT provides inference only. Odysseus is the only agent: no
        # provider-native tool schemas are ever sent on this route.
        ep.supports_tools = False
        ep.model_type = "llm"
        ep.endpoint_kind = "api"
        ep.model_refresh_mode = "manual"
        ep.cached_models = json.dumps(models)
        db.commit()
        result = {
            "id": ep.id,
            "name": ep.name,
            "base_url": ep.base_url,
            "models": models,
            "provider_auth_id": auth.id,
            "account_label": _display_label(auth, ep),
            "reconnected": bool(reconnect_auth_id),
        }
    finally:
        db.close()

    chatgpt_subscription.USAGE_CACHE.invalidate(result["provider_auth_id"])
    try:
        from routes.model_routes import _invalidate_models_cache

        _invalidate_models_cache()
    except Exception:
        pass
    return result


def _form_value(form, key: str) -> str:
    try:
        value = form.get(key) if form is not None else None
    except Exception:
        value = None
    return str(value).strip() if value is not None else ""


def _start_device_flow(request: Request, form) -> DeviceFlowStart:
    owner = effective_user(request) or None
    try:
        label = chatgpt_subscription.normalize_account_label(_form_value(form, "label"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    reconnect_auth_id = _form_value(form, "reconnect_auth_id") or None
    reconnect_endpoint_id = _form_value(form, "reconnect_endpoint_id") or None
    if reconnect_endpoint_id and not reconnect_auth_id:
        raise HTTPException(400, "Reconnect requires an account id")

    # Validate the intended operation up front so the user is not sent through
    # OAuth for a request that can never be provisioned.
    db = SessionLocal()
    try:
        if reconnect_auth_id:
            auth = chatgpt_subscription.find_owned_auth_session(db, reconnect_auth_id, owner)
            if auth is None:
                raise HTTPException(404, "ChatGPT subscription account not found")
            if reconnect_endpoint_id:
                ep_q = db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == reconnect_endpoint_id,
                    ModelEndpoint.provider_auth_id == auth.id,
                )
                if _owner_scope(ep_q, ModelEndpoint, owner).first() is None:
                    raise HTTPException(404, "ChatGPT subscription endpoint not found")
            if label:
                try:
                    _assert_label_available(db, owner, label, exclude_auth_id=auth.id)
                except ValueError as exc:
                    raise HTTPException(409, str(exc))
        else:
            try:
                _assert_label_available(db, owner, label)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
    finally:
        db.close()

    try:
        data = chatgpt_subscription.request_device_code()
    except Exception as exc:
        raise chatgpt_subscription.to_http_exception(exc)

    device_auth_id = data.get("device_auth_id")
    user_code = data.get("user_code")
    if not device_auth_id or not user_code:
        raise HTTPException(502, "ChatGPT did not return a complete device code")
    # Never pass an arbitrary upstream URL into an authorization link.
    from urllib.parse import urlsplit
    fallback_uri = f"{chatgpt_subscription.CHATGPT_OAUTH_ISSUER}/codex/device"
    verification_uri = data.get("verification_uri") or fallback_uri
    try:
        parsed = urlsplit(verification_uri)
        if parsed.scheme != "https" or parsed.netloc != "auth.openai.com":
            verification_uri = fallback_uri
    except (TypeError, ValueError):
        verification_uri = fallback_uri
    # The pending payload carries only what provisioning needs: the device
    # handle, the owner and the intended operation. No access/refresh tokens.
    pending: Dict[str, Any] = {
        "device_auth_id": device_auth_id,
        "user_code": user_code,
        "owner": owner,
        "label": label,
        "reconnect_auth_id": reconnect_auth_id,
        "reconnect_endpoint_id": reconnect_endpoint_id,
    }
    response: Dict[str, Any] = {
        "user_code": user_code,
        "verification_uri": verification_uri,
        "mode": "reconnect" if reconnect_auth_id else "connect",
    }
    if label:
        response["account_label"] = label
    return DeviceFlowStart(
        pending=pending,
        response=response,
        interval=int(data.get("interval") or 5),
        expires_in=int(data.get("expires_in") or 900),
    )


def _poll_device_flow(request: Request, pending: Dict) -> DeviceFlowPoll:
    # The poller must be the same user who started the flow: a poll id is not
    # a bearer for provisioning into someone else's account list.
    current_owner = effective_user(request) or None
    if (pending.get("owner") or None) != current_owner:
        raise HTTPException(403, "This sign-in belongs to another user")
    if pending.get("reconnect_auth_id"):
        db = SessionLocal()
        try:
            auth = chatgpt_subscription.find_owned_auth_session(db, pending["reconnect_auth_id"], current_owner)
            if auth is None:
                raise HTTPException(404, "ChatGPT subscription account not found")
            if pending.get("reconnect_endpoint_id"):
                ep = _owner_scope(db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == pending["reconnect_endpoint_id"],
                    ModelEndpoint.provider_auth_id == auth.id,
                ), ModelEndpoint, current_owner).first()
                if ep is None:
                    raise HTTPException(404, "ChatGPT subscription endpoint not found")
        finally:
            db.close()
    try:
        data = chatgpt_subscription.poll_device_auth(pending["device_auth_id"], pending["user_code"])
    except Exception as exc:
        logger.debug("ChatGPT device poll failed: %s", type(exc).__name__)
        return DeviceFlowPoll.pending("Sign-in status temporarily unavailable")

    authorization_code = data.get("authorization_code")
    code_verifier = data.get("code_verifier")
    if authorization_code and code_verifier:
        try:
            tokens = chatgpt_subscription.exchange_authorization_code(authorization_code, code_verifier)
            result = _provision_endpoint(
                tokens,
                pending.get("owner"),
                label=pending.get("label") or "",
                reconnect_auth_id=pending.get("reconnect_auth_id") or None,
                reconnect_endpoint_id=pending.get("reconnect_endpoint_id") or None,
            )
        except Exception as exc:
            logger.warning("ChatGPT Subscription endpoint provisioning failed: %s", type(exc).__name__)
            raise chatgpt_subscription.to_http_exception(exc)
        return DeviceFlowPoll.authorized(result)

    err = data.get("error") or data.get("status")
    if err in ("authorization_pending", "pending", None):
        return DeviceFlowPoll.pending()
    if err == "slow_down":
        return DeviceFlowPoll.slow_down(int(data.get("interval") or 0) or None)
    if err in ("expired_token", "access_denied", "denied"):
        return DeviceFlowPoll.failed(err)
    return DeviceFlowPoll.pending("unknown")


# ── Account listing / usage ─────────────────────────────────────────────────

def _account_summary(auth, endpoints) -> Dict[str, Any]:
    ep = endpoints[0] if endpoints else None
    return {
        "auth_id": auth.id,
        "label": _display_label(auth, ep),
        "name": (ep.name if ep is not None else None) or auth.label or chatgpt_subscription.CHATGPT_SUBSCRIPTION_LEGACY_NAME,
        "endpoint_ids": [row.id for row in endpoints],
        "connected_at": auth.created_at.isoformat() if getattr(auth, "created_at", None) else None,
        "last_refresh": auth.last_refresh.isoformat() if getattr(auth, "last_refresh", None) else None,
        "connected": bool(auth.refresh_token),
    }


def usage_error_payload(exc: chatgpt_subscription.ChatGPTUsageUnavailable) -> Dict[str, Any]:
    """Safe, credential-free payload for a failed usage read."""
    return {
        "available": False,
        "reason": exc.reason,
        "message": str(exc),
        "status_code": exc.status_code,
        "reconnect_suggested": exc.reason == "reauth",
    }


def _load_owned_account(request: Request, auth_id: str):
    """Admin gate + owner scope for account-level operations."""
    require_admin(request)
    owner = effective_user(request) or get_current_user(request) or None
    db = SessionLocal()
    try:
        auth = chatgpt_subscription.find_owned_auth_session(db, auth_id, owner)
        if auth is None:
            raise HTTPException(404, "ChatGPT subscription account not found")
        endpoints = _endpoints_for_auth(db, auth.id)
        return owner, _account_summary(auth, endpoints)
    finally:
        db.close()


def setup_chatgpt_subscription_routes():
    router = create_device_flow_router(
        prefix="/api/chatgpt-subscription",
        tags=["chatgpt-subscription"],
        store=_DEVICE_FLOW_STORE,
        start_flow=_start_device_flow,
        poll_flow=_poll_device_flow,
    )

    @router.get("/accounts")
    def list_accounts(request: Request):
        require_admin(request)
        owner = effective_user(request) or get_current_user(request) or None
        db = SessionLocal()
        try:
            return [
                _account_summary(auth, _endpoints_for_auth(db, auth.id))
                for auth in _owner_chatgpt_auths(db, owner)
            ]
        finally:
            db.close()

    @router.get("/accounts/{auth_id}/usage")
    def account_usage(auth_id: str, request: Request, refresh: bool = False, response: Response = None):
        """Read-only, owner-scoped usage for exactly one ChatGPT account.

        Failures here are telemetry failures only: the model endpoint is never
        disabled, credentials are never destroyed and no reconnect is started.
        """
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
        owner, account = _load_owned_account(request, auth_id)
        try:
            usage = chatgpt_subscription.get_account_usage(account["auth_id"], owner=owner, force_refresh=refresh)
        except chatgpt_subscription.ChatGPTSubscriptionAuthNotFound:
            raise HTTPException(404, "ChatGPT subscription account not found")
        except chatgpt_subscription.ChatGPTUsageUnavailable as exc:
            payload = usage_error_payload(exc)
            payload["account"] = account
            return payload
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("ChatGPT usage read failed for auth %s: %s", auth_id, type(exc).__name__)
            payload = usage_error_payload(
                chatgpt_subscription.ChatGPTUsageUnavailable("upstream", "ChatGPT usage is unavailable.")
            )
            payload["account"] = account
            return payload
        return {"available": True, "account": account, "usage": usage}

    return router
