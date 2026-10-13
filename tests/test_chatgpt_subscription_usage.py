"""Read-only ChatGPT Subscription usage: contract, normalization, cache, errors.

Contract mirrored from openai/codex ``codex-rs/backend-client`` (ChatGptApi
path style): ``GET {backend-api}/wham/usage`` returning ``plan_type``,
``rate_limit{primary_window,secondary_window}``, ``additional_rate_limits[]``,
``rate_limit_reached_type`` and ``account_id``.
"""

import json

import httpx
import pytest

from src import chatgpt_subscription as cs


@pytest.fixture
def owned_accounts(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import core.database as cdb
    engine = create_engine("sqlite:///:memory:")
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cs, "_database_handles", lambda: (cdb.ProviderAuthSession, factory, cdb.utcnow_naive))
    with factory() as db:
        for auth_id in ("auth-a", "auth-b"):
            db.add(cdb.ProviderAuthSession(id=auth_id, provider=cs.CHATGPT_SUBSCRIPTION_PROVIDER, owner="alice", base_url=cs.DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL))
        db.commit()
    yield factory
    engine.dispose()


def _window(used, seconds, reset_at):
    return {"used_percent": used, "limit_window_seconds": seconds, "reset_after_seconds": 10, "reset_at": reset_at}


_FULL_PAYLOAD = {
    "plan_type": "plus",
    "account_id": "acct_123",
    "user_id": "user_1",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": _window(71, 300 * 60, 1_800_000_000),
        "secondary_window": _window(28, 7 * 24 * 3600, 1_800_400_000),
    },
    "additional_rate_limits": [
        {
            "limit_name": "GPT-5.5 Pro",
            "metered_feature": "codex_pro",
            "normal_model_slug": "gpt-5.5-pro",
            "rate_limit": {"allowed": True, "limit_reached": False, "primary_window": _window(5, 3600, 1_800_001_000)},
        },
        {"limit_name": "future", "metered_feature": "codex_future", "rate_limit": None},
        "garbage",
    ],
    "credits": {"has_credits": True, "unlimited": False, "balance": "9.99"},
    "spend_control": {"reached": False},
    "rate_limit_reached_type": None,
    "rate_limit_upsell": {"title": "upgrade"},
    "some_new_field": {"nested": [1, 2, 3]},
}


def test_usage_url_matches_codex_chatgpt_backend_contract():
    assert cs.CHATGPT_USAGE_URL == "https://chatgpt.com/backend-api/wham/usage"


def test_normalize_primary_secondary_and_additional_buckets():
    out = cs.normalize_usage_payload(json.loads(json.dumps(_FULL_PAYLOAD)))
    assert out["plan_type"] == "plus"
    assert out["account_id"] == "acct_123"
    assert out["ordinary_usage_allowed"] is True
    assert out["rate_limit_reached_type"] is None
    codex, pro, future = out["limits"]
    assert codex["limit_id"] == "codex" and codex["limit_name"] is None
    primary, secondary = codex["windows"]
    assert primary == {
        "kind": "primary", "name": "5H", "used_percent": 71.0, "remaining_percent": 29.0,
        "window_minutes": 300, "resets_at": 1_800_000_000, "reset_after_seconds": 10,
    }
    assert secondary["kind"] == "secondary"
    assert secondary["name"] == "WEEK"
    assert secondary["window_minutes"] == 10080
    assert secondary["used_percent"] == 28.0 and secondary["remaining_percent"] == 72.0
    assert pro["limit_id"] == "codex_pro"
    assert pro["limit_name"] == "GPT-5.5 Pro"
    assert pro["normal_model_slug"] == "gpt-5.5-pro"
    assert pro["windows"][0]["name"] == "1H"
    assert pro["windows"][0]["used_percent"] == 5.0
    # Unknown/empty additional bucket is kept (not dropped) but has no windows.
    assert future["limit_id"] == "codex_future" and future["windows"] == []
    # Unknown top-level fields and raw upstream metadata never leak through.
    for forbidden in ("user_id", "credits", "spend_control", "rate_limit_upsell", "some_new_field"):
        assert forbidden not in out


