"""DB-backed ChatGPT Subscription endpoint provisioning tests."""

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
    monkeypatch.setattr(
        csr.chatgpt_subscription,
        "_database_handles",
        lambda: (ProviderAuthSession, TestSessionLocal, lambda: None),
    )
    return TestSessionLocal


def test_provision_creates_owner_scoped_connection_projection(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)

    res = csr._provision_connection(
        {"connection_id": "conn-alice", "status": "usable", "entitlement": "codex"},
        "alice",
    )

    assert res["connection_id"] == "conn-alice"
    assert res["status"] == "usable"
    assert "access_token" not in res
    assert "refresh_token" not in res

    db = TestSessionLocal()
    try:
        auth = db.query(ProviderAuthSession).first()
        assert auth is not None
        assert auth.owner == "alice"
        assert auth.connection_id == "conn-alice"
        assert auth.status == "usable"
        assert auth.access_token is None
        assert auth.refresh_token is None
        assert db.query(ModelEndpoint).count() == 0
    finally:
        db.close()


def test_provision_refreshes_existing_connection_projection(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)

    first = csr._provision_connection(
        {"connection_id": "conn-bob", "status": "usable", "entitlement": "codex"},
        "bob",
    )
    second = csr._provision_connection(
        {"connection_id": "conn-bob", "status": "usable", "entitlement": "plus"},
        "bob",
    )

    assert first["connection_id"] == second["connection_id"]
    db = TestSessionLocal()
    try:
        auth_rows = db.query(ProviderAuthSession).filter(ProviderAuthSession.owner == "bob").all()
        assert len(auth_rows) == 1
        assert auth_rows[0].entitlement == "plus"
        assert auth_rows[0].access_token is None
        assert auth_rows[0].refresh_token is None
        assert db.query(ModelEndpoint).filter(ModelEndpoint.owner == "bob").count() == 0
    finally:
        db.close()


def test_provision_rejects_token_pump_payload(monkeypatch):
    _mem_db(monkeypatch)
    with pytest.raises(ValueError, match="connection_id"):
        csr._provision_endpoint({"access_token": "AT", "refresh_token": "RT"}, "alice")


def test_provision_requires_opaque_connection_id(monkeypatch):
    _mem_db(monkeypatch)
    with pytest.raises(ValueError, match="connection_id"):
        csr._provision_connection({"status": "usable"}, "alice")


def _add_auth_and_endpoints(db, *, auth_id="auth1", ep_ids=("ep1",)):
    db.add(ProviderAuthSession(
        id=auth_id, provider=csr.chatgpt_subscription.CHATGPT_SUBSCRIPTION_PROVIDER,
        owner="alice", base_url="https://chatgpt.com/backend-api/codex",
        auth_mode="projection",
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
    result = delete_endpoint("ep1", object())

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
    result = delete_endpoint("ep1", object())

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
