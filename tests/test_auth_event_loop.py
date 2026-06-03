"""Pin that the login handler keeps bcrypt off the event loop.

`/api/auth/login` is an `async def` and is reachable unauthenticated. bcrypt
(`checkpw`/`hashpw`) is deliberately CPU-expensive (~100-300 ms). Running it
directly in the coroutine blocks the single event loop for that whole window,
freezing every other in-flight request (chat streams, polling, ...). Because
the endpoint is unauthenticated and rate-limited only per-IP, a burst of login
attempts serializes the whole server — a cheap DoS-amplification vector.

The fix offloads the bcrypt-bearing AuthManager calls via asyncio.to_thread.
This test asserts those calls run on a worker thread, not the loop thread; it
fails if they are awaited inline again.
"""
import os
import sys
import types
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock


# Stub `core.auth` / `core.database` before importing the route module.
# `routes.auth_routes` does `from core.auth import AuthManager`, and importing
# any `core.*` submodule first runs `core/__init__.py`, which transitively
# imports `src.llm_core` (hangs at import under the project venv) and the
# SQLAlchemy declarative models (metaclass blows up on a bare `core.database`
# import / under the conftest's `sqlalchemy.*` MagicMock stubs). We only need
# `AuthManager` as a type hint here — the handler is exercised with a MagicMock
# — so stub the heavy modules out. Same trick as test_auth_regressions.py /
# test_null_owner_gates.py.
def _ensure_stub(name: str, **attrs):
    """Create or augment a stub module, wiring it onto a stubbed parent package.

    Augments existing entries because an earlier-run test may have already
    stubbed the same module with a different attribute set. The parent package
    gets `__path__` pointed at the real on-disk dir so genuinely-unstubbed
    submodules still load normally, while `core/__init__.py` itself is bypassed
    (the package is already in `sys.modules`)."""
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
    if parent is not None and not hasattr(parent, child_name):
        setattr(parent, child_name, mod)
    return mod


@pytest.fixture(autouse=True)
def _event_loop_stubs(monkeypatch):
    db = _ensure_stub("core.database", SessionLocal=MagicMock())
    auth = _ensure_stub("core.auth", AuthManager=MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)
    monkeypatch.setitem(sys.modules, "core.auth", auth)


from routes.auth_routes import setup_auth_routes, LoginRequest


def _login_endpoint(auth_manager):
    router = setup_auth_routes(auth_manager)
    for r in router.routes:
        if getattr(r, "path", None) == "/api/auth/login" and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("login route not found on the auth router")


def test_login_offloads_bcrypt_bearing_calls(monkeypatch):
    calls = []
    auth = MagicMock()

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append(fn)
        return fn(*args, **kwargs)

    monkeypatch.setattr("routes.auth_routes.asyncio.to_thread", fake_to_thread)
    auth.verify_password.return_value = True
    auth.totp_enabled.return_value = False
    auth.create_session.return_value = "tok-123"

    login = _login_endpoint(auth)

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"), cookies={})
    response = MagicMock()
    body = LoginRequest(username="alice", password="hunter2", remember=True)

    result = asyncio.run(login(body=body, request=request, response=response))

    assert result["ok"] is True
    auth.verify_password.assert_called_once()
    auth.create_session.assert_called_once()
    # The whole point: the expensive bcrypt-bearing calls go through
    # asyncio.to_thread rather than running inline in the request coroutine.
    assert calls == [auth.verify_password, auth.create_session]
