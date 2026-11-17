"""DB-backed ChatGPT Subscription endpoint provisioning tests."""

import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint, ProviderAuthSession
import routes.chatgpt_subscription_routes as csr


def _mem_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    # Match production (core.database SessionLocal is autoflush=False): a pending
    # db.delete(ep) is NOT flushed before the orphan-auth reference-count SELECT,
    # which is exactly why _delete_orphaned_provider_auth needs exclude_ep_id.
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(csr, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def test_provider_auth_identifier_is_generated_independently_of_credentials(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    identifiers = iter([
        UUID("12345678-0000-4000-8000-000000000001"),
        UUID("87654321-0000-4000-8000-000000000002"),
    ])
    monkeypatch.setattr(csr.uuid, "uuid4", lambda: next(identifiers))
    monkeypatch.setattr(csr.chatgpt_subscription, "fetch_available_models", lambda token: ["fixture-model"])
    tokens = {"access_token": "ACCESS-SENTINEL", "refresh_token": "REFRESH-SENTINEL",
              "api_key": "KEY-SENTINEL", "password": "PASSWORD-SENTINEL", "account_id": "ACCOUNT-SENTINEL"}
    result = csr._provision_endpoint(tokens, "alice", label="label-sentinel")
    assert result["provider_auth_id"] == "12345678"
    assert result["id"] == "87654321"
    assert not any(value in json.dumps(result) for value in tokens.values())
    db = TestSessionLocal()
    try:
        auth = db.query(ProviderAuthSession).filter_by(id=result["provider_auth_id"]).one()
        endpoint = db.query(ModelEndpoint).filter_by(id=result["id"]).one()
        assert auth.access_token == tokens["access_token"]
        assert auth.refresh_token == tokens["refresh_token"]
        assert endpoint.provider_auth_id == auth.id
        assert endpoint.api_key is None
    finally:
        db.close()


def test_provision_creates_owner_scoped_auth_session_and_endpoint(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    monkeypatch.setattr(csr.chatgpt_subscription, "fetch_available_models", lambda token: ["gpt-5.5", "o4-mini"])

    res = csr._provision_endpoint({"access_token": "AT", "refresh_token": "RT"}, "alice")

    assert res["name"] == "ChatGPT Subscription"
    assert res["base_url"] == csr.chatgpt_subscription.DEFAULT_CHATGPT_SUBSCRIPTION_BASE_URL
    assert res["models"] == ["gpt-5.5", "o4-mini"]

    db = TestSessionLocal()
    try:
        auth = db.query(ProviderAuthSession).first()
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == res["id"]).first()
        assert auth is not None
        assert auth.owner == "alice"
        assert auth.provider == csr.chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER
        assert auth.access_token == "AT"
        assert auth.refresh_token == "RT"
        assert auth.auth_mode == "chatgpt"
        assert ep is not None
        assert ep.owner == "alice"
        assert ep.api_key is None
        assert ep.provider_auth_id == auth.id
        assert ep.endpoint_kind == "api"
        assert ep.model_refresh_mode == "manual"
        assert ep.supports_tools is False
        assert json.loads(ep.cached_models) == ["gpt-5.5", "o4-mini"]
    finally:
        db.close()


def _connect(monkeypatch, owner, label="", models=("gpt-5.5",), tokens=None, **kwargs):
    monkeypatch.setattr(csr.chatgpt_subscription, "fetch_available_models", lambda token: list(models))
    tokens = tokens or {"access_token": f"AT-{label or 'default'}", "refresh_token": f"RT-{label or 'default'}"}
    return csr._provision_endpoint(tokens, owner, label=label, **kwargs)


def test_second_connection_creates_independent_auth_and_endpoint(monkeypatch):
    """Connecting a second subscription must never overwrite the first."""
    TestSessionLocal = _mem_db(monkeypatch)

    first = _connect(monkeypatch, "bob", label="codex00", tokens={"access_token": "A-AT", "refresh_token": "A-RT"})
    second = _connect(monkeypatch, "bob", label="codex01", tokens={"access_token": "B-AT", "refresh_token": "B-RT"})

    assert first["id"] != second["id"]
    assert first["provider_auth_id"] != second["provider_auth_id"]
    assert first["name"] == "ChatGPT · codex00"
    assert second["name"] == "ChatGPT · codex01"
    assert second["reconnected"] is False
    db = TestSessionLocal()
    try:
        auth_rows = {a.id: a for a in db.query(ProviderAuthSession).filter(ProviderAuthSession.owner == "bob").all()}
        ep_rows = {e.id: e for e in db.query(ModelEndpoint).filter(ModelEndpoint.owner == "bob").all()}
        assert len(auth_rows) == 2
        assert len(ep_rows) == 2
        # Account A's credentials are untouched by connecting B.
        assert auth_rows[first["provider_auth_id"]].access_token == "A-AT"
        assert auth_rows[first["provider_auth_id"]].refresh_token == "A-RT"
        assert auth_rows[second["provider_auth_id"]].access_token == "B-AT"
        assert ep_rows[first["id"]].provider_auth_id == first["provider_auth_id"]
        assert ep_rows[second["id"]].provider_auth_id == second["provider_auth_id"]
        # Same base URL + same model on both routes is valid and intentional.
        assert ep_rows[first["id"]].base_url == ep_rows[second["id"]].base_url
        assert json.loads(ep_rows[first["id"]].cached_models) == json.loads(ep_rows[second["id"]].cached_models)
        for ep in ep_rows.values():
            assert ep.supports_tools is False
    finally:
        db.close()


def test_unlabelled_connections_get_distinct_default_names(monkeypatch):
    _mem_db(monkeypatch)
    first = _connect(monkeypatch, "bob")
    second = _connect(monkeypatch, "bob")
    third = _connect(monkeypatch, "bob")
    # The first account keeps the legacy name; later ones are distinguishable.
    assert first["name"] == "ChatGPT Subscription"
    assert second["name"] == "ChatGPT · account 2"
    assert third["name"] == "ChatGPT · account 3"
    assert len({first["provider_auth_id"], second["provider_auth_id"], third["provider_auth_id"]}) == 3


def test_duplicate_label_for_same_owner_is_rejected_but_other_owner_ok(monkeypatch):
    _mem_db(monkeypatch)
    _connect(monkeypatch, "bob", label="codex00")
    with pytest.raises(ValueError, match="already connected"):
        _connect(monkeypatch, "bob", label="Codex00")
    # Labels are owner scoped: another user may reuse the same label.
    other = _connect(monkeypatch, "carol", label="codex00")
    assert other["name"] == "ChatGPT · codex00"


def test_label_is_trimmed_bounded_and_control_chars_stripped(monkeypatch):
    _mem_db(monkeypatch)
    res = _connect(monkeypatch, "bob", label="  co\x00dex  00\t ")
    assert res["account_label"] == "codex 00"
    assert res["name"] == "ChatGPT · codex 00"
    with pytest.raises(ValueError, match="at most 40"):
        _connect(monkeypatch, "bob", label="x" * 41)


def test_reconnect_updates_only_the_targeted_account(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    a = _connect(monkeypatch, "bob", label="codex00", tokens={"access_token": "A-AT", "refresh_token": "A-RT"})
    b = _connect(monkeypatch, "bob", label="codex01", tokens={"access_token": "B-AT", "refresh_token": "B-RT"})

    res = _connect(
        monkeypatch, "bob", tokens={"access_token": "A-AT2", "refresh_token": "A-RT2"},
        reconnect_auth_id=a["provider_auth_id"], reconnect_endpoint_id=a["id"],
    )
    assert res["reconnected"] is True
    assert res["id"] == a["id"]
    assert res["provider_auth_id"] == a["provider_auth_id"]
    assert res["name"] == "ChatGPT · codex00"  # label preserved on reconnect
    db = TestSessionLocal()
    try:
        assert db.query(ProviderAuthSession).count() == 2
        assert db.query(ModelEndpoint).count() == 2
        auth_a = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == a["provider_auth_id"]).first()
        auth_b = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == b["provider_auth_id"]).first()
        assert auth_a.access_token == "A-AT2" and auth_a.refresh_token == "A-RT2"
        assert auth_b.access_token == "B-AT" and auth_b.refresh_token == "B-RT"
    finally:
        db.close()

    # And the symmetric case: reconnecting B leaves A alone.
    _connect(
        monkeypatch, "bob", tokens={"access_token": "B-AT2", "refresh_token": "B-RT2"},
        reconnect_auth_id=b["provider_auth_id"], reconnect_endpoint_id=b["id"],
    )
    db = TestSessionLocal()
    try:
        auth_a = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == a["provider_auth_id"]).first()
        auth_b = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == b["provider_auth_id"]).first()
        assert auth_a.access_token == "A-AT2"
        assert auth_b.access_token == "B-AT2"
    finally:
        db.close()


