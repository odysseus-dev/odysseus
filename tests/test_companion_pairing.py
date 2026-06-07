"""Tests for the companion pairing endpoints (split 3/4).

Covers what the review asked for:
  - a non-admin / bearer caller cannot call /api/companion/pair (admin-only)
  - the pairing token is minted once (hashed at rest) and the mint invalidates
    the auth cache so it works immediately, no restart
  - minting is a POST, never a GET (CSRF: a SameSite=Lax cookie rides a
    top-level GET, so GET-minting would be triggerable by a link / <img>)
"""

import contextlib
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Capture what mint_token would persist, via a stubbed core.database.
_CAPTURED = {}


class _ApiToken:
    def __init__(self, **kw):
        _CAPTURED.clear()
        _CAPTURED.update(kw)
        self.__dict__.update(kw)


@contextlib.contextmanager
def _get_db_session():
    yield MagicMock()


# core/__init__ pulls in models/session_manager which import many ORM names from
# core.database; under conftest's sqlalchemy stubs the real module can't load.
# A __getattr__ module resolves any non-dunder name to a MagicMock, while keeping
# our real get_db_session/ApiToken for the mint test. Dunder names (e.g. __all__)
# are NOT auto-resolved — the next test file does `from core.database import *`,
# which would otherwise see a MagicMock where a list-of-str is required.
class _DBStub(types.ModuleType):
    def __getattr__(self, name):  # noqa: D401
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock()


_db = _DBStub("core.database")
_db.get_db_session = _get_db_session
_db.ApiToken = _ApiToken


@pytest.fixture(autouse=True)
def _companion_pairing_stubs(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", _db)
    for _name, _attrs in {
        "core.auth": {"AuthManager": MagicMock()},
        "src.endpoint_resolver": {"build_chat_url": (lambda u: u)},
    }.items():
        if _name not in sys.modules:
            _mm = types.ModuleType(_name)
            for _k, _v in _attrs.items():
                setattr(_mm, _k, _v)
            sys.modules[_name] = _mm
        monkeypatch.setitem(sys.modules, _name, sys.modules[_name])


from fastapi import HTTPException  # noqa: E402

import companion.pairing as P  # noqa: E402
import companion.routes as R  # noqa: E402
from companion.routes import mint_pairing_token, setup_companion_routes  # noqa: E402
from core.middleware import require_admin  # noqa: E402


# --- token minting: shown once, hashed at rest -----------------------------

def test_mint_token_returns_raw_once_and_stores_only_a_hash(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", _db)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", _db, raising=False)

    token_id, raw = P.mint_token("alice")
    assert raw.startswith("ody_")
    # The persisted row stores a bcrypt hash + prefix, never the plaintext.
    assert _CAPTURED["token_hash"] != raw
    assert _CAPTURED["token_hash"].startswith("$2")  # bcrypt
    assert _CAPTURED["token_prefix"] == raw[:8]
    assert _CAPTURED["owner"] == "alice"
    assert _CAPTURED["scopes"] == "chat"
    assert _CAPTURED["is_active"] is True


def test_mint_pairing_token_invalidates_cache(monkeypatch):
    # The mint must flip the auth middleware's cache so the token works on the
    # very next request, with no restart.
    monkeypatch.setattr(P, "mint_token", lambda owner, name="companion": ("id1", "ody_demo"))
    invalidate = MagicMock()
    token_id, raw = mint_pairing_token("alice", invalidate)
    assert (token_id, raw) == ("id1", "ody_demo")
    invalidate.assert_called_once()


def test_mint_pairing_token_tolerates_no_invalidator(monkeypatch):
    monkeypatch.setattr(P, "mint_token", lambda owner, name="companion": ("id1", "ody_demo"))
    # Must not blow up if the app didn't expose an invalidator.
    assert mint_pairing_token("alice", None) == ("id1", "ody_demo")


def test_pairing_payload_shape():
    p = P.pairing_payload("192.168.1.9", 7000, "ody_x")
    assert p == {"v": 1, "host": "192.168.1.9", "port": 7000, "token": "ody_x"}


@pytest.mark.parametrize("payload", ["[]", '{"users": []}'])
def test_find_admin_user_ignores_invalid_auth_shape(tmp_path, monkeypatch, payload):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "auth.json").write_text(payload)
    monkeypatch.chdir(tmp_path)

    assert P.find_admin_user() is None


# --- admin-only gate: a bearer/non-admin caller is rejected ----------------

def _admin_mgr(is_admin):
    return SimpleNamespace(is_admin=lambda u: is_admin, is_configured=True)


def _req(current_user, *, api_token=False, is_admin=False):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=current_user, api_token=api_token),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=_admin_mgr(is_admin))),
    )


def test_bearer_token_caller_cannot_pair(monkeypatch):
    # Bearer callers come through as the "api" pseudo-user, which is not admin.
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc:
        require_admin(_req("api", api_token=True, is_admin=False))
    assert exc.value.status_code == 403


