"""Device-flow start/poll semantics for multiple ChatGPT subscriptions.

The pending device-flow state must carry the *intended operation* — create a
new account, or reconnect one exact existing account — with owner validation,
and must never carry access/refresh tokens.
"""

import types

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint, ProviderAuthSession
import routes.chatgpt_subscription_routes as csr
from routes.device_flow import PendingDeviceFlowStore

_BASE = "https://chatgpt.com/backend-api/codex"


def _mem_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(csr, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def _request(user):
    return types.SimpleNamespace(state=types.SimpleNamespace(current_user=user), app=None, headers={})


def _seed_account(TestSessionLocal, owner, auth_id, ep_id, label="ChatGPT Subscription"):
    db = TestSessionLocal()
    try:
        db.add(ProviderAuthSession(
            id=auth_id, provider=csr.chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER, owner=owner,
            label=label, base_url=_BASE, access_token="AT", refresh_token="RT", auth_mode="chatgpt",
        ))
        db.add(ModelEndpoint(id=ep_id, name=label, base_url=_BASE, provider_auth_id=auth_id, owner=owner))
        db.commit()
    finally:
        db.close()


def _fake_device_code(monkeypatch):
    monkeypatch.setattr(
        csr.chatgpt_subscription, "request_device_code",
        lambda: {"device_auth_id": "dev-1", "user_code": "ABCD-EFGH", "interval": 3, "expires_in": 120},
    )


def test_start_carries_label_and_connect_mode_without_tokens(monkeypatch):
    _mem_db(monkeypatch)
    _fake_device_code(monkeypatch)
    start = csr._start_device_flow(_request("alice"), {"label": "  codex00 "})
    assert start.pending == {
        "device_auth_id": "dev-1",
        "user_code": "ABCD-EFGH",
        "owner": "alice",
        "label": "codex00",
        "reconnect_auth_id": None,
        "reconnect_endpoint_id": None,
    }
    assert start.response["mode"] == "connect"
    assert start.response["account_label"] == "codex00"
    for value in start.pending.values():
        assert "access_token" not in str(value) and "refresh_token" not in str(value)
    assert "access_token" not in start.pending and "refresh_token" not in start.pending


def test_start_rejects_overlong_or_duplicate_label(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _fake_device_code(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        csr._start_device_flow(_request("alice"), {"label": "x" * 41})
    assert exc.value.status_code == 400
    _seed_account(TestSessionLocal, "alice", "auth-a", "ep-a", label="ChatGPT · codex00")
    with pytest.raises(HTTPException) as exc:
        csr._start_device_flow(_request("alice"), {"label": "codex00"})
    assert exc.value.status_code == 409


def test_start_reconnect_carries_exact_target_ids(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _fake_device_code(monkeypatch)
    _seed_account(TestSessionLocal, "alice", "auth-a", "ep-a", label="ChatGPT · codex00")
    start = csr._start_device_flow(
        _request("alice"), {"reconnect_auth_id": "auth-a", "reconnect_endpoint_id": "ep-a"},
    )
    assert start.pending["reconnect_auth_id"] == "auth-a"
    assert start.pending["reconnect_endpoint_id"] == "ep-a"
    assert start.pending["owner"] == "alice"
    assert start.response["mode"] == "reconnect"


def test_start_reconnect_for_another_owner_is_rejected(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    _fake_device_code(monkeypatch)
    _seed_account(TestSessionLocal, "alice", "auth-a", "ep-a")
    with pytest.raises(HTTPException) as exc:
        csr._start_device_flow(_request("mallory"), {"reconnect_auth_id": "auth-a"})
    assert exc.value.status_code == 404
    # Mismatched endpoint id for a real auth id is also rejected.
    _seed_account(TestSessionLocal, "alice", "auth-b", "ep-b")
    with pytest.raises(HTTPException) as exc:
        csr._start_device_flow(_request("alice"), {"reconnect_auth_id": "auth-a", "reconnect_endpoint_id": "ep-b"})
    assert exc.value.status_code == 404


def test_poll_provisions_with_pending_operation_and_owner(monkeypatch):
    factory = _mem_db(monkeypatch)
    _seed_account(factory, "alice", "auth-a", "ep-a")
    monkeypatch.setattr(
        csr.chatgpt_subscription, "poll_device_auth",
        lambda device_auth_id, user_code: {"authorization_code": "code", "code_verifier": "ver"},
    )
    monkeypatch.setattr(
        csr.chatgpt_subscription, "exchange_authorization_code",
        lambda code, verifier: {"access_token": "AT", "refresh_token": "RT"},
    )
    seen = {}

    def fake_provision(tokens, owner, **kwargs):
        seen["tokens"] = tokens
        seen["owner"] = owner
        seen["kwargs"] = kwargs
        return {"id": "ep-new", "name": "ChatGPT · codex00", "models": ["gpt-5.5"]}

    monkeypatch.setattr(csr, "_provision_endpoint", fake_provision)
    pending = {
        "device_auth_id": "dev-1", "user_code": "X", "owner": "alice", "label": "codex00",
        "reconnect_auth_id": "auth-a", "reconnect_endpoint_id": "ep-a",
    }
    outcome = csr._poll_device_flow(_request("alice"), pending)
    assert outcome.status == "authorized"
    assert seen["owner"] == "alice"
    assert seen["kwargs"] == {"label": "codex00", "reconnect_auth_id": "auth-a", "reconnect_endpoint_id": "ep-a"}


def test_poll_by_a_different_user_is_rejected(monkeypatch):
    _mem_db(monkeypatch)
    called = {"n": 0}

    def _never(*a, **k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(csr.chatgpt_subscription, "poll_device_auth", _never)
    pending = {"device_auth_id": "dev-1", "user_code": "X", "owner": "alice", "label": "", "reconnect_auth_id": None, "reconnect_endpoint_id": None}
    with pytest.raises(HTTPException) as exc:
        csr._poll_device_flow(_request("mallory"), pending)
    assert exc.value.status_code == 403
    assert called["n"] == 0


def test_expired_pending_flow_is_dropped_by_store():
    clock = {"t": 1000.0}
    store = PendingDeviceFlowStore(time_func=lambda: clock["t"])
    poll_id = store.add({"owner": "alice", "reconnect_auth_id": "auth-a"}, interval=5, expires_in=60)
    assert store.get_payload(poll_id)["reconnect_auth_id"] == "auth-a"
    clock["t"] += 61
    assert store.get_payload(poll_id) is None
    clock["t"] += 3600
    assert store.get_payload(poll_id) is None


def test_poll_revalidates_reconnect_endpoint_after_start(monkeypatch):
    factory = _mem_db(monkeypatch)
    _fake_device_code(monkeypatch)
    _seed_account(factory, "alice", "auth-a", "ep-a")
    start = csr._start_device_flow(_request("alice"), {"reconnect_auth_id": "auth-a", "reconnect_endpoint_id": "ep-a"})
    with factory() as db:
        db.delete(db.get(ModelEndpoint, "ep-a"))
        db.commit()
    monkeypatch.setattr(csr.chatgpt_subscription, "poll_device_auth", lambda *args: pytest.fail("must reject before polling"))
    with pytest.raises(HTTPException) as exc:
        csr._poll_device_flow(_request("alice"), start.pending)
    assert exc.value.status_code == 404


def test_pending_payload_ignores_actual_secrets_and_allows_harmless_label(monkeypatch):
    _mem_db(monkeypatch)
    monkeypatch.setattr(csr.chatgpt_subscription, "request_device_code", lambda: {
        "device_auth_id": "dev", "user_code": "code", "access_token": "SECRET-AT", "refresh_token": "SECRET-RT",
    })
    start = csr._start_device_flow(_request("alice"), {"label": "refresh_token"})
    assert start.pending["label"] == "refresh_token"
    for payload in (start.pending, start.response):
        assert "access_token" not in payload and "refresh_token" not in payload
        assert "SECRET-AT" not in str(payload) and "SECRET-RT" not in str(payload)


def test_account_listing_and_usage_route_are_owner_scoped(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    monkeypatch.setattr(csr, "require_admin", lambda request: None)
    _seed_account(TestSessionLocal, "alice", "auth-a", "ep-a", label="ChatGPT · codex00")
    _seed_account(TestSessionLocal, "bob", "auth-b", "ep-b", label="ChatGPT · work")
    router = csr.setup_chatgpt_subscription_routes()
    handlers = {(r.path, tuple(sorted(r.methods))): r.endpoint for r in router.routes}
    list_accounts = handlers[("/api/chatgpt-subscription/accounts", ("GET",))]
    usage = handlers[("/api/chatgpt-subscription/accounts/{auth_id}/usage", ("GET",))]

    alice_accounts = list_accounts(_request("alice"))
    assert [a["auth_id"] for a in alice_accounts] == ["auth-a"]
    assert alice_accounts[0]["label"] == "codex00"
    assert alice_accounts[0]["endpoint_ids"] == ["ep-a"]
    assert "access_token" not in str(alice_accounts) and "refresh_token" not in str(alice_accounts)
    assert "AT" not in str(alice_accounts)

    calls = []

    def fake_usage(auth_id, owner=None, force_refresh=False, cache=None):
        calls.append((auth_id, owner, force_refresh))
        return {"plan_type": "plus", "limits": [], "auth_id": auth_id}

    monkeypatch.setattr(csr.chatgpt_subscription, "get_account_usage", fake_usage)
    ok = usage("auth-a", _request("alice"), refresh=True)
    assert ok["available"] is True
    assert ok["usage"]["auth_id"] == "auth-a"
    assert calls == [("auth-a", "alice", True)]

    # Bob's account is invisible to Alice; the usage helper is never invoked.
    with pytest.raises(HTTPException) as exc:
        usage("auth-b", _request("alice"))
    assert exc.value.status_code == 404
    assert len(calls) == 1