def test_reconnect_target_owned_by_another_user_is_rejected(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    a = _connect(monkeypatch, "bob", label="codex00", tokens={"access_token": "A-AT", "refresh_token": "A-RT"})
    with pytest.raises(csr.chatgpt_subscription.ChatGPTSubscriptionAuthNotFound):
        _connect(
            monkeypatch, "mallory", tokens={"access_token": "M-AT", "refresh_token": "M-RT"},
            reconnect_auth_id=a["provider_auth_id"], reconnect_endpoint_id=a["id"],
        )
    db = TestSessionLocal()
    try:
        auth_a = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == a["provider_auth_id"]).first()
        assert auth_a.access_token == "A-AT"
        assert db.query(ProviderAuthSession).count() == 1
        assert db.query(ModelEndpoint).count() == 1
    finally:
        db.close()


def test_legacy_single_account_endpoint_can_be_reconnected_in_place(monkeypatch):
    """Rows provisioned before multi-account support keep working unchanged."""
    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        db.add(ProviderAuthSession(
            id="legacyauth", provider=csr.chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER,
            owner="alice", label="ChatGPT Subscription", base_url="https://chatgpt.com/backend-api/codex",
            access_token="OLD", refresh_token="OLD-RT", auth_mode="chatgpt",
        ))
        db.add(ModelEndpoint(
            id="legacyep", name="ChatGPT Subscription", base_url="https://chatgpt.com/backend-api/codex",
            provider_auth_id="legacyauth", owner="alice", supports_tools=False,
        ))
        db.commit()
    finally:
        db.close()

    res = _connect(
        monkeypatch, "alice", tokens={"access_token": "NEW", "refresh_token": "NEW-RT"},
        reconnect_auth_id="legacyauth",
    )
    assert res["id"] == "legacyep"
    assert res["name"] == "ChatGPT Subscription"
    assert res["account_label"] == ""
    db = TestSessionLocal()
    try:
        assert db.query(ProviderAuthSession).count() == 1
        assert db.query(ProviderAuthSession).first().access_token == "NEW"
        assert db.query(ModelEndpoint).count() == 1
    finally:
        db.close()


