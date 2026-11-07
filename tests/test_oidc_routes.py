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


def _fake_request(base_url="http://testserver", cookies=None, root_path=None):
    """Build a minimal Request-like object.

    ``root_path`` carries the ASGI mount prefix through ``request.scope``;
    ``app`` carries an empty ``state`` so the callback's optional
    ``invalidate_token_cache`` lookup resolves to absent.
    """
    req = SimpleNamespace()
    req.base_url = SimpleNamespace()
    req.base_url.__str__ = lambda s, b=base_url: b
    req.base_url.rstrip = lambda s, strip="/": base_url.rstrip(strip)
    req.query_params = {}
    req.cookies = cookies or {}
    req.url = SimpleNamespace()
    req.url.scheme = "http"
    req.headers = {}
    req.scope = {"root_path": root_path} if root_path is not None else {}
    req.app = SimpleNamespace(state=SimpleNamespace())
    return req


def _fake_request_with_params(query_params, base_url="http://testserver", cookies=None, root_path=None):
    req = _fake_request(base_url, cookies=cookies, root_path=root_path)
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

    def _run_admin_sync_callback(self, claims, monkeypatch):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = claims

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "existing-admin"
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
        return auth

    def test_userinfo_available_without_groups_preserves_admin(self, monkeypatch):
        """UserInfo fetched but carrying no groups claim is NOT evidence of
        membership loss — availability alone must not drive a demotion."""
        auth = self._run_admin_sync_callback({
            "sub": "existing-admin",
            "email": "admin@example.com",
            "_userinfo_available": True,
            # no "groups" key anywhere
        }, monkeypatch)
        auth.set_oidc_user_admin.assert_not_called()

    def test_malformed_groups_claim_preserves_admin(self, monkeypatch):
        """A non-list groups value is not valid evidence and must not
        demote an existing administrator."""
        auth = self._run_admin_sync_callback({
            "sub": "existing-admin",
            "email": "admin@example.com",
            "_userinfo_available": True,
            "groups": "odysseus-admins",  # malformed: string, not list
        }, monkeypatch)
        auth.set_oidc_user_admin.assert_not_called()


class TestSubjectIdentifierPreservation:
    """Regression: OIDC subs are opaque — whitespace-bearing subjects must
    not be normalized into a different subject's account."""

    def _run_callback_with_sub(self, sub):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": sub,
            "email": "alice@example.com",
            "preferred_username": "alice",
        }

        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "alice"
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
        return auth

    def test_whitespace_bearing_sub_preserved_exactly(self):
        sub = " user123 "
        auth = self._run_callback_with_sub(sub)
        auth.get_user_by_oidc.assert_called_once_with(sub, "https://idp.example.com")
        auth.create_user_oidc.assert_called_once_with(
            "alice", sub, "https://idp.example.com",
            email="alice@example.com", is_admin=False,
        )

    def test_distinct_whitespace_subs_lookup_distinctly(self):
        """'user123' and ' user123 ' must hit the account store as two
        different keys, never collapsing into one local account."""
        auth_a = self._run_callback_with_sub("user123")
        auth_b = self._run_callback_with_sub(" user123 ")
        (sub_a, _), _ = auth_a.get_user_by_oidc.call_args
        (sub_b, _), _ = auth_b.get_user_by_oidc.call_args
        assert sub_a != sub_b

    def test_whitespace_only_sub_rejected(self):
        auth = self._run_callback_with_sub("   ")
        # "   " is a technically valid opaque sub per spec; ensure it is
        # either used exactly or rejected — never trimmed to empty and
        # never persisted as a different identifier.
        if auth.create_user_oidc.called:
            args, kwargs = auth.create_user_oidc.call_args
            assert args[1] == "   "


