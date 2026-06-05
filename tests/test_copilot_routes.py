"""DB-backed tests for Copilot endpoint provisioning (routes/copilot_routes.py)."""
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint
import routes.copilot_routes as cr


def _mem_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(cr, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def test_provision_creates_owner_scoped_endpoint(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    monkeypatch.setattr(
        cr.copilot, "fetch_models",
        lambda base, token: [
            {"id": "gpt-4o", "tool_calls": True, "vision": True},
            {"id": "claude-3.5", "tool_calls": True, "vision": False},
        ],
    )

    res = cr._provision_endpoint("GHTOK", "https://api.githubcopilot.com", "alice")

    assert res["base_url"] == "https://api.githubcopilot.com"
    assert res["models"] == ["gpt-4o", "claude-3.5"]

    db = TestSessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == res["id"]).first()
        assert ep is not None
        assert ep.owner == "alice"
        assert ep.is_enabled is True
        assert ep.supports_tools is True
        assert ep.api_key == "GHTOK"  # round-trips through EncryptedText
        assert json.loads(ep.cached_models) == ["gpt-4o", "claude-3.5"]
    finally:
        db.close()


def test_provision_refreshes_existing_token(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)
    monkeypatch.setattr(cr.copilot, "fetch_models", lambda base, token: [{"id": "gpt-4o", "tool_calls": True}])

    first = cr._provision_endpoint("OLD", "https://api.githubcopilot.com", "bob")
    second = cr._provision_endpoint("NEW", "https://api.githubcopilot.com", "bob")

    # Same row reused (no duplicate), token refreshed.
    assert first["id"] == second["id"]
    db = TestSessionLocal()
    try:
        rows = db.query(ModelEndpoint).filter(ModelEndpoint.owner == "bob").all()
        assert len(rows) == 1
        assert rows[0].api_key == "NEW"
    finally:
        db.close()


def test_provision_handles_model_fetch_failure(monkeypatch):
    TestSessionLocal = _mem_db(monkeypatch)

    def boom(base, token):
        raise RuntimeError("network down")

    monkeypatch.setattr(cr.copilot, "fetch_models", boom)
    # Should still create the endpoint (login succeeded) with an empty model list.
    res = cr._provision_endpoint("GHTOK", "https://api.githubcopilot.com", "carol")
    assert res["models"] == []
    db = TestSessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == res["id"]).first()
        assert ep is not None and ep.api_key == "GHTOK"
    finally:
        db.close()