def test_provision_rejects_missing_tokens(monkeypatch):
    _mem_db(monkeypatch)
    with pytest.raises(ValueError, match="missing access_token or refresh_token"):
        csr._provision_endpoint({"access_token": "AT"}, "alice")


def test_provision_rejects_accounts_without_usable_models(monkeypatch):
    _mem_db(monkeypatch)
    monkeypatch.setattr(csr.chatgpt_subscription, "fetch_available_models", lambda token: [])

    with pytest.raises(ValueError, match="no usable Codex models"):
        csr._provision_endpoint({"access_token": "AT", "refresh_token": "RT"}, "alice")


def _add_auth_and_endpoints(db, *, auth_id="auth1", ep_ids=("ep1",)):
    db.add(ProviderAuthSession(
        id=auth_id, provider=csr.chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER,
        owner="alice", base_url="https://chatgpt.com/backend-api/codex",
        refresh_token="RT", auth_mode="chatgpt",
    ))
    for ep_id in ep_ids:
        db.add(ModelEndpoint(
            id=ep_id, name="ChatGPT Subscription",
            base_url="https://chatgpt.com/backend-api/codex",
            provider_auth_id=auth_id, owner="alice",
        ))
    db.commit()


def test_delete_orphaned_provider_auth_revokes_when_last_endpoint_removed(monkeypatch):
    from routes.model_routes import _delete_orphaned_provider_auth

    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        _add_auth_and_endpoints(db, auth_id="auth1", ep_ids=("ep1",))
        # Mirror the production delete route: db.delete(ep) is issued (but not yet
        # flushed/committed) BEFORE the orphan check runs.
        ep1 = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep1").first()
        db.delete(ep1)
        # ep1 (its only referencing endpoint) is being deleted, so the auth clears.
        assert _delete_orphaned_provider_auth(db, "auth1", exclude_ep_id="ep1") is True
        db.commit()
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is None
    finally:
        db.close()


