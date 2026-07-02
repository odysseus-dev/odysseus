"""Tests for API token CRUD route handlers.

Covers GET /api/tokens, POST /api/tokens, DELETE /api/tokens/{token_id}.
Uses direct endpoint extraction from setup_api_token_routes().routes and
fake objects only — no real DB, no network, no external services.
"""

import asyncio
import contextlib
import datetime
import secrets as _secrets_mod
import sys
import types
import uuid as _uuid_mod
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Fixture: install per-test stubs via monkeypatch so they are torn down
# automatically and never leak into sibling tests in the same pytest session.
# ---------------------------------------------------------------------------


@pytest.fixture
def token_routes_mod(monkeypatch):
    """Yield routes.api_token_routes imported under isolated module stubs.

    Two stubs are required:
    - python_multipart: FastAPI validates Form() params at router-registration
      time and raises RuntimeError when the package is absent.
    - core.database: the real module declares SQLAlchemy ORM models at import
      time; the conftest sqlalchemy stubs cause a metaclass conflict.

    Both are installed with monkeypatch.setitem so they are restored after
    each test without touching any other test's module state.
    """
    # python-multipart stub
    mp_stub = types.ModuleType("python_multipart")
    mp_stub.__version__ = "0.0.13"
    monkeypatch.setitem(sys.modules, "python_multipart", mp_stub)

    # core.database stub: __getattr__ resolves any ORM name to a MagicMock
    class _DBStub(types.ModuleType):
        def __getattr__(self, name):
            return MagicMock()

    @contextlib.contextmanager
    def _noop_db_session():
        yield MagicMock()

    db_stub = _DBStub("core.database")
    db_stub.get_db_session = _noop_db_session
    db_stub.ApiToken = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db_stub)

    # Force a fresh import so the route module binds to the stubbed core.database
    monkeypatch.delitem(sys.modules, "routes.api_token_routes", raising=False)

    import routes.api_token_routes as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# Pure helpers — no module-level side effects
# ---------------------------------------------------------------------------


def _admin_mgr(is_admin: bool):
    return SimpleNamespace(is_admin=lambda u: is_admin, is_configured=True)


def _req(current_user: str, *, is_admin: bool = False, invalidator=None):
    app_state = SimpleNamespace(auth_manager=_admin_mgr(is_admin))
    if invalidator is not None:
        app_state.invalidate_token_cache = invalidator
    return SimpleNamespace(
        state=SimpleNamespace(current_user=current_user),
        headers={},
        app=SimpleNamespace(state=app_state),
    )


def _get_handler(mod, method: str, path_pattern: str):
    """Extract a route endpoint from setup_api_token_routes() by method and path fragment."""
    router = mod.setup_api_token_routes()
    for route in router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path_pattern in path and method.upper() in methods:
            return route.endpoint
    raise KeyError(f"No {method} route matching '{path_pattern}'")


@contextlib.contextmanager
def _db_ctx(session):
    yield session


# ---------------------------------------------------------------------------
# 1. Admin gate — all three endpoints reject non-admin callers
# ---------------------------------------------------------------------------