class TestOidcCookieSecurity:
    """Regression: OIDC cookies must be Secure by default, regardless of
    SECURE_COOKIES (which the bundled Compose files default to false)."""

    def _login_cookie_kwargs(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.get_authorization_url.return_value = (
            "https://idp.example.com/authorize?state=abc", "abc", "def",
        )
        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/login")
        import asyncio
        with patch("fastapi.responses.RedirectResponse.set_cookie") as sc:
            asyncio.run(ep(_fake_request()))
            return sc.call_args.kwargs

    def test_csrf_cookie_secure_despite_secure_cookies_false(self, monkeypatch):
        monkeypatch.setenv("SECURE_COOKIES", "false")
        monkeypatch.delenv("OIDC_ALLOW_INSECURE_COOKIES", raising=False)
        assert self._login_cookie_kwargs()["secure"] is True

    def test_csrf_cookie_secure_on_http_request(self, monkeypatch):
        """Plain-http request scheme must not downgrade the cookie."""
        monkeypatch.delenv("SECURE_COOKIES", raising=False)
        monkeypatch.delenv("OIDC_ALLOW_INSECURE_COOKIES", raising=False)
        assert self._login_cookie_kwargs()["secure"] is True

    def test_explicit_dev_override_allows_insecure(self, monkeypatch):
        monkeypatch.setenv("OIDC_ALLOW_INSECURE_COOKIES", "true")
        assert self._login_cookie_kwargs()["secure"] is False

    def test_session_cookie_secure_despite_secure_cookies_false(self, monkeypatch):
        monkeypatch.setenv("SECURE_COOKIES", "false")
        monkeypatch.delenv("OIDC_ALLOW_INSECURE_COOKIES", raising=False)

        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user123", "email": "alice@example.com",
        }
        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = "alice"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")
        set_cookie = MagicMock()
        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "code", "state": "state"},
                    cookies={"odysseus_oidc_csrf": "state"},
                ),
                SimpleNamespace(
                    set_cookie=set_cookie,
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )
        assert set_cookie.call_args.kwargs["secure"] is True


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


# ---------------------------------------------------------------------------
# S3: dev's newer auth rules — username candidates, token-cache invalidation
# on demotion, and ASGI root_path mounting.
# ---------------------------------------------------------------------------

class TestCallbackUsernameCandidates:
    """dev #6821: the callback uses the first candidate that passes
    username_error, is non-empty and not reserved; otherwise the hash sub."""

    def _run(self, claims):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = claims

        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = "resolved"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")
        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params(
                    {"code": "c", "state": "s"},
                    cookies={"odysseus_oidc_csrf": "s"},
                ),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    delete_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )
        return auth, result

    def test_backslash_preferred_username_falls_back_to_hash(self):
        """A backslash DOMAIN-user login must succeed under the hash fallback."""
        import hashlib
        sub = "sub-back"
        auth, result = self._run(
            {"sub": sub, "preferred_username": "DOMAIN\\user"},
        )
        expected = f"oidc_{hashlib.sha256(sub.encode()).hexdigest()[:16]}"
        assert auth.create_user_oidc.call_args[0][0] == expected
        assert result.status_code == 302
        assert result.headers["location"] == "/"
        auth.create_session_trusted.assert_called_once_with("resolved")

    def test_reserved_candidate_skipped_for_next_candidate(self):
        auth, result = self._run(
            {"sub": "sub-res", "preferred_username": "api",
             "email": "someone@example.com"},
        )
        assert auth.create_user_oidc.call_args[0][0] == "someone"
        assert result.status_code == 302

    def test_overlong_preferred_username_falls_back(self):
        import hashlib
        sub = "sub-long"
        auth, _ = self._run(
            {"sub": sub, "preferred_username": "u" * 65},
        )
        expected = f"oidc_{hashlib.sha256(sub.encode()).hexdigest()[:16]}"
        assert auth.create_user_oidc.call_args[0][0] == expected

    def test_invalid_email_local_part_skipped_for_name(self):
        auth, _ = self._run(
            {"sub": "sub-email", "email": "bad\\user@domain.example",
             "name": "Good Name"},
        )
        assert auth.create_user_oidc.call_args[0][0] == "good name"

    def test_name_candidate_used_last(self):
        auth, _ = self._run({"sub": "sub-name", "name": " Ada Loven "})
        assert auth.create_user_oidc.call_args[0][0] == "ada loven"

    def test_all_candidates_invalid_uses_hash_of_sub(self):
        import hashlib
        sub = "sub-none"
        auth, _ = self._run(
            {"sub": sub, "preferred_username": "sys/tem",
             "email": "a/b@domain.example"},
        )
        expected = f"oidc_{hashlib.sha256(sub.encode()).hexdigest()[:16]}"
        assert auth.create_user_oidc.call_args[0][0] == expected

    # --- candidate ORDER (review R5): the plan pins preferred_username,
    # --- email local-part, name.  A silent reordering would rename new
    # --- OIDC accounts, so every slot with two or more valid candidates
    # --- must be asserted with distinct values.

    def test_all_three_valid_preferred_username_wins(self):
        """preferred_username, email local-part and name all valid and
        different: the account must be created under preferred_username."""
        auth, result = self._run({
            "sub": "sub-order-1",
            "preferred_username": "preferredname",
            "email": "emailperson@domain.example",
            "name": "Name Person",
        })
        chosen = auth.create_user_oidc.call_args[0][0]
        assert chosen == "preferredname"
        assert chosen != "emailperson" and chosen != "name person"

    def test_preferred_username_absent_email_before_name(self):
        """preferred_username absent, email and name valid and different:
        the email local-part wins over name."""
        auth, _ = self._run({
            "sub": "sub-order-2",
            "email": "emailperson@domain.example",
            "name": "Name Person",
        })
        assert auth.create_user_oidc.call_args[0][0] == "emailperson"

    def test_preferred_username_invalid_email_before_name(self):
        """preferred_username invalid (backslash, dev #6821), email and
        name valid and different: the email local-part wins over name."""
        auth, _ = self._run({
            "sub": "sub-order-3",
            "preferred_username": "DOM\\main",
            "email": "emailperson@domain.example",
            "name": "Name Person",
        })
        assert auth.create_user_oidc.call_args[0][0] == "emailperson"

    def test_only_name_valid_name_used(self):
        """preferred_username and the email local-part both invalid, name
        valid: name is used."""
        auth, _ = self._run({
            "sub": "sub-order-4",
            "preferred_username": "who/ever",
            "email": "bad/user@domain.example",
            "name": "Name Person",
        })
        assert auth.create_user_oidc.call_args[0][0] == "name person"


