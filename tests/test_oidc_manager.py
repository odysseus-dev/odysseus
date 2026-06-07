"""Tests for OidcManager — discovery, auth URL, code exchange, id_token verification."""

import json
import time
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers — fake OIDC provider
# ---------------------------------------------------------------------------

FAKE_ISSUER = "https://idp.example.com"
FAKE_CLIENT_ID = "test-client"
FAKE_CLIENT_SECRET = "test-secret"

DISCOVERY_DOC = {
    "issuer": FAKE_ISSUER,
    "authorization_endpoint": f"{FAKE_ISSUER}/authorize",
    "token_endpoint": f"{FAKE_ISSUER}/token",
    "jwks_uri": f"{FAKE_ISSUER}/jwks",
    "userinfo_endpoint": f"{FAKE_ISSUER}/userinfo",
    "response_types_supported": ["code"],
    "subject_types_supported": ["public"],
    "id_token_signing_alg_values_supported": ["RS256"],
}


# Module-level cache so _make_id_token and the tests share the same key
_test_jwks_cache = None
_test_jwk_key_cache = None


def _make_test_jwks_and_key():
    """Generate an RSA key pair and return (jwks_dict, private_jwk).

    The key pair is cached at module level so id_token signing and JWKS
    verification use the same key — calling this multiple times returns
    the same pair.
    """
    global _test_jwks_cache, _test_jwk_key_cache
    if _test_jwks_cache is not None:
        return _test_jwks_cache, _test_jwk_key_cache

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from authlib.jose import JsonWebKey

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build JWKS (public key)
    public_jwk = JsonWebKey.import_key(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
        {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key-1"},
    )
    jwk_dict = json.loads(public_jwk.as_json())
    jwks = {"keys": [jwk_dict]}

    # Private key JWK for signing
    private_jwk = JsonWebKey.import_key(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key-1"},
    )

    _test_jwks_cache = jwks
    _test_jwk_key_cache = private_jwk
    return jwks, private_jwk


def _make_id_token(sub, nonce, issuer=FAKE_ISSUER, aud=FAKE_CLIENT_ID, exp=None):
    """Sign a test id_token with the test RSA key."""
    from authlib.jose import jwt

    _, jwk = _make_test_jwks_and_key()

    if exp is None:
        exp = int(time.time()) + 3600

    header = {"alg": "RS256", "kid": "test-key-1"}
    payload = {
        "iss": issuer,
        "sub": sub,
        "aud": aud,
        "exp": exp,
        "iat": int(time.time()),
        "nonce": nonce,
        "email": f"{sub}@example.com",
        "name": sub.title(),
        "preferred_username": sub,
    }
    return jwt.encode(header, payload, jwk).decode()


# ---------------------------------------------------------------------------
# Mock httpx responses
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError
            raise HTTPStatusError("error", request=MagicMock(), response=self)

    def json(self):
        return self._json


def _mock_discovery_response():
    return _FakeResponse(200, DISCOVERY_DOC)


def _mock_token_response(id_token):
    return _FakeResponse(200, {
        "access_token": "fake-access-token",
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": 3600,
    })


def _mock_jwks_response(jwks):
    return _FakeResponse(200, jwks)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOidcManagerInit:
    def test_discovery_success(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),  # JWKS fetch happens in exchange_code, not init
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )
            mgr._config = DISCOVERY_DOC  # ensure config is set for subsequent tests
            assert mgr.configured
            assert mgr.issuer == FAKE_ISSUER

    def test_discovery_failure_raises(self):
        import core.oidc as mod

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.return_value = _FakeResponse(500, {"error": "down"}, "server error")
            with pytest.raises(mod.OidcError, match="Failed to fetch OIDC discovery"):
                mod.OidcManager(
                    issuer=FAKE_ISSUER,
                    client_id=FAKE_CLIENT_ID,
                    client_secret=FAKE_CLIENT_SECRET,
                )

    def test_discovery_missing_endpoint_raises(self):
        import core.oidc as mod

        bad_doc = dict(DISCOVERY_DOC)
        del bad_doc["authorization_endpoint"]

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.return_value = _FakeResponse(200, bad_doc)
            with pytest.raises(mod.OidcError, match="authorization_endpoint"):
                mod.OidcManager(
                    issuer=FAKE_ISSUER,
                    client_id=FAKE_CLIENT_ID,
                    client_secret=FAKE_CLIENT_SECRET,
                )

    def test_provider_name_from_hostname(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )
            assert mgr.provider_name == "idp.example.com"


class TestAuthorizationUrl:
    def test_returns_url_and_state(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

        url, state, nonce = mgr.get_authorization_url("https://app.example.com/callback")
        assert url.startswith(f"{FAKE_ISSUER}/authorize?")
        assert "response_type=code" in url
        assert f"client_id={FAKE_CLIENT_ID}" in url
        assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fcallback" in url
        assert f"state={state}" in url
        assert f"nonce={nonce}" in url
        assert len(state) == 64  # 32 hex bytes
        assert len(nonce) == 64


class TestExchangeCode:
    def test_successful_exchange(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "a" * 64
        id_token = _make_id_token("user123", nonce)

        import core.oidc as mod

        # Clear state store
        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            # Discovery
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),   # JWKS for id_token verification
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            # Generate state first
            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")

            # We need to override the nonce in the stored state to match our id_token
            # Replace the state entry with our controlled nonce
            mod._state_store[state] = {
                "nonce": nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            # Token exchange
            mock_post.return_value = _mock_token_response(id_token)

            # Also need to handle any additional get calls
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),   # JWKS fetch in _verify_id_token
            ]

            claims = mgr.exchange_code("auth_code_xyz", state, "https://app.example.com/callback")

            assert claims["sub"] == "user123"
            assert claims["email"] == "user123@example.com"
            assert claims["nonce"] == nonce

    def test_state_not_found(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

        with pytest.raises(mod.OidcError, match="state not found"):
            mgr.exchange_code("code", "nonexistent_state", "https://app.example.com/callback")

    def test_no_id_token_in_response(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        nonce = "b" * 64
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")
            mod._state_store[state] = {
                "nonce": nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            # Token response without id_token
            mock_post.return_value = _FakeResponse(200, {"access_token": "fake"})

            with pytest.raises(mod.OidcError, match="No id_token"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_token_endpoint_error(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        nonce = "c" * 64
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")
            mod._state_store[state] = {
                "nonce": nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            # Token endpoint returns error
            mock_post.return_value = _FakeResponse(400, {"error": "invalid_grant"})

            with pytest.raises(mod.OidcError):
                mgr.exchange_code("bad_code", state, "https://app.example.com/callback")

    def test_id_token_wrong_issuer(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "d" * 64
        id_token = _make_id_token("user123", nonce, issuer="https://evil.example.com")
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")
            mod._state_store[state] = {
                "nonce": nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="iss"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_wrong_audience(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "e" * 64
        id_token = _make_id_token("user123", nonce, aud="wrong-client")
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")
            mod._state_store[state] = {
                "nonce": nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_expired(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "f" * 64
        id_token = _make_id_token("user123", nonce, exp=int(time.time()) - 60)
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")
            mod._state_store[state] = {
                "nonce": nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="exp"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_nonce_mismatch(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce_in_token = "g" * 64
        different_nonce = "h" * 64
        id_token = _make_id_token("user123", nonce_in_token)
        import core.oidc as mod

        mod._state_store.clear()

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")
            # Store a different nonce than what's in the token
            mod._state_store[state] = {
                "nonce": different_nonce,
                "redirect_uri": "https://app.example.com/callback",
                "created": time.time(),
            }

            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="nonce"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")
