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
    from routes.oidc_routes import setup_oidc_routes
    return setup_oidc_routes(auth_manager, oidc_manager)


def _get_endpoint(router: APIRouter, path: str):
    """Find the route endpoint for a given path."""
    for route in router.routes:
        if getattr(route, "path", "") == path:
            return route.endpoint
    raise AssertionError(f"No route found for path: {path}")


def _fake_request(base_url="http://testserver"):
    """Build a minimal Request-like object."""
    req = SimpleNamespace()
    req.base_url = SimpleNamespace()
    req.base_url.__str__ = lambda s, b=base_url: b
    req.base_url.rstrip = lambda s, strip="/": base_url.rstrip(strip)
    req.query_params = {}
    req.cookies = {}
    return req


def _fake_request_with_params(query_params, base_url="http://testserver"):
    req = _fake_request(base_url)
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
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user123",
            "email": "alice@example.com",
            "preferred_username": "alice",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None  # new user
        auth.create_user_oidc.return_value = "alice"
        auth.create_session_trusted.return_value = "session-token-abc"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params({"code": "authcode", "state": "state123"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # Check session cookie was set
        assert result.status_code == 302
        assert result.headers["location"] == "/"

        # Check user creation was called correctly
        auth.create_user_oidc.assert_called_once_with(
            "alice", "user123", "https://idp.example.com", email="alice@example.com",
            is_admin=False,
        )

    def test_callback_existing_user(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing_sub",
            "email": "bob@example.com",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "bob"  # existing user
        # create_user_oidc should NOT be called for existing users
        auth.create_session_trusted.return_value = "session-token-xyz"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params({"code": "authcode", "state": "state456"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        assert result.status_code == 302
        assert result.headers["location"] == "/"
        auth.create_user_oidc.assert_not_called()
        auth.create_session_trusted.assert_called_once_with("bob")

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

    def test_callback_exchange_failure_redirects(self):
        from core.oidc import OidcError
        mgr = MagicMock()
        mgr.configured = True
        mgr.exchange_code.side_effect = OidcError("token exchange failed")

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params({"code": "badcode", "state": "state999"}),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_failed" in result.headers["location"]

    def test_callback_missing_sub_in_claims(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.exchange_code.return_value = {"sub": ""}  # empty sub

        router = _setup_oidc_routes(MagicMock(), mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_failed" in result.headers["location"]

    def test_callback_create_user_failure(self):
        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "new_user",
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None
        auth.create_user_oidc.return_value = None  # creation failed

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        from fastapi.responses import RedirectResponse
        result = asyncio.run(
            ep(
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(),
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=oidc_failed" in result.headers["location"]

    def test_callback_username_from_email_local_part(self):
        """When no preferred_username, the email local-part is used."""
        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user456",
            "email": "charlie@example.com",
            # no preferred_username
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
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        auth.create_user_oidc.assert_called_once()
        call_args = auth.create_user_oidc.call_args
        assert call_args[0][0] == "charlie"  # first positional arg is username

    def test_callback_new_user_admin_from_groups(self, monkeypatch):
        """New user whose groups claim includes an admin group is created as admin."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins,superusers")

        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user1",
            "email": "dave@example.com",
            "preferred_username": "dave",
            "groups": ["users", "odysseus-admins"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = None  # new user
        auth.create_user_oidc.return_value = "dave"
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        result = asyncio.run(
            ep(
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # Should be created with is_admin=True
        auth.create_user_oidc.assert_called_once()
        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is True
        assert result.status_code == 302

    def test_callback_new_user_no_admin_groups(self, monkeypatch):
        """New user without admin groups is created as non-admin."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user2",
            "email": "eve@example.com",
            "preferred_username": "eve",
            "groups": ["users"],  # no admin group
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
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is False

    def test_callback_existing_user_promoted_to_admin(self, monkeypatch):
        """Existing OIDC user gets admin synced from groups on login."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing",
            "email": "frank@example.com",
            "groups": ["odysseus-admins"],
        }

        auth = MagicMock()
        auth.get_user_by_oidc.return_value = "frank"  # existing user
        auth.set_oidc_user_admin.return_value = True
        auth.create_session_trusted.return_value = "token"

        router = _setup_oidc_routes(auth, mgr)
        ep = _get_endpoint(router, "/api/auth/oidc/callback")

        import asyncio
        asyncio.run(
            ep(
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        # Existing user should have admin synced, not re-created
        auth.set_oidc_user_admin.assert_called_once_with("frank", True)
        auth.create_user_oidc.assert_not_called()

    def test_callback_existing_user_demoted_from_admin(self, monkeypatch):
        """Existing OIDC user loses admin when removed from admin group."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "existing",
            "email": "grace@example.com",
            "groups": ["users"],  # no longer in admin group
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
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        auth.set_oidc_user_admin.assert_called_once_with("grace", False)

    def test_callback_no_groups_claim(self, monkeypatch):
        """Provider doesn't return a groups claim — default to non-admin."""
        monkeypatch.setenv("OIDC_ADMIN_GROUPS", "odysseus-admins")

        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user3",
            "email": "hank@example.com",
            # no groups key
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
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is False

    def test_callback_no_admin_groups_configured(self, monkeypatch):
        """OIDC_ADMIN_GROUPS not set — default to non-admin."""
        monkeypatch.delenv("OIDC_ADMIN_GROUPS", raising=False)

        mgr = MagicMock()
        mgr.configured = True
        mgr.issuer = "https://idp.example.com"
        mgr.exchange_code.return_value = {
            "sub": "user4",
            "email": "iris@example.com",
            "groups": ["odysseus-admins"],  # has the group, but not configured
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
                _fake_request_with_params({"code": "code", "state": "state"}),
                SimpleNamespace(
                    set_cookie=MagicMock(),
                    status_code=200,
                    headers={},
                ),
            )
        )

        call_kwargs = auth.create_user_oidc.call_args.kwargs
        assert call_kwargs.get("is_admin") is False