def test_normalize_tolerates_absent_windows_and_unknown_reached_type():
    out = cs.normalize_usage_payload({"plan_type": "pro", "rate_limit": None, "rate_limit_reached_type": {"type": "workspace_owner_credits_depleted"}})
    assert out["plan_type"] == "pro"
    assert out["ordinary_usage_allowed"] is None
    assert out["rate_limit_reached_type"] == "workspace_owner_credits_depleted"
    assert out["limits"] == [{
        "limit_id": "codex", "limit_name": None, "normal_model_slug": None,
        "allowed": None, "limit_reached": None, "windows": [],
    }]
    # Missing reset must stay absent — never invented.
    out2 = cs.normalize_usage_payload({"plan_type": "free", "rate_limit": {"primary_window": {"used_percent": "12.5", "limit_window_seconds": 0}}})
    win = out2["limits"][0]["windows"][0]
    assert win["resets_at"] is None and win["window_minutes"] is None and win["name"] == "LIMIT"
    assert win["used_percent"] == 12.5


def test_normalize_clamps_percent_and_rejects_non_object():
    out = cs.normalize_usage_payload({"rate_limit": {"primary_window": {"used_percent": 250, "limit_window_seconds": 90}}})
    win = out["limits"][0]["windows"][0]
    assert win["used_percent"] == 100.0 and win["remaining_percent"] == 0.0
    assert win["window_minutes"] == 2  # ceil(90/60) like Codex
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.normalize_usage_payload(["not", "an", "object"])
    assert exc.value.reason == "malformed"


@pytest.mark.parametrize("minutes,name", [(300, "5H"), (10080, "WEEK"), (20160, "2W"), (1440, "1D"), (90, "90M"), (None, "LIMIT")])
def test_friendly_window_names_derive_from_duration(minutes, name):
    assert cs.friendly_window_name(minutes) == name


def _fake_get(monkeypatch, *, status=200, body=b"{}", raise_exc=None, seen=None):
    def fake_get(url, headers=None, timeout=None):
        if seen is not None:
            seen.append({"url": url, "headers": dict(headers or {}), "timeout": timeout})
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, content=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(cs.httpx, "get", fake_get)


def test_fetch_usage_uses_bearer_and_account_header_and_strict_timeout(monkeypatch):
    seen = []
    _fake_get(monkeypatch, body=json.dumps({"plan_type": "plus"}).encode(), seen=seen)
    # A JWT whose auth claim carries the ChatGPT account id, as Codex reads it.
    import base64
    claims = base64.urlsafe_b64encode(json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_9"}}).encode()).rstrip(b"=").decode()
    token = f"hdr.{claims}.sig"
    data = cs.fetch_usage_payload(token)
    assert data == {"plan_type": "plus"}
    assert seen[0]["url"] == "https://chatgpt.com/backend-api/wham/usage"
    assert seen[0]["headers"]["Authorization"] == f"Bearer {token}"
    assert seen[0]["headers"]["ChatGPT-Account-Id"] == "acct_9"
    assert 0 < seen[0]["timeout"] <= 10


@pytest.mark.parametrize("status,reason", [(401, "reauth"), (403, "reauth"), (429, "rate_limited"), (500, "upstream"), (503, "upstream"), (418, "upstream")])
def test_fetch_usage_classifies_http_failures(monkeypatch, status, reason):
    _fake_get(monkeypatch, status=status, body=b"nope")
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.fetch_usage_payload("tok")
    assert exc.value.reason == reason
    assert exc.value.status_code == status


def test_fetch_usage_handles_timeout_network_and_malformed_json(monkeypatch):
    _fake_get(monkeypatch, raise_exc=httpx.ReadTimeout("slow"))
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.fetch_usage_payload("tok")
    assert exc.value.reason == "timeout"
    _fake_get(monkeypatch, raise_exc=httpx.ConnectError("down"))
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.fetch_usage_payload("tok")
    assert exc.value.reason == "network"
    _fake_get(monkeypatch, body=b"<html>not json")
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.fetch_usage_payload("tok")
    assert exc.value.reason == "malformed"
    _fake_get(monkeypatch, body=b"[1,2]")
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.fetch_usage_payload("tok")
    assert exc.value.reason == "malformed"


def test_usage_cache_is_per_auth_session_and_expires():
    clock = {"t": 100.0}
    cache = cs.UsageCache(ttl_seconds=45, time_func=lambda: clock["t"])
    cache.put("auth-a", {"plan_type": "plus"})
    assert cache.get("auth-a") == {"plan_type": "plus"}
    # Cache for A can never satisfy B.
    assert cache.get("auth-b") is None
    clock["t"] += 44
    assert cache.get("auth-a") is not None
    clock["t"] += 2
    assert cache.get("auth-a") is None


def test_cache_is_bounded_copies_values_and_cleans_expired_accounts():
    clock = [0]
    cache = cs.UsageCache(time_func=lambda: clock[0], max_entries=2)
    original = {"limits": [{"used": 1}]}
    cache.put("a", original)
    original["limits"][0]["used"] = 99
    assert cache.get("a")["limits"][0]["used"] == 1
    cache.put("b", {})
    cache.put("c", {})
    assert cache.get("a") is None
    clock[0] = 46
    cache.put("d", {})
    assert set(cache._entries) == {"d"}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "NaN", "Infinity", True, {}])