def test_api_token_routes_require_admin_for_list_create_delete(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    list_tokens = _get_handler(mod, "GET", "/tokens")
    create_token = _get_handler(mod, "POST", "/tokens")
    delete_token = _get_handler(mod, "DELETE", "/tokens/{token_id}")

    non_admin = _req("bob", is_admin=False)

    for handler, kwargs in [
        (list_tokens, {"request": non_admin}),
        (create_token, {"request": non_admin, "name": "my-token"}),
        (delete_token, {"request": non_admin, "token_id": "abc12345"}),
    ]:
        with pytest.raises(HTTPException) as exc:
            handler(**kwargs)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 2. POST /api/tokens — owner attribution, hashed at rest, raw returned once
# ---------------------------------------------------------------------------


def test_create_token_attributes_owner_hashes_secret_and_returns_raw_once(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    fake_suffix = "FAKESUFFIX_XXXXXXXXXXXXXXXXXXXXXXXXXX"
    fake_uuid_str = "abcd1234-0000-0000-0000-000000000000"
    fake_hash = b"$2b$12$FAKEHASHVALUE"

    monkeypatch.setattr(_secrets_mod, "token_urlsafe", lambda n: fake_suffix)

    class _FakeUUID:
        def __str__(self):
            return fake_uuid_str

    monkeypatch.setattr(_uuid_mod, "uuid4", _FakeUUID)

    fake_bcrypt = SimpleNamespace(
        hashpw=lambda pw, salt: fake_hash,
        gensalt=lambda: b"fakesalt",
    )
    monkeypatch.setattr(mod, "bcrypt", fake_bcrypt)

    captured = {}

    class _FakeApiToken:
        def __init__(self, **kw):
            captured.clear()
            captured.update(kw)
            self.__dict__.update(kw)

    fake_session = MagicMock()
    monkeypatch.setattr(mod, "ApiToken", _FakeApiToken)
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)

    invalidator = MagicMock()
    req = _req("alice", is_admin=True, invalidator=invalidator)
    create_token = _get_handler(mod, "POST", "/tokens")
    resp = create_token(request=req, name="my-token")

    expected_raw = "ody_" + fake_suffix
    expected_prefix = expected_raw[:8]
    expected_id = fake_uuid_str[:8]

    assert resp["token"] == expected_raw
    assert resp["token"].startswith("ody_")
    assert resp["token_prefix"] == expected_prefix
    assert resp["id"] == expected_id
    assert resp["owner"] == "alice"
    assert resp["scopes"] == ["chat"]

    assert captured["owner"] == "alice"
    assert captured["scopes"] == "chat"
    assert captured["is_active"] is True
    assert captured["token_hash"] == fake_hash.decode()
    assert captured["token_hash"] != expected_raw
    assert captured["token_prefix"] == expected_prefix

    invalidator.assert_called_once()


def test_create_token_accepts_cookbook_read_scope(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    fake_session = MagicMock()
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)

    req = _req("alice", is_admin=True)
    create_token = _get_handler(mod, "POST", "/tokens")
    resp = create_token(request=req, name="cookbook-reader", scopes="cookbook:read")

    assert resp["scopes"] == ["cookbook:read"]


def test_cookbook_launch_scope_implies_read(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    fake_session = MagicMock()
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)

    req = _req("alice", is_admin=True)
    create_token = _get_handler(mod, "POST", "/tokens")
    resp = create_token(request=req, name="cookbook-launcher", scopes="cookbook:launch")

    assert resp["scopes"] == ["cookbook:read", "cookbook:launch"]


# ---------------------------------------------------------------------------
# 3. GET /api/tokens — safe display fields only, no hash or raw token
# ---------------------------------------------------------------------------


def test_list_tokens_returns_safe_display_fields_only(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)

    row1 = SimpleNamespace(
        id="tok001",
        name="Production",
        owner="alice",
        token_prefix="ody_prod",
        token_hash="$2b$12$SHOULDNEVERAPPEAR",
        scopes="chat,research",
        is_active=True,
        last_used_at=datetime.datetime(2024, 1, 15, 10, 0),
        created_at=datetime.datetime(2024, 1, 1, 0, 0),
    )
    # Empty scopes should default to ["chat"]
    row2 = SimpleNamespace(
        id="tok002",
        name="Empty scopes",
        owner="bob",
        token_prefix="ody_empt",
        token_hash="$2b$12$ALSONEVERSHOWN",
        scopes="",
        is_active=False,
        last_used_at=None,
        created_at=datetime.datetime(2024, 2, 1, 0, 0),
    )

    fake_session = MagicMock()
    fake_session.query.return_value.all.return_value = [row1, row2]
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    req = _req("alice", is_admin=True)
    list_tokens = _get_handler(mod, "GET", "/tokens")
    result = list_tokens(request=req)

    assert len(result) == 2

    safe_fields = {"id", "name", "owner", "token_prefix", "scopes", "is_active", "last_used_at", "created_at"}
    for item in result:
        assert set(item.keys()) == safe_fields
        assert "token" not in item
        assert "token_hash" not in item

    assert result[0]["scopes"] == ["chat", "research"]
    assert result[1]["scopes"] == ["chat"]


# ---------------------------------------------------------------------------
# 4. DELETE /api/tokens/{id} — found → deleted + cache invalidated
# ---------------------------------------------------------------------------


def test_delete_token_deletes_and_invalidates_cache(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)
    monkeypatch.setattr(mod, "ApiToken", MagicMock())

    fake_token = SimpleNamespace(id="abcd1234", owner="alice", name="test")
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = fake_token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _req("alice", is_admin=True, invalidator=invalidator)
    delete_token = _get_handler(mod, "DELETE", "/tokens/{token_id}")
    resp = delete_token(request=req, token_id="abcd1234")

    assert resp == {"status": "deleted"}
    fake_session.delete.assert_called_once_with(fake_token)
    invalidator.assert_called_once()


# ---------------------------------------------------------------------------
# 5. DELETE /api/tokens/{id} — not found → 404, cache NOT invalidated
# ---------------------------------------------------------------------------


def test_delete_missing_token_returns_404_without_invalidating_cache(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)
    monkeypatch.setattr(mod, "ApiToken", MagicMock())

    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _req("alice", is_admin=True, invalidator=invalidator)
    delete_token = _get_handler(mod, "DELETE", "/tokens/{token_id}")

    with pytest.raises(HTTPException) as exc:
        delete_token(request=req, token_id="missing99")
    assert exc.value.status_code == 404
    invalidator.assert_not_called()


# ---------------------------------------------------------------------------
# 6. PATCH /api/tokens/{id} — a partial update must not wipe scopes
# ---------------------------------------------------------------------------


def _patch_request(invalidator, body):
    """An admin request whose async .json() yields `body`."""
    req = _req("alice", is_admin=True, invalidator=invalidator)

    async def _json():
        return body

    req.json = _json
    return req


def test_update_token_rename_preserves_scopes(monkeypatch, token_routes_mod):
    """Renaming a token (no 'scopes' key in the body) must keep its scopes.

    Previously update_token recomputed scopes from payload.get("scopes"),
    which is None on a rename, so _normalize_scopes(None) reset every token to
    the default ["chat"] scope — a silent privilege/data loss.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    token = SimpleNamespace(
        id="tok123", name="original", owner="alice",
        token_prefix="ody_orig", scopes="email:read,email:draft", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _patch_request(invalidator, {"name": "renamed"})
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    resp = asyncio.run(update_token(request=req, token_id="tok123"))

    assert token.scopes == "email:read,email:draft"  # untouched
    assert resp["scopes"] == ["email:read", "email:draft"]
    assert token.name == "renamed"
    invalidator.assert_called_once()


def test_update_token_applies_explicit_scopes(monkeypatch, token_routes_mod):
    """When the body includes 'scopes', they are normalized and written."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    token = SimpleNamespace(
        id="tok123", name="original", owner="alice",
        token_prefix="ody_orig", scopes="email:read,email:draft", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    req = _patch_request(MagicMock(), {"scopes": ["chat"]})
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    resp = asyncio.run(update_token(request=req, token_id="tok123"))

    assert token.scopes == "chat"
    assert resp["scopes"] == ["chat"]


def test_update_missing_token_returns_404(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    req = _patch_request(MagicMock(), {"name": "x"})
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_token(request=req, token_id="missing99"))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 7. Owner check — update/delete reject a different admin's token with 403
# ---------------------------------------------------------------------------


def _bob_patch_request(invalidator, body):
    """An admin request from bob whose async .json() yields `body`."""
    req = _req("bob", is_admin=True, invalidator=invalidator)

    async def _json():
        return body

    req.json = _json
    return req


def test_update_token_rejects_non_owner(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)

    token = SimpleNamespace(
        id="tok123", name="alice-token", owner="alice",
        token_prefix="ody_alic", scopes="chat", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    req = _bob_patch_request(MagicMock(), {"name": "hijacked"})
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(update_token(request=req, token_id="tok123"))
    assert exc.value.status_code == 403
    assert token.name == "alice-token"


def test_delete_token_rejects_non_owner(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: req.state.current_user)
    monkeypatch.setattr(mod, "ApiToken", MagicMock())

    fake_token = SimpleNamespace(id="tok123", owner="alice", name="alice-token")
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = fake_token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _req("bob", is_admin=True, invalidator=invalidator)
    delete_token = _get_handler(mod, "DELETE", "/tokens/{token_id}")
    with pytest.raises(HTTPException) as exc:
        delete_token(request=req, token_id="tok123")
    assert exc.value.status_code == 403
    fake_session.delete.assert_not_called()
    invalidator.assert_not_called()


def test_update_token_owner_check_skipped_when_auth_disabled(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: None)

    token = SimpleNamespace(
        id="tok123", name="original", owner="alice",
        token_prefix="ody_alic", scopes="chat", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    req = _bob_patch_request(MagicMock(), {"name": "renamed-in-single-user"})
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    resp = asyncio.run(update_token(request=req, token_id="tok123"))
    assert resp["name"] == "renamed-in-single-user"


def test_delete_token_owner_check_skipped_when_auth_disabled(monkeypatch, token_routes_mod):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    mod = token_routes_mod
    monkeypatch.setattr(mod, "get_current_user", lambda req: None)
    monkeypatch.setattr(mod, "ApiToken", MagicMock())

    fake_token = SimpleNamespace(id="tok123", owner="alice", name="alice-token")
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = fake_token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _req("", is_admin=True, invalidator=invalidator)
    delete_token = _get_handler(mod, "DELETE", "/tokens/{token_id}")
    resp = delete_token(request=req, token_id="tok123")
    assert resp == {"status": "deleted"}
    fake_session.delete.assert_called_once_with(fake_token)


# ---------------------------------------------------------------------------
# 7. PATCH /api/tokens/{id} — non-object JSON bodies must not 500
# ---------------------------------------------------------------------------


def test_update_token_with_array_body_does_not_500(monkeypatch, token_routes_mod):
    """PATCH body of [] must be normalised to {} and not raise."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    token = SimpleNamespace(
        id="tok123", name="original", owner="alice",
        token_prefix="ody_orig", scopes="email:read", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _patch_request(invalidator, [])
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    resp = asyncio.run(update_token(request=req, token_id="tok123"))

    # Name and scopes must be unchanged — payload was normalised to {}
    assert token.name == "original"
    assert token.scopes == "email:read"
    assert resp["name"] == "original"


def test_update_token_with_null_body_does_not_500(monkeypatch, token_routes_mod):
    """PATCH body of null must be normalised to {} and not raise."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    token = SimpleNamespace(
        id="tok123", name="original", owner="alice",
        token_prefix="ody_orig", scopes="chat", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _patch_request(invalidator, None)
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    resp = asyncio.run(update_token(request=req, token_id="tok123"))

    assert token.name == "original"
    assert token.scopes == "chat"


def test_update_token_normal_object_still_works(monkeypatch, token_routes_mod):
    """Normal dict payload continues to update fields as before."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mod = token_routes_mod

    token = SimpleNamespace(
        id="tok123", name="original", owner="alice",
        token_prefix="ody_orig", scopes="email:read", is_active=True,
    )
    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(mod, "get_db_session", lambda: _db_ctx(fake_session))

    invalidator = MagicMock()
    req = _patch_request(invalidator, {"name": "updated"})
    update_token = _get_handler(mod, "PATCH", "/tokens/{token_id}")
    resp = asyncio.run(update_token(request=req, token_id="tok123"))

    assert token.name == "updated"
    assert resp["name"] == "updated"
    invalidator.assert_called_once()
