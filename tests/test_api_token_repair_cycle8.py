"""Cycle-8/9 regressions for bearer provider-auth session repair."""

import base64
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as cdb


_CODEX_BASE = "https://chatgpt.com/backend-api/codex"


def _future_access_token():
    def encode(payload):
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode({'exp': int(time.time()) + 3600})}.signature"


def _provider_db(monkeypatch, *, access_token="cached-access-token"):
    from routes import chat_helpers
    from src import chatgpt_subscription

    engine = create_engine("sqlite:///:memory:")
    cdb.Base.metadata.create_all(bind=engine)
    test_session_local = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(chat_helpers, "SessionLocal", test_session_local)
    monkeypatch.setattr(
        chatgpt_subscription,
        "_database_handles",
        lambda: (cdb.ProviderAuthSession, test_session_local, cdb.utcnow_naive),
    )

    db = test_session_local()
    db.add(cdb.ProviderAuthSession(
        id="auth-1",
        provider="chatgpt-subscription",
        owner="alice",
        base_url=_CODEX_BASE,
        access_token=access_token,
        refresh_token="refresh-token",
        auth_mode="chatgpt",
    ))
    db.add(cdb.ModelEndpoint(
        id="endpoint-1",
        name="ChatGPT Subscription",
        base_url=_CODEX_BASE,
        api_key=None,
        provider_auth_id="auth-1",
        owner="alice",
        is_enabled=True,
        endpoint_kind="api",
        cached_models=json.dumps(["gpt-5.5"]),
        pinned_models=json.dumps(["gpt-5.5"]),
    ))
    db.commit()
    db.close()
    return test_session_local


def _registered_session():
    return SimpleNamespace(
        endpoint_url=f"{_CODEX_BASE}/responses",
        model="gpt-5.5",
        model_endpoint_id="endpoint-1",
        endpoint_provenance="registered",
        headers={"Authorization": "Bearer stale-token"},
    )


def test_bearer_validator_uses_owner_cached_provider_auth_without_refresh(monkeypatch):
    from routes.chat_helpers import _validate_bearer_session_model
    from src import chatgpt_subscription

    _provider_db(monkeypatch)
    monkeypatch.setattr(chatgpt_subscription, "access_token_is_expiring", lambda token: False)
    monkeypatch.setattr(
        chatgpt_subscription,
        "refresh_oauth_tokens",
        lambda *args, **kwargs: pytest.fail("bearer validation refreshed provider credentials"),
    )

    session = _registered_session()
    assert _validate_bearer_session_model(session, owner="alice") == "gpt-5.5"
    assert session.headers["Authorization"] == "Bearer cached-access-token"


def test_bearer_validator_rejects_provider_auth_when_cache_is_unusable(monkeypatch):
    from routes.chat_helpers import _validate_bearer_session_model
    from src import chatgpt_subscription

    _provider_db(monkeypatch)
    monkeypatch.setattr(chatgpt_subscription, "access_token_is_expiring", lambda token: True)

    with pytest.raises(HTTPException) as exc:
        _validate_bearer_session_model(_registered_session(), owner="alice")
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "owner",
    [None, "", "   ", "api", "demo", "system", "internal-tool", " API "],
)
def test_cache_only_provider_auth_rejects_missing_and_sentinel_owners_before_query(monkeypatch, owner):
    from src import chatgpt_subscription

    def forbidden_database_handles():
        pytest.fail("cache-only provider auth queried without a real owner")

    monkeypatch.setattr(chatgpt_subscription, "_database_handles", forbidden_database_handles)
    monkeypatch.setattr(
        chatgpt_subscription,
        "refresh_oauth_tokens",
        lambda *args, **kwargs: pytest.fail("cache-only provider auth refreshed credentials"),
    )

    with pytest.raises(chatgpt_subscription.ChatGPTSubscriptionAuthNotFound):
        chatgpt_subscription.resolve_runtime_credentials(
            "auth-1", owner=owner, allow_live_probes=False
        )


def test_cache_only_provider_auth_normalizes_exact_owner_and_blocks_live_io(monkeypatch):
    from src import chatgpt_subscription

    access_token = _future_access_token()
    _provider_db(monkeypatch, access_token=access_token)
    monkeypatch.setattr(
        chatgpt_subscription,
        "refresh_oauth_tokens",
        lambda *args, **kwargs: pytest.fail("cache-only provider auth refreshed credentials"),
    )
    monkeypatch.setattr(
        chatgpt_subscription.httpx,
        "get",
        lambda *args, **kwargs: pytest.fail("cache-only provider auth probed the provider"),
    )
    monkeypatch.setattr(
        chatgpt_subscription.httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("cache-only provider auth probed the provider"),
    )

    result = chatgpt_subscription.resolve_runtime_credentials(
        "auth-1", owner=" alice ", allow_live_probes=False
    )
    assert result["api_key"] == access_token

    with pytest.raises(chatgpt_subscription.ChatGPTSubscriptionAuthNotFound):
        chatgpt_subscription.resolve_runtime_credentials(
            "auth-1", owner="bob", allow_live_probes=False
        )
