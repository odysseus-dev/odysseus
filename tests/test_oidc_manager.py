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
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )
            mgr._config = DISCOVERY_DOC
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
        # State is Fernet (base64) and gets URL-encoded; check via parse
        from urllib.parse import urlparse, parse_qs
        parsed = parse_qs(urlparse(url).query)
        assert parsed.get("state") == [state]
        assert f"nonce={nonce}" in url
        # State is now a Fernet-encrypted token (base64, variable length)
        assert len(state) > 60  # Fernet tokens are always >60 chars
        assert len(nonce) == 64  # nonce is still 32 hex bytes

    def test_state_roundtrip(self):
        """Verify state token can be decoded back to the original data."""
        import core.oidc as mod

        jwt_jwks, _ = _make_test_jwks_and_key()
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
        decoded = mod._decode_state(state)
        assert decoded is not None
        assert decoded["nonce"] == nonce
        assert decoded["redirect_uri"] == "https://app.example.com/callback"


class TestExchangeCode:
    def test_successful_exchange(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "a" * 64  # 32 hex bytes
        id_token = _make_id_token("user123", nonce)

        import core.oidc as mod

        # Clear JWKS cache to force clean fetch
        mod.OidcManager._jwks_cache = {}

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            # Discovery
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            # Generate auth URL — this creates an encrypted state with
            # the same nonce we'll use in our id_token
            url, state, gen_nonce = mgr.get_authorization_url("https://app.example.com/callback")

            # Override: build our own state with the nonce that matches the id_token
            state = mod._encode_state(nonce, "https://app.example.com/callback")

            # Token exchange — mock first the JWKS fetch, then the token POST
            mock_post.return_value = _mock_token_response(id_token)
            # Mock the JWKS fetch that happens inside _verify_id_token
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            claims = mgr.exchange_code("auth_code_xyz", state, "https://app.example.com/callback")

            assert claims["sub"] == "user123"
            assert claims["email"] == "user123@example.com"
            assert claims["nonce"] == nonce

    def test_state_invalid(self):
        """An invalid/expired state token should raise OidcError."""
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

        with pytest.raises(mod.OidcError, match="state not found"):
            mgr.exchange_code("code", "not-a-valid-fernet-token", "https://app.example.com/callback")

    def test_state_expired(self):
        """An expired state token should raise OidcError."""
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

        # Build an already-expired state token
        token = mod._encode_state("nonce", "https://app.example.com/callback")
        # Decode to verify it's valid, then re-encode with old timestamp
        fernet = mod._get_state_fernet()
        expired_data = json.dumps({
            "nonce": "nonce",
            "redirect_uri": "https://app.example.com/callback",
            "created": time.time() - 1200,  # 20 minutes ago
        })
        expired_state = fernet.encrypt(expired_data.encode()).decode()

        with pytest.raises(mod.OidcError, match="state not found"):
            mgr.exchange_code("code", expired_state, "https://app.example.com/callback")

    def test_no_id_token_in_response(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

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

            state = mod._encode_state("nonce", "https://app.example.com/callback")

            # Token response without id_token
            mock_post.return_value = _FakeResponse(200, {"access_token": "fake"})

            with pytest.raises(mod.OidcError, match="No id_token"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_token_endpoint_error(self):
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

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

            state = mod._encode_state("nonce", "https://app.example.com/callback")
            mock_post.return_value = _FakeResponse(400, {"error": "invalid_grant"})

            with pytest.raises(mod.OidcError):
                mgr.exchange_code("bad_code", state, "https://app.example.com/callback")

    def test_id_token_wrong_issuer(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "d" * 64
        id_token = _make_id_token("user123", nonce, issuer="https://evil.example.com")
        import core.oidc as mod

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

            state = mod._encode_state(nonce, "https://app.example.com/callback")
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

            state = mod._encode_state(nonce, "https://app.example.com/callback")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_aud_array_valid(self):
        """aud as a JSON array containing the client_id should pass."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "f" * 64
        id_token = _make_id_token("user123", nonce, aud=[FAKE_CLIENT_ID, "other-client"])
        import core.oidc as mod

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

            state = mod._encode_state(nonce, "https://app.example.com/callback")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            claims = mgr.exchange_code("code", state, "https://app.example.com/callback")
            assert claims["sub"] == "user123"

    def test_id_token_aud_array_missing_client_id(self):
        """aud as a JSON array WITHOUT the client_id should fail."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "g" * 64
        id_token = _make_id_token("user123", nonce, aud=["some-other-client", "another-one"])
        import core.oidc as mod

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

            state = mod._encode_state(nonce, "https://app.example.com/callback")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="aud"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_expired(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "h" * 64
        id_token = _make_id_token("user123", nonce, exp=int(time.time()) - 60)
        import core.oidc as mod

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

            state = mod._encode_state(nonce, "https://app.example.com/callback")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="exp"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_nonce_mismatch(self):
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce_in_token = "i" * 64
        different_nonce = "j" * 64
        id_token = _make_id_token("user123", nonce_in_token)
        import core.oidc as mod

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

            # State carries a different nonce than the id_token
            state = mod._encode_state(different_nonce, "https://app.example.com/callback")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="nonce"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")


class TestJwksCache:
    def test_jwks_cached_on_first_fetch(self):
        """JWKS should be fetched once then served from cache."""
        jwt_jwks, _ = _make_test_jwks_and_key()
        import core.oidc as mod

        # Reset cache
        mod.OidcManager._jwks_cache = {}

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

            # First fetch: hits the network
            jwks1 = mgr._fetch_jwks()
            assert mock_get.call_count == 2  # discovery + first JWKS fetch

            # Second fetch: cached (no additional HTTP call)
            jwks2 = mgr._fetch_jwks()
            assert mock_get.call_count == 2  # still 2
            assert jwks1 == jwks2

    def test_jwks_refresh_on_unknown_kid(self):
        """Verification with an unknown kid should refresh the JWKS cache."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "k" * 64
        id_token = _make_id_token("user123", nonce)
        import core.oidc as mod

        # Reset cache
        mod.OidcManager._jwks_cache = {}

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),  # first JWKS fetch
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            state = mod._encode_state(nonce, "https://app.example.com/callback")

            # First exchange — cache is populated
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),  # called by _verify_id_token
            ]
            claims = mgr.exchange_code("code1", state, "https://app.example.com/callback")
            assert claims["sub"] == "user123"

            # Second exchange with same kid — cached, no extra JWKS fetch.
            # But userinfo still tries to call GET on the userinfo endpoint
            # (which fails gracefully — logged as a warning, not a crash).
            state2 = mod._encode_state(nonce, "https://app.example.com/callback")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            # Provide a userinfo mock so it doesn't count as a real failure
            mock_get.side_effect = [
                _FakeResponse(200, {"sub": "user123"}),  # userinfo
            ]
            claims2 = mgr.exchange_code("code2", state2, "https://app.example.com/callback")
            assert claims2["sub"] == "user123"
            # One GET call for userinfo (not JWKS — that's cached)
            assert mock_get.call_count == 1