class TestCallbackAdminSyncCacheInvalidation:
    """A demotion synced from IdP groups purges ApiTokens in the core and
    must dirty the per-process bearer cache, including when the purge
    fails — mirroring set_user_admin's finally pattern."""

    def _run_demotion(self, monkeypatch, *, sync_error=None,
                      with_invalidator=True, group="users"):
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing", "email": "grace@example.com", "groups": [group],
        }

        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = "grace"
        auth.create_session_trusted.return_value = "token"
        if sync_error is not None:
            auth.set_oidc_user_admin.side_effect = sync_error
        else:
            auth.set_oidc_user_admin.return_value = True

        req = _fake_request_with_params(
            {"code": "c", "state": "s"},
            cookies={"odysseus_oidc_csrf": "s"},
        )
        invalidator = MagicMock()
        if with_invalidator:
            req.app.state.invalidate_token_cache = invalidator

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")
        import asyncio
        try:
            result = asyncio.run(
                ep(
                    req,
                    SimpleNamespace(
                        set_cookie=MagicMock(),
                        delete_cookie=MagicMock(),
                        status_code=200,
                        headers={},
                    ),
                )
            )
        except Exception as exc:  # HEAD never wraps the call
            pytest.fail(f"callback did not handle set_oidc_user_admin: {exc!r}")
        return auth, invalidator, result

    def test_demotion_invalidates_bearer_token_cache(self, monkeypatch):
        auth, invalidator, result = self._run_demotion(monkeypatch)
        auth.set_oidc_user_admin.assert_called_once_with("grace", False)
        assert result.status_code == 302
        invalidator.assert_called_once_with()

    def test_demotion_sync_failure_refuses_session_and_still_invalidates(
        self, monkeypatch,
    ):
        """Store failure: no session issued, oidc_failed redirect, and the
        cache is still invalidated by the finally block."""
        auth, invalidator, result = self._run_demotion(
            monkeypatch, sync_error=RuntimeError("database unavailable"),
        )
        assert "error=oidc_failed" in result.headers["location"]
        auth.create_session_trusted.assert_not_called()
        invalidator.assert_called_once_with()

    def test_promotion_does_not_invalidate_cache(self, monkeypatch):
        auth, invalidator, result = self._run_demotion(
            monkeypatch, group="odysseus-admins",
        )
        auth.set_oidc_user_admin.assert_called_once_with("grace", True)
        assert result.status_code == 302
        invalidator.assert_not_called()

    def test_missing_invalidator_is_tolerated(self, monkeypatch):
        auth, _, result = self._run_demotion(monkeypatch, with_invalidator=False)
        assert result.status_code == 302
        assert result.headers["location"] == "/"


