"""Regression: login/setup/signup must not 500 when request.client is None.

The rate-limit gate read `request.client.host` directly. Starlette leaves
`request.client` as None for some ASGI servers, proxies, and test clients, so an
unguarded `.host` raised AttributeError and turned login/setup/signup into a 500.
The rest of the project already guards this (src/auth_helpers.py, app.py); this
pins the auth routes to the same behavior.

Reuses the stub + endpoint-extraction harness from test_auth_regressions.py.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from tests.test_auth_regressions import _auth_route_endpoint
from routes.auth_routes import LoginRequest


def test_login_does_not_crash_when_request_client_is_none():
    auth_manager, login = _auth_route_endpoint("/api/auth/login", "POST")
    auth_manager.verify_password.return_value = False  # force the fast 401 path
    req = SimpleNamespace(client=None, cookies={})
    body = LoginRequest(username="alice", password="password123", totp_code=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(login(body=body, request=req, response=Response()))
    # Must reach the credential check (401), not raise AttributeError from
    # request.client.host in the rate-limit gate before it.
    assert exc.value.status_code == 401
