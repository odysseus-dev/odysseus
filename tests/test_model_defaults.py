"""Tests for share_defaults_with_users setting"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.helpers.import_state import preserve_import_state
from tests.helpers.db_stubs import make_core_db_stub

with preserve_import_state("core.database", "src.database", "routes.model_routes", "routes.prefs_routes"):
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes
    import src.auth_helpers as auth_helpers


### Helper Classes

class _FakeEndpoint:
    """Minimal fake endpoint for testing"""
    def __init__(self, id, base_url, is_enabled=True, owner=None):
        self.id = id
        self.base_url = base_url
        self.is_enabled = is_enabled
        self.owner = owner
        self.cached_models = None
        self.hidden_models = None
        self.pinned_models = None


class _FakeQuery:
    """Fake query object for testing"""
    def __init__(self, endpoints, user=None, include_shared=True):
        self._endpoints = endpoints
        self._user = user
        self._include_shared = include_shared
    
    def filter(self, *conditions):
        for cond in conditions:
            cond_str = str(cond)
            print(f"Filter condition: {cond_str}")
            if 'owner' in cond_str and 'IS NULL' not in cond_str:
                self._include_shared = False
        return self
    
    def first(self):
        """Return first endpoint respecting owner filter"""
        if not self._endpoints:
            return None
        
        if self._user:
            for ep in self._endpoints:
                ep_owner = getattr(ep, 'owner', None)
                if ep_owner == self._user:
                    return ep
                if self._include_shared and ep_owner is None:
                    return ep
            return None
        return self._endpoints[0]


def _make_db_session(endpoints, user=None):
    """Create a fake DB session that returns our fake query"""
    fake_session = MagicMock()
    fake_query = _FakeQuery(endpoints, user)
    fake_session.query.return_value = fake_query
    return fake_session


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
def _run_get_default_chat_test(monkeypatch, share_defaults_enabled, second_endpoint_only=False):
    """Helper function that runs get_default_chat with the given share_defaults_with_users setting."""
    
    global_settings = {
        "default_endpoint_id": "global-ep-123",
        "default_model": "qwen-3.6",
        "default_model_fallbacks": [
            {"endpoint_id": "fallback-ep", "model": "fallback-model"}
        ],
        "share_defaults_with_users": share_defaults_enabled
    }

    monkeypatch.setattr(model_routes, "_load_settings", lambda: global_settings)
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda user: {})
    
    fake_auth_manager = MagicMock()
    fake_auth_manager.is_admin = lambda user: False
    
    endpoints = [
        _FakeEndpoint(
            id="global-ep-123",
            base_url="http://global-endpoint:8000/v1",
            is_enabled=True
        ),
        _FakeEndpoint(
            id="fallback-ep",
            base_url="http://fallback-endpoint:8000/v1",
            is_enabled=True
        )
    ]
    
    # When testing fallback scenario, removes the primary endpoint
    if second_endpoint_only:
        endpoints = [endpoints[1]]
    
    fake_db = _make_db_session(endpoints, user="regular_user")
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url)
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat")
    
    router = model_routes.setup_model_routes(model_discovery=None)
    get_default_chat = _get_default_chat_route(router)
    fake_request = _make_request(user="regular_user", auth_manager=fake_auth_manager)
    
    result = get_default_chat(fake_request)
    
    return result

### Test Functions

def test_get_default_chat_user_no_prefs_share_disabled_resolves_nothing(monkeypatch):
    """
    Non-admin user without personal preferences should resolve to empty
    ep_id, model, and fallbacks when share_defaults_with_users is disabled.
    """

    test_data = _run_get_default_chat_test(monkeypatch, share_defaults_enabled=False)
    
    assert test_data["endpoint_id"] == "", "Should get empty endpoint_id"
    assert test_data["model"] == "", "Should get empty model"


def test_get_default_chat_user_no_prefs_share_enabled_resolves_global_defaults_fallbacks(monkeypatch):
    """
    Non-admin user without personal preferences should resolve to global
    defaults for ep_id, model, and fallbacks when share_defaults_with_users is enabled.
    """

    test_data = _run_get_default_chat_test(monkeypatch, share_defaults_enabled=True)
    
    assert test_data["model"] == "qwen-3.6", \
        "model should be resolved from global default_model"
    
    assert test_data["endpoint_id"] == "global-ep-123", \
        "Should get global endpoint_id"
    
def test_get_default_chat_user_no_prefs_share_enabled_resolves_global_defaults(monkeypatch):
    """
    Non-admin user without personal preferences should resolve to global
    defaults for ep_id, model, and fallbacks when share_defaults_with_users is enabled.
    """

    test_data = _run_get_default_chat_test(monkeypatch, share_defaults_enabled=True, second_endpoint_only=True)
    
    assert test_data["model"] == "qwen-3.6", \
        "model should be resolved from global default_model"
    
    assert test_data["endpoint_id"] == "fallback-ep", \
        "Should get global endpoint_id"