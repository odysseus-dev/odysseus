"""Tests for scheme-aware Secure cookie logic (FIX-1 / UX-P4-01).

Tests the module-level ``_should_use_secure_cookie(request)`` helper in
``routes/auth_routes.py``.  This helper is called by the login endpoint to
decide whether the session cookie carries the ``Secure`` flag.

Decision rules:
  1. SECURE_COOKIES=true  → True   (explicit operator override)
  2. SECURE_COOKIES=false → False  (explicit opt-out, e.g. plain-HTTP dev)
  3. Not set, https scheme          → True  (auto-detect)
  4. Not set, http scheme           → False (localhost Quick Start)
  5. Not set, X-Forwarded-Proto: https → True  (reverse-proxy aware)
  6. Not set, X-Forwarded-Proto: http  → False
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# The helper lives in routes/auth_routes.py which imports fastapi at the top
# level.  Fastapi is not installed in the test environment, so conftest stubs
# it as a MagicMock. That means APIRouter(), HTTPException, etc. are all mocks
# and the module imports successfully.  We only need to pull the pure-Python
# ``_should_use_secure_cookie`` symbol out of the module.
# ---------------------------------------------------------------------------


def _ensure_stub(name: str, **attrs):
    """Create or augment a stub module — same pattern as test_auth_regressions."""
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        if parent_name not in sys.modules:
            parent = types.ModuleType(parent_name)
            real_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                *parent_name.split("."),
            )
            parent.__path__ = [real_path] if os.path.isdir(real_path) else []
            sys.modules[parent_name] = parent
        else:
            parent = sys.modules[parent_name]
    else:
        parent = None
        child_name = None

    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    if parent is not None and child_name and not hasattr(parent, child_name):
        setattr(parent, child_name, mod)
    return mod


@pytest.fixture(autouse=True)
def _setup_stubs(monkeypatch):
    """Ensure core stubs are present before each test and purge the cached module."""
    _ensure_stub("core.database",
        SessionLocal=MagicMock(), McpServer=MagicMock(), Session=MagicMock(),
        ChatMessage=MagicMock(), Document=MagicMock(),
    )
    _ensure_stub("core.auth", AuthManager=MagicMock())
    _ensure_stub("src.endpoint_resolver",
        resolve_endpoint=MagicMock(return_value=("", "", {})),
        normalize_base=MagicMock(),
        build_chat_url=MagicMock(),
        build_models_url=MagicMock(),
        build_headers=MagicMock(),
    )
    # Force re-import of auth_routes so each test sees fresh env vars
    monkeypatch.delitem(sys.modules, "routes.auth_routes", raising=False)
    yield


def _get_helper():
    """Import auth_routes and return the cookie-decision helper."""
    from routes.auth_routes import _should_use_secure_cookie
    return _should_use_secure_cookie


def _req(scheme="http", forwarded_proto=None):
    """Minimal request stand-in with just the attributes the helper reads."""
    req = SimpleNamespace()
    req.url = SimpleNamespace(scheme=scheme)
    headers = {}
    if forwarded_proto is not None:
        headers["x-forwarded-proto"] = forwarded_proto
    req.headers = headers
    return req


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_explicit_true_forces_secure(monkeypatch):
    """SECURE_COOKIES=true forces Secure=True regardless of scheme."""
    monkeypatch.setenv("SECURE_COOKIES", "true")
    fn = _get_helper()
    assert fn(_req(scheme="http")) is True


def test_explicit_false_forces_insecure(monkeypatch):
    """SECURE_COOKIES=false forces Secure=False regardless of scheme."""
    monkeypatch.setenv("SECURE_COOKIES", "false")
    fn = _get_helper()
    assert fn(_req(scheme="https")) is False


def test_auto_https_scheme(monkeypatch):
    """No override + https request scheme → Secure=True."""
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    fn = _get_helper()
    assert fn(_req(scheme="https")) is True


def test_auto_http_scheme(monkeypatch):
    """No override + http request (localhost Quick Start) → Secure=False."""
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    fn = _get_helper()
    assert fn(_req(scheme="http")) is False


def test_forwarded_proto_https(monkeypatch):
    """No override + X-Forwarded-Proto: https (reverse proxy) → Secure=True."""
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    fn = _get_helper()
    assert fn(_req(scheme="http", forwarded_proto="https")) is True


def test_forwarded_proto_http(monkeypatch):
    """No override + X-Forwarded-Proto: http → Secure=False."""
    monkeypatch.delenv("SECURE_COOKIES", raising=False)
    fn = _get_helper()
    assert fn(_req(scheme="http", forwarded_proto="http")) is False