def test_non_finite_and_malformed_usage_never_invents_a_percentage(value):
    out = cs.normalize_usage_window({"used_percent": value, "reset_at": value}, "primary")
    assert out["used_percent"] is None
    assert out["remaining_percent"] is None
    assert out["resets_at"] is None
    json.dumps(out, allow_nan=False)


def test_future_window_kind_and_reached_kind_are_retained():
    out = cs.normalize_usage_payload({"rate_limit": {"tertiary_window": _window(3, 7200, 100)},
                                      "rate_limit_reached_type": {"kind": "future_limit"}})
    assert out["limits"][0]["windows"][0]["kind"] == "tertiary"
    assert out["limits"][0]["windows"][0]["window_minutes"] == 120
    assert out["rate_limit_reached_type"] == "future_limit"


def test_cache_hits_revalidate_owner_and_deleted_auth(owned_accounts):
    from core.database import ProviderAuthSession
    cache = cs.UsageCache()
    cache.put("auth-a", {"plan_type": "plus"})
    with pytest.raises(cs.ChatGPTSubscriptionAuthNotFound):
        cs.get_account_usage("auth-a", owner="mallory", cache=cache)
    with pytest.raises(cs.ChatGPTSubscriptionAuthNotFound):
        cs.get_account_usage("auth-a", owner=None, cache=cache)
    assert cs.get_account_usage("auth-a", owner="alice", cache=cache)["cached"] is True
    with owned_accounts() as db:
        db.delete(db.get(ProviderAuthSession, "auth-a"))
        db.commit()
    with pytest.raises(cs.ChatGPTSubscriptionAuthNotFound):
        cs.get_account_usage("auth-a", owner="alice", cache=cache)


def test_oauth_errors_never_echo_upstream_credentials():
    response = httpx.Response(401, json={"error": {"code": "invalid_token", "message": "SECRET-AT SECRET-RT"}})
    with pytest.raises(cs.ChatGPTSubscriptionReauthRequired) as exc:
        cs._raise_for_oauth_response(response, "token refresh")
    assert "SECRET" not in str(exc.value)