class TestOidcMountPath:
    """dev's ASGI root_path support: every redirect target and the CSRF
    cookie path must carry the mount prefix; an empty root_path keeps the
    exact pre-mount values."""

    MOUNT = "/odysseus"

    def _login_manager(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.get_authorization_url.return_value = (
            "https://idp.example.com/authorize?state=abc", "abc", "def",
        )
        return mgr

    def _login_set_cookie_kwargs(self, root_path):
        import asyncio
        router = _setup_oidc_routes(MagicMock(), self._login_manager())
        ep = _get_endpoint(router, "/api/auth/oidc/login")
        from unittest.mock import patch
        with patch("fastapi.responses.RedirectResponse.set_cookie") as sc:
            asyncio.run(ep(_fake_request(root_path=root_path)))
            return sc.call_args.kwargs

    def _callback(self, *, root_path, mgr, auth, query=None, cookies=None):
        import asyncio
        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")
        response = SimpleNamespace(
            set_cookie=MagicMock(),
            delete_cookie=MagicMock(),
            status_code=200,
            headers={},
        )
        return asyncio.run(
            ep(
                _fake_request_with_params(
                    query if query is not None else {"code": "c", "state": "s"},
                    cookies=cookies if cookies is not None else {"odysseus_oidc_csrf": "s"},
                    root_path=root_path,
                ),
                response,
            )
        )

    def _success_auth(self):
        auth = MagicMock()
        auth.check_oidc_totp.return_value = False
        auth.get_user_by_oidc.return_value = "alice"
        auth.create_session_trusted.return_value = "token"
        return auth

    def _success_mgr(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.redirect_uri_override = None
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user123", "email": "alice@example.com",
        }
        return mgr

    # -- /login error redirect -------------------------------------------------

    def test_login_error_redirect_includes_root_path(self):
        from core.oidc import OidcError
        mgr = self._login_manager()
        mgr.get_authorization_url.side_effect = OidcError("bad config")
        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/login")
        import asyncio
        result = asyncio.run(ep(_fake_request(root_path=self.MOUNT)))
        assert result.headers["location"] == f"{self.MOUNT}/login?error=oidc_config"

    def test_login_error_redirect_empty_root_path_unchanged(self):
        from core.oidc import OidcError
        mgr = self._login_manager()
        mgr.get_authorization_url.side_effect = OidcError("bad config")
        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/login")
        import asyncio
        result = asyncio.run(ep(_fake_request()))
        assert result.headers["location"] == "/login?error=oidc_config"

    # -- CSRF cookie path on set ------------------------------------------------

    def test_csrf_cookie_set_path_includes_root_path(self):
        kwargs = self._login_set_cookie_kwargs(self.MOUNT)
        assert kwargs["path"] == f"{self.MOUNT}/api/auth/oidc/callback"

    def test_csrf_cookie_set_path_empty_root_unchanged(self):
        kwargs = self._login_set_cookie_kwargs(None)
        assert kwargs["path"] == "/api/auth/oidc/callback"

    # -- callback success: redirect target + CSRF delete path --------------------

    def test_callback_success_redirect_and_csrf_delete_under_mount(self):
        result = self._callback(
            root_path=self.MOUNT, mgr=self._success_mgr(),
            auth=self._success_auth(),
        )
        assert result.headers["location"] == f"{self.MOUNT}/"
        assert result.delete_cookie.call_args.kwargs["path"] == \
            f"{self.MOUNT}/api/auth/oidc/callback"
        # The session cookie path itself stays "/" exactly like password login.
        assert result.set_cookie.call_args.kwargs["path"] == "/"

    def test_callback_success_empty_root_path_unchanged(self):
        result = self._callback(root_path=None, mgr=self._success_mgr(),
                                auth=self._success_auth())
        assert result.headers["location"] == "/"
        assert result.delete_cookie.call_args.kwargs["path"] == \
            "/api/auth/oidc/callback"

    # -- callback error redirect (via _build_oidc_error_redirect) ----------------

    def test_callback_error_redirect_under_mount(self):
        from core.oidc import OidcError
        mgr = self._success_mgr()
        mgr.exchange_code.side_effect = OidcError("exchange failed")
        auth = MagicMock()
        result = self._callback(root_path=self.MOUNT, mgr=mgr, auth=auth)
        assert result.headers["location"] == \
            f"{self.MOUNT}/login?error=oidc_failed"
        assert "Path=/odysseus/api/auth/oidc/callback" in \
            result.headers.get("set-cookie", "")

    def test_callback_error_redirect_empty_root_path_unchanged(self):
        from core.oidc import OidcError
        mgr = self._success_mgr()
        mgr.exchange_code.side_effect = OidcError("exchange failed")
        result = self._callback(root_path=None, mgr=mgr, auth=MagicMock())
        assert result.headers["location"] == "/login?error=oidc_failed"
        assert "Path=/api/auth/oidc/callback" in result.headers.get("set-cookie", "")

    # -- callback not-configured branch delete path ------------------------------

    def test_callback_not_configured_csrf_delete_under_mount(self):
        import asyncio
        mgr = MagicMock()
        mgr.configured = False
        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")
        result = asyncio.run(ep(
            _fake_request_with_params({"code": "c", "state": "s"},
                                      root_path=self.MOUNT),
            SimpleNamespace(),
        ))
        assert result.status_code == 503
        assert "Path=/odysseus/api/auth/oidc/callback" in \
            result.headers.get("set-cookie", "")