def test_non_admin_user_cannot_pair(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc:
        require_admin(_req("bob", is_admin=False))
    assert exc.value.status_code == 403


def test_admin_user_passes_the_gate(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    # Should not raise.
    require_admin(_req("alice", is_admin=True))


# --- CSRF: minting is POST, never GET --------------------------------------

def _pair_methods():
    router = setup_companion_routes()
    methods = set()
    for r in router.routes:
        path = getattr(r, "path", "")
        if path.endswith("/pair"):
            methods |= set(getattr(r, "methods", set()) or set())
    return methods


def _pair_route(method):
    for route in setup_companion_routes().routes:
        path = getattr(route, "path", "")
        if path.endswith("/pair") and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} /api/companion/pair route not found")


def _fake_pair_request(format=None, port=7000):
    query_params = {}
    if format is not None:
        query_params["format"] = format
    return SimpleNamespace(
        state=SimpleNamespace(current_user="alice", api_token=False),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=_admin_mgr(True),
                invalidate_token_cache=MagicMock(),
            )
        ),
        query_params=query_params,
        url=SimpleNamespace(port=port),
    )


def test_pair_is_minted_via_post_not_get():
    methods = _pair_methods()
    assert "POST" in methods, "pairing must accept POST (the mint)"
    assert "GET" in methods, "GET should render the form page"
    # The distinction is enforced in the handlers: GET renders a form and never
    # mints; only POST calls mint_pairing_token.


def test_pair_page_uses_imported_admin_gate(monkeypatch):
    monkeypatch.setattr(R, "require_admin", lambda request: None)
    response = _pair_route("GET")(SimpleNamespace())

    assert "Pair a device" in str(getattr(response, "body", response))


def test_pair_get_renders_form_without_minting(monkeypatch):
    mint = MagicMock(side_effect=AssertionError("GET must not mint a token"))
    monkeypatch.setattr(R, "require_admin", lambda request: None, raising=False)
    monkeypatch.setattr(R, "mint_pairing_token", mint)

    response = _pair_route("GET")(_fake_pair_request())
    body = response.body.decode()

    assert response.media_type == "text/html"
    assert '<form method="POST" action="/api/companion/pair">' in body
    assert "Generate pairing code" in body
    mint.assert_not_called()


def test_pair_post_json_returns_pairing_payload(monkeypatch):
    mint = MagicMock(return_value=("tok123", "ody_raw"))
    monkeypatch.setattr(R, "require_admin", lambda request: None, raising=False)
    monkeypatch.setattr(R, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(R, "mint_pairing_token", mint)
    monkeypatch.setattr(R._pairing, "lan_ip_candidates", lambda: ["192.168.1.50"])

    request = _fake_pair_request(format="json", port=7000)
    response = _pair_route("POST")(request)

    mint.assert_called_once_with("alice", request.app.state.invalidate_token_cache)
    assert response["host"] == "192.168.1.50"
    assert response["port"] == 7000
    assert response["token"] == "ody_raw"
    assert response["token_id"] == "tok123"
    assert response["payload"] == {
        "v": 1,
        "host": "192.168.1.50",
        "port": 7000,
        "token": "ody_raw",
    }
    for secret_key in ("token_hash", "token_prefix", "scopes", "is_active", "owner", "name"):
        assert secret_key not in response
        assert secret_key not in response["payload"]


def test_pair_post_json_qr_failure_returns_null_qr(monkeypatch):
    monkeypatch.setattr(R, "require_admin", lambda request: None, raising=False)
    monkeypatch.setattr(R, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(R, "mint_pairing_token", lambda owner, invalidate: ("tok123", "ody_raw"))
    monkeypatch.setattr(R._pairing, "lan_ip_candidates", lambda: ["192.168.1.50"])
    monkeypatch.setattr(R._pairing, "pairing_qr_png_data_uri", lambda payload: None)

    response = _pair_route("POST")(_fake_pair_request(format="json", port=7000))

    assert response["qr"] is None
    assert response["host"] == "192.168.1.50"
    assert response["port"] == 7000
    assert response["token"] == "ody_raw"
    assert response["payload"] == {
        "v": 1,
        "host": "192.168.1.50",
        "port": 7000,
        "token": "ody_raw",
    }


def test_pair_post_html_escapes_pairing_values(monkeypatch):
    monkeypatch.setattr(R, "require_admin", lambda request: None, raising=False)
    monkeypatch.setattr(R, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(R, "mint_pairing_token", lambda owner, invalidate: ("tok<123>", "ody_<raw>&"))
    monkeypatch.setattr(R._pairing, "lan_ip_candidates", lambda: ["host<one>&"])
    monkeypatch.setattr(R._pairing, "pairing_qr_png_data_uri", lambda payload: None)

    response = _pair_route("POST")(_fake_pair_request())
    body = response.body.decode()

    assert response.media_type == "text/html"
    assert "host<one>&" not in body
    assert "ody_<raw>&" not in body
    assert "tok<123>" not in body
    assert "host&lt;one&gt;&amp;" in body
    assert "ody_&lt;raw&gt;&amp;" in body
    assert "tok&lt;123&gt;" in body
