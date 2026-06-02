"""Owner-scope tests for the read-only companion bridge.

Mirrors the direct-helper style of tests/test_null_owner_gates.py: exercise the
small pure helpers against mock request state and owner values, so the scoping
rule can't silently regress. A bearer token for owner A must never see owner B's
rows, and legacy null-owner rows must not widen a token's access.
"""

import os
import sys
import types
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# core.database instantiates SQLAlchemy declarative classes at import time, which
# blows up under conftest's sqlalchemy MagicMock stubs. companion.routes only
# imports it lazily inside the /models handler, but stub it defensively so the
# import is robust regardless of collection order.
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["core.database"] = _db

import companion.routes as companion_routes
from companion.routes import setup_companion_routes, token_owner, owner_can_see


def _request(**state):
    return SimpleNamespace(state=SimpleNamespace(**state))


class _Predicate:
    def __init__(self, check):
        self._check = check

    def __call__(self, row):
        return self._check(row)

    def __or__(self, other):
        return _Predicate(lambda row: self(row) or other(row))


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):  # noqa: D401
        return _Predicate(lambda row: getattr(row, self.name) == value)


class _ModelEndpoint:
    is_enabled = _Column("is_enabled")
    model_type = _Column("model_type")
    owner = _Column("owner")


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates):
        self._rows = [
            row for row in self._rows
            if all(predicate(row) for predicate in predicates)
        ]
        return self

    def all(self):
        return list(self._rows)


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def query(self, model):
        assert model is _ModelEndpoint
        return _Query(self._rows)

    def close(self):
        self.closed = True


def _ep(
    id,
    name,
    owner,
    *,
    is_enabled=True,
    model_type="llm",
    base_url=None,
    cached_models=None,
    hidden_models=None,
    supports_tools=False,
    api_key="secret-key",
):
    return SimpleNamespace(
        id=id,
        name=name,
        owner=owner,
        is_enabled=is_enabled,
        model_type=model_type,
        base_url=base_url or f"https://{name}.example/v1",
        cached_models=json.dumps(cached_models or [f"{name}-model"]),
        hidden_models=json.dumps(hidden_models or []),
        supports_tools=supports_tools,
        api_key=api_key,
        headers={"Authorization": "Bearer secret-header"},
    )


def _models_route():
    for route in setup_companion_routes().routes:
        if getattr(route, "path", "") == "/api/companion/models":
            assert "GET" in getattr(route, "methods", set())
            return route.endpoint
    raise AssertionError("GET /api/companion/models route not found")


def _call_models_route(monkeypatch, rows, request):
    db = _DB(rows)
    db_mod = sys.modules["core.database"]
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: db)
    monkeypatch.setattr(db_mod, "ModelEndpoint", _ModelEndpoint)

    endpoint_mod = sys.modules.get("src.endpoint_resolver")
    if endpoint_mod is None:
        endpoint_mod = types.ModuleType("src.endpoint_resolver")
        sys.modules["src.endpoint_resolver"] = endpoint_mod
    monkeypatch.setattr(
        endpoint_mod,
        "build_chat_url",
        lambda base_url: f"{base_url.rstrip('/')}/chat/completions",
        raising=False,
    )

    response = _models_route()(request)
    assert db.closed is True
    return response["endpoints"]


def _endpoint_names(endpoints):
    return [endpoint["name"] for endpoint in endpoints]


# --- token_owner: who a request is attributed to ---------------------------

def test_token_owner_bearer_resolves_to_token_owner():
    # A paired bearer caller runs as the "api" pseudo-user, but must attribute
    # to the token's real owner.
    req = _request(api_token=True, api_token_owner="alice", current_user="api")
    assert token_owner(req) == "alice"


def test_token_owner_cookie_uses_logged_in_user():
    req = _request(api_token=False, current_user="alice")
    assert token_owner(req) == "alice"


def test_token_owner_none_when_unresolved():
    req = _request(api_token=True, api_token_owner=None, current_user="api")
    assert token_owner(req) is None


# --- owner_can_see: the read-scope rule ------------------------------------

def test_owner_sees_their_own_rows():
    assert owner_can_see("alice", "alice") is True


def test_null_owner_shared_rows_are_visible():
    # Legacy shared rows (owner is None) are visible to everyone by design...
    assert owner_can_see(None, "alice") is True


def test_null_owner_does_not_widen_access_to_others_rows():
    # ...but a null-owner row must not be a backdoor to another OWNER's rows.
    assert owner_can_see("bob", "alice") is False


def test_cross_owner_is_blocked():
    assert owner_can_see("bob", "alice") is False
    assert owner_can_see("alice", "bob") is False


