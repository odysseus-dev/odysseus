"""New usernames must be usable as a URL path segment and not carry control characters."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_auth_policy import _auth_module, _make_manager, _setup_endpoint, _signup_endpoint

BAD = ["a/b", "../x", "a\\b", "tab\tname", "new\nline", "..", ".", "u" * 65]


@pytest.mark.parametrize("name", BAD)
def test_username_error_rejects(name):
    assert _auth_module().username_error(name)


@pytest.mark.parametrize("name", ["alice", "Bob Smith", "j.doe", "user@example.com", "日本語", "u" * 64])
def test_username_error_accepts(name):
    assert _auth_module().username_error(name) is None


def test_username_error_requires_a_name():
    assert _auth_module().username_error("   ") == "Username is required"


@pytest.mark.parametrize("name", ["a/b", "u" * 65])
def test_setup_rejects_bad_username(tmp_path, name):
    mgr = _make_manager(tmp_path)
    endpoint, SetupRequest = _setup_endpoint(mgr)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=SetupRequest(username=name, password="long-enough-pw"), request=request))

    assert exc.value.status_code == 400
    assert not mgr.is_configured


def test_signup_rejects_bad_username(tmp_path):
    mgr = _make_manager(tmp_path)
    mgr.create_user("admin", "admin-password", is_admin=True)
    mgr.signup_enabled = True
    endpoint, SignupRequest = _signup_endpoint(mgr)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(body=SignupRequest(username="a/b", password="long-enough-pw"), request=request))

    assert exc.value.status_code == 400
    assert "a/b" not in mgr.users
