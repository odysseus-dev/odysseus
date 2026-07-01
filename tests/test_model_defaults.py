"""Tests for share_defaults_with_users setting"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import preserve_import_state
from tests.helpers.db_stubs import make_core_db_stub

with preserve_import_state("core.database", "src.database", "routes.model_routes", "routes.prefs_routes"):
    from core.database import Base, ModelEndpoint, SessionLocal
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes
    import src.auth_helpers as auth_helpers


### Test Database Setup

def _create_in_memory_db():
    """Create an in-memory SQLite database with the schema"""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return TestingSessionLocal, engine


def _get_default_chat_route(router):
    """Extract the /api/default-chat GET route from the router"""
    for route in router.routes:
        if getattr(route, "path", "") == "/api/default-chat" and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("GET /api/default-chat route not found")


def _make_request(user=None, auth_manager=None):
    """Create a fake request for testing"""
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager)),
        client=SimpleNamespace(host="127.0.0.1"),
    )

### Shared test logic
def _run_get_default_chat_test(monkeypatch, share_defaults_enabled, testing_fallback=False):
    """Helper function that runs get_default_chat with the given share_defaults_with_users setting."""

    global_settings = {
        "default_endpoint_id": "model-2",
        "default_model": "qwen-3.6",
        "default_model_fallbacks": [
            {"endpoint_id": "model-3", "model": "llama-4"}
        ],
        "share_defaults_with_users": share_defaults_enabled
    }

    monkeypatch.setattr(model_routes, "_load_settings", lambda: global_settings)
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda user: {})

    fake_auth_manager = MagicMock()
    fake_auth_manager.is_admin = lambda user: False

    # Create real in-memory database with actual ModelEndpoint rows
    TestingSessionLocal, engine = _create_in_memory_db()
    session = TestingSessionLocal()
    ep1 = ModelEndpoint(id="model-1", name="first", base_url="http://first-endpoint:8000/v1", is_enabled=True)
    ep2 = ModelEndpoint(id="model-2", name="second", base_url="http://second-endpoint:8000/v1", is_enabled=not testing_fallback)
    ep3 = ModelEndpoint(id="model-3", name="third", base_url="http://third-endpoint:8000/v1", is_enabled=True)
    session.add_all([ep1, ep2, ep3])
    session.commit()

    monkeypatch.setattr(model_routes, "SessionLocal", lambda: TestingSessionLocal())
    monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url)
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat")

    router = model_routes.setup_model_routes(model_discovery=None)
    get_default_chat = _get_default_chat_route(router)
    fake_request = _make_request(user="regular_user", auth_manager=fake_auth_manager)

    result = get_default_chat(fake_request)
    session.close()

    return result

### Test Functions

def test_get_default_chat_user_no_prefs_share_disabled_resolves_nothing(monkeypatch):
    """
    Non-admin user without personal preferences should resolve to empty
    ep_id, model, and fallbacks when share_defaults_with_users is disabled.
    """

    test_data = _run_get_default_chat_test(monkeypatch, share_defaults_enabled=False)

    assert test_data["endpoint_id"] == "", "Should get empty endpoint_id and got: "+test_data["endpoint_id"]
    assert test_data["model"] == "", "Should get empty model and got: "+test_data["model"]


def test_get_default_chat_user_no_prefs_share_enabled_resolves_global_defaults_fallbacks(monkeypatch):
    """
    Non-admin user without personal preferences should resolve to global
    defaults for ep_id, model, and fallbacks when share_defaults_with_users is enabled.
    """

    test_data = _run_get_default_chat_test(monkeypatch, share_defaults_enabled=True, testing_fallback=True)

    assert test_data["model"] == "llama-4", \
        "model should be resolved from global default_model and got: "+test_data["model"]

    assert test_data["endpoint_id"] == "model-3", \
        "Should get global endpoint_id and got: "+test_data["endpoint_id"]

def test_get_default_chat_user_no_prefs_share_enabled_resolves_global_defaults(monkeypatch):
    """
    Non-admin user without personal preferences should resolve to global
    defaults for ep_id, model, and fallbacks when share_defaults_with_users is enabled.
    """

    test_data = _run_get_default_chat_test(monkeypatch, share_defaults_enabled=True)

    assert test_data["model"] == "qwen-3.6", \
        "model should be resolved from global default_model and got: "+test_data["model"]

    assert test_data["endpoint_id"] == "model-2", \
        "Should get global endpoint_id and got: "+test_data["endpoint_id"]