def test_unauthenticated_owner_sees_only_shared_rows():
    # owner=None (no resolved caller): only null-owner shared rows are visible,
    # never any owned row.
    assert owner_can_see(None, None) is True
    assert owner_can_see("alice", None) is False


# --- GET /api/companion/models: route-level scoping -----------------------

def test_models_route_scopes_cookie_user_to_owned_and_shared_rows(monkeypatch):
    rows = [
        _ep(1, "alice-endpoint", "alice"),
        _ep(2, "shared-endpoint", None),
        _ep(3, "bob-endpoint", "bob"),
    ]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "alice")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=False, current_user="ignored"),
    )

    assert _endpoint_names(endpoints) == ["alice-endpoint", "shared-endpoint"]


def test_models_route_scopes_api_token_to_token_owner(monkeypatch):
    rows = [
        _ep(1, "alice-endpoint", "alice"),
        _ep(2, "shared-endpoint", None),
        _ep(3, "bob-endpoint", "bob"),
    ]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "api")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=True, api_token_owner="alice", current_user="api"),
    )

    assert _endpoint_names(endpoints) == ["alice-endpoint", "shared-endpoint"]


def test_models_route_unresolved_owner_returns_only_shared_rows(monkeypatch):
    rows = [
        _ep(1, "alice-endpoint", "alice"),
        _ep(2, "shared-endpoint", None),
        _ep(3, "bob-endpoint", "bob"),
    ]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: None)

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=True, api_token_owner=None, current_user="api"),
    )

    assert _endpoint_names(endpoints) == ["shared-endpoint"]


def test_models_route_filters_hidden_models_and_secret_fields(monkeypatch):
    rows = [
        _ep(
            1,
            "alice-endpoint",
            "alice",
            base_url="https://alice.example/v1",
            cached_models=["visible-model", "hidden-model"],
            hidden_models=["hidden-model"],
            supports_tools=True,
            api_key="super-secret",
        ),
    ]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "alice")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=False, current_user="alice"),
    )

    assert endpoints == [{
        "endpoint_id": 1,
        "name": "alice-endpoint",
        "endpoint_url": "https://alice.example/v1/chat/completions",
        "models": ["visible-model"],
        "supports_tools": True,
    }]
    returned = endpoints[0]
    assert "hidden-model" not in returned["models"]
    assert set(returned) == {
        "endpoint_id",
        "name",
        "endpoint_url",
        "models",
        "supports_tools",
    }
    assert "api_key" not in returned
    assert "headers" not in returned
    assert "base_url" not in returned
    assert "super-secret" not in repr(returned)


def test_models_route_tolerates_invalid_cached_models_json(monkeypatch):
    endpoint = _ep(1, "alice-endpoint", "alice")
    endpoint.cached_models = "{not-json"
    rows = [endpoint]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "alice")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=False, current_user="alice"),
    )

    assert len(endpoints) == 1
    returned = endpoints[0]
    assert returned["name"] == "alice-endpoint"
    assert returned["models"] == []
    assert "api_key" not in returned
    assert "headers" not in returned
    assert "base_url" not in returned


def test_models_route_tolerates_invalid_hidden_models_json(monkeypatch):
    endpoint = _ep(
        1,
        "alice-endpoint",
        "alice",
        cached_models=["visible-model"],
    )
    endpoint.hidden_models = "{not-json"
    rows = [endpoint]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "alice")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=False, current_user="alice"),
    )

    assert len(endpoints) == 1
    returned = endpoints[0]
    assert returned["name"] == "alice-endpoint"
    assert returned["models"] == ["visible-model"]
    assert "api_key" not in returned
    assert "headers" not in returned
    assert "base_url" not in returned


def test_models_route_filters_disabled_and_non_llm_endpoints(monkeypatch):
    rows = [
        _ep(1, "enabled-llm", "alice", is_enabled=True, model_type="llm"),
        _ep(2, "legacy-null-type", "alice", is_enabled=True, model_type=None),
        _ep(3, "disabled-llm", "alice", is_enabled=False, model_type="llm"),
        _ep(4, "image-endpoint", "alice", is_enabled=True, model_type="image"),
    ]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "alice")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=False, current_user="alice"),
    )

    assert _endpoint_names(endpoints) == ["enabled-llm", "legacy-null-type"]


def test_models_route_returns_built_chat_url(monkeypatch):
    rows = [
        _ep(1, "alice-endpoint", "alice", base_url="https://raw.example/v1"),
    ]
    monkeypatch.setattr(companion_routes, "get_current_user", lambda request: "alice")

    endpoints = _call_models_route(
        monkeypatch,
        rows,
        _request(api_token=False, current_user="alice"),
    )

    assert endpoints[0]["endpoint_url"] == "https://raw.example/v1/chat/completions"
    assert endpoints[0]["endpoint_url"] != "https://raw.example/v1"