def test_get_account_usage_uses_exact_auth_session_cache_and_force_refresh(monkeypatch, owned_accounts):
    clock = {"t": 1000.0}
    cache = cs.UsageCache(ttl_seconds=45, time_func=lambda: clock["t"])
    resolved = []

    def fake_resolve(auth_id, owner=None, force_refresh=False):
        resolved.append((auth_id, owner))
        return {"api_key": f"token-for-{auth_id}", "base_url": cs.DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL}

    fetched = []

    def fake_fetch(access_token, timeout=None):
        fetched.append(access_token)
        return {"plan_type": "plus" if access_token.endswith("auth-a") else "pro", "rate_limit": {"primary_window": _window(10, 300, 5)}}

    monkeypatch.setattr(cs, "resolve_runtime_credentials", fake_resolve)
    monkeypatch.setattr(cs, "fetch_usage_payload", fake_fetch)

    a1 = cs.get_account_usage("auth-a", owner="alice", cache=cache)
    assert a1["plan_type"] == "plus" and a1["cached"] is False and a1["auth_id"] == "auth-a"
    assert resolved == [("auth-a", "alice")]
    assert fetched == ["token-for-auth-a"]

    a2 = cs.get_account_usage("auth-a", owner="alice", cache=cache)
    assert a2["cached"] is True and a2["plan_type"] == "plus"
    assert len(fetched) == 1  # served from cache

    b1 = cs.get_account_usage("auth-b", owner="alice", cache=cache)
    assert b1["plan_type"] == "pro" and b1["cached"] is False
    assert fetched == ["token-for-auth-a", "token-for-auth-b"]  # B never reuses A's cache or token

    a3 = cs.get_account_usage("auth-a", owner="alice", force_refresh=True, cache=cache)
    assert a3["cached"] is False
    assert fetched == ["token-for-auth-a", "token-for-auth-b", "token-for-auth-a"]

    # Nothing token-shaped in the returned structure.
    dumped = json.dumps(a3)
    assert "token-for" not in dumped and "refresh_token" not in dumped and "access_token" not in dumped


def test_get_account_usage_maps_credential_failures_without_touching_endpoint(monkeypatch, owned_accounts):
    cache = cs.UsageCache(ttl_seconds=45)

    def boom(auth_id, owner=None, force_refresh=False):
        raise cs.ChatGPTSubscriptionReauthRequired("expired")

    monkeypatch.setattr(cs, "resolve_runtime_credentials", boom)
    with pytest.raises(cs.ChatGPTUsageUnavailable) as exc:
        cs.get_account_usage("auth-a", owner="alice", cache=cache)
    assert exc.value.reason == "reauth"

    def missing(auth_id, owner=None, force_refresh=False):
        raise cs.ChatGPTSubscriptionAuthNotFound("gone")

    monkeypatch.setattr(cs, "resolve_runtime_credentials", missing)
    with pytest.raises(cs.ChatGPTSubscriptionAuthNotFound):
        cs.get_account_usage("auth-x", owner="alice", cache=cache)


def test_usage_route_failure_is_reported_safely_and_endpoint_stays_enabled(monkeypatch):
    import types
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base, ModelEndpoint, ProviderAuthSession
    import routes.chatgpt_subscription_routes as csr

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(csr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(csr, "require_admin", lambda request: None)
    db = TestSessionLocal()
    try:
        db.add(ProviderAuthSession(id="auth-a", provider=cs.CHATGPT_SUBSCRIPTION_PROVIDER, owner="alice", label="ChatGPT · codex00",
                                   base_url=cs.DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL, access_token="SECRET-AT", refresh_token="SECRET-RT"))
        db.add(ModelEndpoint(id="ep-a", name="ChatGPT · codex00", base_url=cs.DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL,
                             provider_auth_id="auth-a", owner="alice", is_enabled=True, supports_tools=False))
        db.commit()
    finally:
        db.close()

    def failing(auth_id, owner=None, force_refresh=False, cache=None):
        raise cs.ChatGPTUsageUnavailable("reauth", "rejected", status_code=401)

    monkeypatch.setattr(cs, "get_account_usage", failing)
    router = csr.setup_chatgpt_subscription_routes()
    usage = next(r.endpoint for r in router.routes if r.path.endswith("/usage"))
    request = types.SimpleNamespace(state=types.SimpleNamespace(current_user="alice"), app=None, headers={})
    payload = usage("auth-a", request)
    assert payload["available"] is False
    assert payload["reason"] == "reauth"
    assert payload["reconnect_suggested"] is True
    assert payload["account"]["auth_id"] == "auth-a"
    dumped = json.dumps(payload)
    assert "SECRET" not in dumped and "access_token" not in dumped and "refresh_token" not in dumped

    db = TestSessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-a").first()
        auth = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth-a").first()
        # Usage failure != endpoint failure: nothing disabled, nothing destroyed.
        assert ep.is_enabled is True
        assert auth.refresh_token == "SECRET-RT"
    finally:
        db.close()
