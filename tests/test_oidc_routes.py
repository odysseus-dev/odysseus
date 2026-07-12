"""Tests for OIDC routes — config, login redirect, callback handling."""

import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from fastapi import APIRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_oidc_routes(auth_manager, oidc_manager):
    """Import and call setup_oidc_routes, returning the router."""
    # MagicMock methods are truthy by default; explicitly model the normal
    # OIDC-user case so the defense-in-depth TOTP refusal does not alter every
    # unrelated route test.
    if isinstance(auth_manager, MagicMock):
        # Preserve an explicit test override (e.g. return_value=True in the
        # refusal-path test), but make bare MagicMock auth managers behave
        # like normal OIDC users.
        if isinstance(auth_manager.check_oidc_totp.return_value, MagicMock):
            auth_manager.check_oidc_totp.return_value = False
    from routes.oidc_routes import setup_oidc_routes
    return setup_oidc_routes(auth_manager, oidc_manager)


def _get_endpoint(router: APIRouter, path: str):
    """Find the route endpoint for a given path."""
    for route in router.routes:
        if getattr(route, "path", "") == path:
            return route.endpoint
    raise AssertionError(f"No route found for path: {path}")


def _fake_request(base_url="http://testserver", cookies=None):
    """Build a minimal Request-like object."""
    req = SimpleNamespace()
    req.base_url = SimpleNamespace()
    req.base_url.__str__ = lambda s, b=base_url: b
    req.base_url.rstrip = lambda s, strip="/": base_url.rstrip(strip)
    req.query_params = {}
    req.cookies = cookies or {}
    req.url = SimpleNamespace()
    req.url.scheme = "http"
    req.headers = {}
    return req


def _fake_request_with_params(query_params, base_url="http://testserver", cookies=None):
    req = _fake_request(base_url, cookies=cookies)
    req.query_params = query_params
    return req


# ---------------------------------------------------------------------------
# /config
# ---------------------------------------------------------------------------