def test_delete_orphaned_provider_auth_requires_exclude_ep_id_for_pending_delete(monkeypatch):
    from routes.model_routes import _delete_orphaned_provider_auth

    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        _add_auth_and_endpoints(db, auth_id="auth1", ep_ids=("ep1",))
        ep1 = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep1").first()
        db.delete(ep1)
        # Without exclude_ep_id, the un-flushed pending delete leaves ep1 visible
        # to the reference-count SELECT (autoflush=False), so the helper must
        # conservatively KEEP the auth row. This is the bug exclude_ep_id fixes.
        assert _delete_orphaned_provider_auth(db, "auth1") is False
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is not None
    finally:
        db.close()


def test_delete_orphaned_provider_auth_keeps_auth_while_another_endpoint_uses_it(monkeypatch):
    from routes.model_routes import _delete_orphaned_provider_auth

    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        _add_auth_and_endpoints(db, auth_id="auth1", ep_ids=("ep1", "ep2"))
        # ep2 still references auth1, so deleting ep1 must NOT revoke it.
        assert _delete_orphaned_provider_auth(db, "auth1", exclude_ep_id="ep1") is False
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is not None
    finally:
        db.close()


def test_delete_orphaned_provider_auth_noop_without_auth_id(monkeypatch):
    from routes.model_routes import _delete_orphaned_provider_auth

    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        assert _delete_orphaned_provider_auth(db, None, exclude_ep_id="ep1") is False
    finally:
        db.close()


def test_delete_orphaned_provider_auth_noop_when_auth_row_missing(monkeypatch):
    from routes.model_routes import _delete_orphaned_provider_auth

    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        # Endpoint points at an auth_id whose ProviderAuthSession is already gone.
        db.add(ModelEndpoint(
            id="ep1", name="ChatGPT Subscription",
            base_url="https://chatgpt.com/backend-api/codex",
            provider_auth_id="ghost", owner="alice",
        ))
        db.commit()
        ep1 = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep1").first()
        db.delete(ep1)
        # No other endpoint references "ghost" and no auth row exists → no-op, no error.
        assert _delete_orphaned_provider_auth(db, "ghost", exclude_ep_id="ep1") is False
    finally:
        db.close()


