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
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from routes.auth_routes import setup_auth_routes, LoginRequest


def _login_endpoint(auth_manager):
    router = setup_auth_routes(auth_manager)
    for r in router.routes:
        if getattr(r, "path", None) == "/api/auth/login" and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("login route not found on the auth router")


def test_login_runs_bcrypt_off_the_event_loop():
    loop_thread = threading.get_ident()
    seen = {}

    auth = MagicMock()

    def _verify(username, password):
        seen["verify_thread"] = threading.get_ident()
        return True

    def _create(username, password):
        seen["create_thread"] = threading.get_ident()
        return "tok-123"

    auth.verify_password.side_effect = _verify
    auth.totp_enabled.return_value = False
    auth.create_session.side_effect = _create

    login = _login_endpoint(auth)

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.7"), cookies={})
    response = MagicMock()
    body = LoginRequest(username="alice", password="hunter2", remember=True)

    result = asyncio.run(login(body=body, request=request, response=response))

    assert result["ok"] is True
    auth.verify_password.assert_called_once()
    auth.create_session.assert_called_once()
    # The whole point: the expensive bcrypt calls must NOT run on the loop thread.
    assert seen["verify_thread"] != loop_thread, "verify_password ran on the event-loop thread"
    assert seen["create_thread"] != loop_thread, "create_session ran on the event-loop thread"