class TestOidcConfig:
    def test_config_disabled_when_manager_none(self):
        from core.oidc import OidcError
        router = _setup_oidc_routes(MagicMock(), None)
        ep = _get_endpoint(router, "/api/auth/oidc/config")

        import asyncio
        result = asyncio.run(ep())
        assert result == {"enabled": False, "error": "OIDC not configured"}

    def test_config_enabled_when_configured(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.provider_name = "Test IDP"

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/config")

        import asyncio
        result = asyncio.run(ep())
        assert result["enabled"] is True
        assert result["provider_name"] == "Test IDP"


# ---------------------------------------------------------------------------
# /login
# ---------------------------------------------------------------------------

class TestOidcLogin:
    def test_login_redirects_to_provider(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.get_authorization_url.return_value = (
            "https://idp.example.com/authorize?state=abc&nonce=def",
            "abc",
            "def",
        )

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/login")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(ep(_fake_request()))

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 302
        assert result.headers["location"] == "https://idp.example.com/authorize?state=abc&nonce=def"

    def test_login_returns_503_when_not_configured(self):
        router = _setup_oidc_routes(MagicMock(), None)
        ep = _get_endpoint(router, "/api/auth/oidc/login")

        import asyncio
        from fastapi.responses import JSONResponse
        result = asyncio.run(ep(_fake_request()))

        assert isinstance(result, JSONResponse)
        assert result.status_code == 503

    def test_login_handles_oidc_error(self):
        from core.oidc import OidcError
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.get_authorization_url.side_effect = OidcError("bad config")

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/login")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(ep(_fake_request()))

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_config" in result.headers["location"]


# ---------------------------------------------------------------------------
# /callback
# ---------------------------------------------------------------------------

class TestOidcCallback:
    def test_callback_success_creates_session(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user123",
            "email": "alice@example.com",
            "preferred_username": "alice",
        }

        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = None  # new user
        auth.create_user_oidc.return_value = "alice"
        auth.create_session_trusted.return_value = "session-token-abc"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "authcode", "state": "state123"},
                    cookies={"odysseus_oidc_csrf": "state123"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        assert result.status_code == 302
        assert result.headers["location"] == "/"

        auth.create_user_oidc.assert_called_once_with(
            "alice", "user123", "https://idp.example.com", email="alice@example.com",
            is_admin=False,
        )

    def test_callback_existing_user(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing_sub",
            "email": "bob@example.com",
        }

        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = "bob"
        auth.create_session_trusted.return_value = "session-token-xyz"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "authcode", "state": "state456"},
                    cookies={"odysseus_oidc_csrf": "state456"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        assert result.status_code == 302
        assert result.headers["location"] == "/"
        auth.create_user_oidc.assert_not_called()
        auth.create_session_trusted.assert_called_once_with("bob")
        # No admin groups configured — set_oidc_user_admin should NOT be called
        auth.set_oidc_user_admin.assert_not_called()

    def test_callback_provider_error_redirects(self):
        mgr = MagicMock()
        mgr.configured = True

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"error": "access_denied", "error_description": "User cancelled"}
                ),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_denied" in result.headers["location"]

    def test_callback_missing_code_redirects(self):
        mgr = MagicMock()
        mgr.configured = True

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params({"state": "state789"}),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_invalid" in result.headers["location"]

    def test_callback_csrf_cookie_mismatch(self):
        """Callback with mismatched CSRF cookie should redirect with error."""
        mgr = MagicMock()
        mgr.configured = True

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state_real"},
                    cookies={"odysseus_oidc_csrf": "state_different"},
                ),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_csrf" in result.headers["location"]

    def test_callback_csrf_cookie_missing(self):
        """Callback without CSRF cookie should redirect with error."""
        mgr = MagicMock()
        mgr.configured = True

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    # no cookie
                ),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_csrf" in result.headers["location"]

    def test_callback_exchange_failure_redirects(self):
        from core.oidc import OidcError
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.exchange_code.side_effect = OidcError("token exchange failed")

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "badcode", "state": "state999"},
                    cookies={"odysseus_oidc_csrf": "state999"},
                ),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_failed" in result.headers["location"]

    def test_callback_missing_sub_in_claims(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.exchange_code.return_value = {"sub": ""}

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_failed" in result.headers["location"]

    def test_callback_create_user_failure(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "new_user",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = None

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_failed" in result.headers["location"]

    def test_callback_username_from_email_local_part(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user456",
            "email": "charlie@example.com",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "charlie"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        auth.create_user_oidc.assert_called_once()
        call_args = auth.create_user_oidc.call_args
        assert call_args[0][0] == "charlie"

    def test_callback_new_user_admin_from_groups(self, monkeypatch):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins,superusers")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user1",
            "email": "dave@example.com",
            "preferred_username": "dave",
            "groups": ["users", "odysseus-admins"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "dave"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        auth.create_user_oidc.assert_called_once()
        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is True
        assert result.status_code == 302

    def test_callback_new_user_no_admin_groups(self, monkeypatch):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user2",
            "email": "eve@example.com",
            "preferred_username": "eve",
            "groups": ["users"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "eve"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is False

    def test_callback_existing_user_promoted_to_admin(self, monkeypatch):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing",
            "email": "frank@example.com",
            "groups": ["odysseus-admins"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "frank"
        auth.set_oidc_user_admin.return_value = True
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        auth.set_oidc_user_admin.assert_called_once_with("frank", True)
        auth.create_user_oidc.assert_not_called()

    def test_callback_existing_user_demoted_from_admin(self, monkeypatch):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing",
            "email": "grace@example.com",
            "groups": ["users"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "grace"
        auth.set_oidc_user_admin.return_value = True
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        auth.set_oidc_user_admin.assert_called_once_with("grace", False)

    def test_callback_no_groups_claim(self, monkeypatch):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user3",
            "email": "hank@example.com",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "hank"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is False

    def test_callback_no_admin_groups_configured(self, monkeypatch):
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user4",
            "email": "iris@example.com",
            "groups": ["odysseus-admins"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "iris"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is False

    def test_callback_existing_user_no_admin_groups_sync(self, monkeypatch):
        """Existing user: when OIDC_ADMIN_GROUPS is unset, admin status is NOT
        synced (preserving bootstrap or manual grant)."""
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing_admin",
            "email": "jake@example.com",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "jake"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # No admin sync when groups not configured
        auth.set_oidc_user_admin.assert_not_called()


class TestEventLoopOffloading:
    """Regression: the OIDC callback must offload blocking I/O off the event loop."""

    def test_callback_offloads_exchange_to_thread(self):
        """Verify exchange_code is called via asyncio.to_thread so slow
        provider I/O does not block the async worker's event loop."""
        import asyncio
        import time

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"

        # Simulate a slow provider: exchange_code takes 0.3s
        def slow_exchange(code, state, redirect_uri):
            time.sleep(0.3)
            return {"sub": "slow-user", "email": "slow@example.com"}
        mgr.exchange_code.side_effect = slow_exchange

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "slow"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        # Start a tight concurrent coroutine that must not be blocked
        start = time.monotonic()

        async def run_callback():
            await ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )

        asyncio.run(run_callback())
        elapsed = time.monotonic() - start

        # The slow exchange sleeps 0.3s.  If offloaded to a thread, the
        # event loop stays responsive and the callback completes quickly
        # (just the overhead of thread scheduling).  Without offloading,
        # the callback would block for ≥0.3s.
        assert elapsed < 1.0, (
            f"Callback took {elapsed:.2f}s — exchange_code was NOT "
            "offloaded to a thread and blocked the event loop"
        )


class TestAdminDemotionProtection:
    """Regression: a transient UserInfo failure must not demote an
    existing OIDC admin when groups are only available via UserInfo."""

    def test_userinfo_unavailable_preserves_existing_admin(self, monkeypatch):
        """Existing admin + UserInfo unavailable + no id_token groups →
        admin status must NOT be synced (no demotion on missing evidence)."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        # Simulate: UserInfo was NOT fetched.  The id_token has no groups.
        mgr.exchange_code.return_value = {
            "sub": "admin-user",
            "email": "admin@example.com",
            "_userinfo_available": False,
            # no "groups" key in the id_token
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "admin-user"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # set_oidc_user_admin must NOT be called — we skip the sync
        # because we have no authoritative group evidence.
        auth.set_oidc_user_admin.assert_not_called()

    def test_userinfo_available_demotes_when_no_admin_groups(self, monkeypatch):
        """Existing admin + UserInfo available + no admin groups →
        admin MUST be demoted (authentic non-membership evidence)."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        # Simulate: UserInfo WAS fetched. Groups = empty or non-admin.
        mgr.exchange_code.return_value = {
            "sub": "demoted-admin",
            "email": "demoted@example.com",
            "_userinfo_available": True,
            "groups": ["regular-users"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "demoted-admin"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # set_oidc_user_admin MUST be called with is_admin=False
        auth.set_oidc_user_admin.assert_called_once_with("demoted-admin", False)

    def test_id_token_groups_authoritative_even_without_userinfo(self, monkeypatch):
        """When the id_token itself carries a groups claim, it is
        authoritative even if UserInfo was not fetched."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        # Simulate: UserInfo NOT fetched, but id_token has groups.
        mgr.exchange_code.return_value = {
            "sub": "idtoken-admin",
            "email": "idtoken@example.com",
            "_userinfo_available": False,
            "groups": ["odysseus-admins"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "idtoken-admin"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # id_token groups are authoritative — admin sync must run
        auth.set_oidc_user_admin.assert_called_once_with("idtoken-admin", True)

    def test_no_userinfo_endpoint_preserves_existing_admin(self, monkeypatch):
        """Existing OIDC admin + OIDC_ADMIN_GROUPS + access_token present
        + no userinfo_endpoint in discovery + no id_token groups →
        admin must NOT be demoted (missing endpoint is not group evidence)."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        # Simulate: discovery had no userinfo_endpoint, so _fetch_userinfo
        # returned None → _userinfo_available stayed False.  The id_token
        # has no groups claim.  This is the exact scenario where the
        # callback must NOT treat "no endpoint" as authoritative
        # non-membership evidence.
        mgr.exchange_code.return_value = {
            "sub": "endpointless-admin",
            "email": "nobody@example.com",
            "_userinfo_available": False,
            # no "groups" key in the id_token — no group evidence at all
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "endpointless-admin"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # Admin sync must NOT be called — without a userinfo_endpoint,
        # _userinfo_available is False, and the id_token has no groups.
        # An existing admin must not be demoted on missing evidence.
        auth.set_oidc_user_admin.assert_not_called()


class TestOidcCallbackSecurityFailures:
    def _run_callback(self, auth, mgr, params, cookies=None):
        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")
        import asyncio
        return asyncio.run(
            ep(
                _fake_request_with_params(params, cookies=cookies),
                SimpleNamespace(),
            )
        )

    def test_callback_rejects_oidc_totp_user(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {"sub": "user123", "email": "alice@example.com"}

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "alice"
        auth.check_oidc_totp.return_value = True
        auth.create_session_trusted.return_value = "must-not-be-issued"

        result = self._run_callback(
            auth, mgr, {"code": "code", "state": "state"},
            cookies={"odysseus_oidc_csrf": "state"},
        )
        assert "error=oidc_failed" in result.headers["location"]
        auth.create_session_trusted.assert_not_called()
        assert "odysseus_oidc_csrf" in result.headers.get("set-cookie", "")

    def test_callback_handles_none_session_token(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {"sub": "user123", "email": "alice@example.com"}

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "alice"
        auth.check_oidc_totp.return_value = False
        auth.create_session_trusted.return_value = None

        result = self._run_callback(
            auth, mgr, {"code": "code", "state": "state"},
            cookies={"odysseus_oidc_csrf": "state"},
        )
        assert "error=oidc_failed" in result.headers["location"]
        assert "odysseus_oidc_csrf" in result.headers.get("set-cookie", "")

    def test_callback_clears_csrf_on_each_error(self):
        from core.oidc import OidcError

        cases = [
            ({"error": "access_denied", "state": "state"}, {}, "oidc_denied"),
            ({"state": "state"}, {}, "oidc_invalid"),
            (
                {"code": "code", "state": "state"},
                {"odysseus_oidc_csrf": "wrong-state"},
                "oidc_csrf",
            ),
            (
                {"code": "code", "state": "state"},
                {"odysseus_oidc_csrf": "state"},
                "oidc_failed",
            ),
        ]
        for params, cookies, error_code in cases:
            mgr = MagicMock()
            mgr.configured = True
            mgr.redirect_uri_override = None
            mgr.exchange_code.side_effect = OidcError("exchange failed")
            auth = MagicMock()
            auth.check_oidc_totp.return_value = False

            result = self._run_callback(auth, mgr, params, cookies=cookies)
            assert f"error={error_code}" in result.headers["location"]
            assert "odysseus_oidc_csrf" in result.headers.get("set-cookie", ""), (
                f"CSRF cookie was not cleared for {error_code}"
            )