def _delete_route(monkeypatch, TestSessionLocal):
    """Resolve the real DELETE /model-endpoints/{ep_id} route, wired to the test DB.

    Neutralizes the route's unrelated cleanup side effects (settings/prefs files,
    in-memory session manager) so the test stays hermetic and focuses on the
    provider-auth revocation wiring.
    """
    import routes.model_routes as mr
    import routes.prefs_routes as prefs_routes
    import src.ai_interaction as ai_interaction

    monkeypatch.setattr(mr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(mr, "require_admin", lambda request: None)
    monkeypatch.setattr(mr, "_load_settings", lambda: {})
    monkeypatch.setattr(mr, "_save_settings", lambda settings: None)
    monkeypatch.setattr(prefs_routes, "_load", lambda: {})
    monkeypatch.setattr(prefs_routes, "_save", lambda prefs: None)
    monkeypatch.setattr(ai_interaction, "get_session_manager", lambda: None)

    router = mr.setup_model_routes(model_discovery=None)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/model-endpoints/{ep_id}" and "DELETE" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("DELETE /api/model-endpoints/{ep_id} not found")


def test_delete_endpoint_route_revokes_orphaned_provider_auth(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        _add_auth_and_endpoints(db, auth_id="auth1", ep_ids=("ep1",))
    finally:
        db.close()

    delete_endpoint = _delete_route(monkeypatch, TestSessionLocal)
    result = delete_endpoint("ep1", SimpleNamespace(state=SimpleNamespace(current_user="alice")))

    assert result["deleted"] is True
    # The last (only) endpoint backed by auth1 is gone, so the route revokes it.
    assert result["cleared_provider_auth"] is True
    db = TestSessionLocal()
    try:
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is None
        assert db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep1").first() is None
    finally:
        db.close()


def test_delete_endpoint_route_keeps_auth_when_shared(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        _add_auth_and_endpoints(db, auth_id="auth1", ep_ids=("ep1", "ep2"))
    finally:
        db.close()

    delete_endpoint = _delete_route(monkeypatch, TestSessionLocal)
    result = delete_endpoint("ep1", SimpleNamespace(state=SimpleNamespace(current_user="alice")))

    assert result["deleted"] is True
    # ep2 still references auth1, so deleting ep1 must NOT revoke the credentials.
    assert result["cleared_provider_auth"] is False
    db = TestSessionLocal()
    try:
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is not None
    finally:
        db.close()


def test_delete_orphaned_provider_auth_revokes_only_after_last_of_several(monkeypatch):
    from routes.model_routes import _delete_orphaned_provider_auth

    TestSessionLocal = _mem_db(monkeypatch)
    db = TestSessionLocal()
    try:
        _add_auth_and_endpoints(db, auth_id="auth1", ep_ids=("ep1", "ep2"))

        # Delete ep1 first: ep2 still references auth1, so the row survives.
        ep1 = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep1").first()
        db.delete(ep1)
        assert _delete_orphaned_provider_auth(db, "auth1", exclude_ep_id="ep1") is False
        db.commit()
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is not None

        # Now delete the last endpoint ep2: the auth row is finally cleared.
        ep2 = db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep2").first()
        db.delete(ep2)
        assert _delete_orphaned_provider_auth(db, "auth1", exclude_ep_id="ep2") is True
        db.commit()
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == "auth1").first() is None
    finally:
        db.close()


def test_delete_account_a_preserves_account_b(monkeypatch):
    """Deleting one subscription clears only its own orphaned auth row."""
    TestSessionLocal = _mem_db(monkeypatch)
    a = _connect(monkeypatch, "bob", label="codex00", tokens={"access_token": "A-AT", "refresh_token": "A-RT"})
    b = _connect(monkeypatch, "bob", label="codex01", tokens={"access_token": "B-AT", "refresh_token": "B-RT"})

    delete_endpoint = _delete_route(monkeypatch, TestSessionLocal)
    result = delete_endpoint(a["id"], SimpleNamespace(state=SimpleNamespace(current_user="bob")))
    assert result["deleted"] is True
    assert result["cleared_provider_auth"] is True

    db = TestSessionLocal()
    try:
        assert db.query(ModelEndpoint).filter(ModelEndpoint.id == a["id"]).first() is None
        assert db.query(ProviderAuthSession).filter(ProviderAuthSession.id == a["provider_auth_id"]).first() is None
        ep_b = db.query(ModelEndpoint).filter(ModelEndpoint.id == b["id"]).first()
        auth_b = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == b["provider_auth_id"]).first()
        assert ep_b is not None and ep_b.is_enabled is True
        assert auth_b is not None and auth_b.access_token == "B-AT" and auth_b.refresh_token == "B-RT"
    finally:
        db.close()


def test_manual_model_refresh_uses_the_endpoints_own_auth_session(monkeypatch):
    """Refreshing A's models resolves A's bearer and never touches B."""
    import routes.model_routes as mr
    import src.endpoint_resolver as endpoint_resolver

    TestSessionLocal = _mem_db(monkeypatch)
    a = _connect(monkeypatch, "bob", label="codex00", models=("gpt-5.5",))
    b = _connect(monkeypatch, "bob", label="codex01", models=("gpt-5.5",))
    monkeypatch.setattr(mr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(mr, "require_admin", lambda request: None)

    resolved = []

    def fake_runtime(ep, owner=None):
        resolved.append((ep.id, ep.provider_auth_id, owner))
        return (ep.base_url, f"bearer-{ep.provider_auth_id}")

    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", fake_runtime)
    probed = []

    def fake_probe(base, api_key=None, timeout=5):
        probed.append(api_key)
        return ["gpt-5.5", "gpt-5.5-codex"] if api_key == f"bearer-{a['provider_auth_id']}" else []

    monkeypatch.setattr(mr, "_probe_endpoint", fake_probe)

    router = mr.setup_model_routes(model_discovery=None)
    list_models = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/model-endpoints/{ep_id}/models" and "GET" in getattr(r, "methods", set())
    )

    class _Resp:
        headers = {}

    rows = list_models(a["id"], SimpleNamespace(state=SimpleNamespace(current_user="bob")), _Resp(), refresh=True)
    assert resolved == [(a["id"], a["provider_auth_id"], "bob")]
    assert probed == [f"bearer-{a['provider_auth_id']}"]
    assert {r["id"] for r in rows} == {"gpt-5.5", "gpt-5.5-codex"}

    db = TestSessionLocal()
    try:
        ep_a = db.query(ModelEndpoint).filter(ModelEndpoint.id == a["id"]).first()
        ep_b = db.query(ModelEndpoint).filter(ModelEndpoint.id == b["id"]).first()
        assert json.loads(ep_a.cached_models) == ["gpt-5.5", "gpt-5.5-codex"]
        assert json.loads(ep_b.cached_models) == ["gpt-5.5"]  # untouched
    finally:
        db.close()


def test_endpoint_listing_exposes_account_metadata_without_credentials(monkeypatch):
    import routes.model_routes as mr

    TestSessionLocal = _mem_db(monkeypatch)
    a = _connect(monkeypatch, "bob", label="codex00")
    _connect(monkeypatch, "bob", label="codex01")
    monkeypatch.setattr(mr, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(mr, "require_admin", lambda request: None)
    router = mr.setup_model_routes(model_discovery=None)
    list_endpoints = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/model-endpoints" and "GET" in getattr(r, "methods", set())
    )
    rows = list_endpoints(SimpleNamespace(state=SimpleNamespace(current_user="bob")))
    by_id = {r["id"]: r for r in rows}
    assert by_id[a["id"]]["provider"] == "chatgpt-subscription"
    assert by_id[a["id"]]["provider_auth_id"] == a["provider_auth_id"]
    assert by_id[a["id"]]["account_label"] == "codex00"
    assert by_id[a["id"]]["supports_tools"] is False
    assert by_id[a["id"]]["has_key"] is False
    labels = sorted(r["account_label"] for r in rows)
    assert labels == ["codex00", "codex01"]
    dumped = json.dumps(rows)
    assert "AT-" not in dumped and "RT-" not in dumped and "refresh_token" not in dumped
