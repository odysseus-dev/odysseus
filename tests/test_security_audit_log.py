"""Tests for the security audit log feature."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, SecurityEvent


@pytest.fixture(scope="function")
def db_session(monkeypatch):
    # Create a dedicated engine instead of reusing core.database.engine.
    # Changing DATABASE_URL after core.database has been imported does not
    # rebind that engine; reusing it here would point drop_all() at live data.
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    monkeypatch.setattr("core.database.SessionLocal", lambda: session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def _request_with_meta():
    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.headers.get.return_value = "pytest"
    return req


def test_log_security_event_creates_row(db_session, monkeypatch):
    from src import security_audit as sa

    req = _request_with_meta()

    event_id = sa.log_security_event(
        sa.LOGIN_SUCCESS,
        actor="alice",
        target="web",
        request=req,
        detail="user agent test",
    )

    assert event_id
    row = db_session.query(SecurityEvent).filter(SecurityEvent.id == event_id).first()
    assert row is not None
    assert row.event_type == "login.success"
    assert row.actor == "alice"
    assert row.target == "web"
    assert row.success is True
    assert row.ip == "127.0.0.1"
    assert row.user_agent == "pytest"
    assert "user agent test" in row.detail_text


def test_log_security_event_failure(db_session, monkeypatch):
    from src import security_audit as sa

    req = _request_with_meta()

    event_id = sa.log_security_event(
        sa.LOGIN_FAILURE,
        actor="bob",
        success=False,
        detail="bad password",
        request=req,
    )

    row = db_session.query(SecurityEvent).filter(SecurityEvent.id == event_id).first()
    assert row is not None
    assert row.success is False
    assert row.event_type == "login.failure"


def test_log_security_event_no_request(db_session, monkeypatch):
    from src import security_audit as sa

    event_id = sa.log_security_event(sa.USER_CREATED, actor="admin", target="carol")
    row = db_session.query(SecurityEvent).filter(SecurityEvent.id == event_id).first()
    assert row is not None
    assert row.event_type == "user.created"
    assert row.actor == "admin"
    assert row.ip is None


def test_log_security_event_never_raises_before_database_write(monkeypatch):
    from src import security_audit as sa

    class _UuidWithoutHex:
        def __str__(self):
            return "fake-uuid"

    monkeypatch.setattr(sa.uuid, "uuid4", _UuidWithoutHex)

    assert sa.log_security_event(sa.LOGIN_SUCCESS, actor="alice") is None


def test_log_security_event_bounds_and_redacts_extra(db_session):
    from src import security_audit as sa

    event_id = sa.log_security_event(
        sa.USER_CREATED,
        actor="a" * 400,
        extra={"api_token": "do-not-store", "note": "n" * 400},
    )
    row = db_session.query(SecurityEvent).filter(SecurityEvent.id == event_id).first()

    assert len(row.actor) == 256
    assert "do-not-store" not in row.detail_text
    assert "api_token=[REDACTED]" in row.detail_text
    assert "note=" + ("n" * 256) in row.detail_text


def _auth_endpoint(router, name):
    return next(route.endpoint for route in router.routes if route.name == name)


@pytest.mark.asyncio
async def test_anonymous_logout_does_not_write_success_audit(monkeypatch):
    from routes.auth_routes import setup_auth_routes
    from src import security_audit as sa

    auth_manager = MagicMock()
    auth_manager.get_username_for_token.return_value = None
    endpoint = _auth_endpoint(setup_auth_routes(auth_manager), "logout")
    audit = AsyncMock()
    monkeypatch.setattr(sa, "log_security_event_async", audit)
    request = MagicMock()
    request.cookies = {}
    response = MagicMock()

    result = await endpoint(request, response)

    assert result == {"ok": True}
    audit.assert_not_awaited()
    response.delete_cookie.assert_called_once_with("odysseus_session", path="/")


@pytest.mark.asyncio
async def test_audit_log_clamps_negative_limit(db_session):
    from routes.auth_routes import setup_auth_routes

    db_session.add_all([
        SecurityEvent(id=f"event-{index}", event_type="login.success", success=True)
        for index in range(3)
    ])
    db_session.commit()

    auth_manager = MagicMock()
    auth_manager.get_username_for_token.return_value = "admin"
    auth_manager.is_admin.return_value = True
    endpoint = _auth_endpoint(setup_auth_routes(auth_manager), "get_audit_log")
    request = MagicMock()
    request.cookies = {"odysseus_session": "session"}
    request.query_params = {"limit": "-1"}

    result = await endpoint(request)

    assert result["limit"] == 1
    assert len(result["events"]) == 1
