"""Tests for OidcManager — discovery, auth URL, code exchange, id_token verification."""

import json
import time
import pytest
from unittest.mock import patch, MagicMock

from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_state_fernet(request, monkeypatch):
    """Ensure every test gets a fresh in-memory Fernet key for OIDC state
    encryption so tests don't depend on the host filesystem (data/.app_key).
    Skipped for TestStateKeyPersistence which tests the real key creation."""
    if request.cls and request.cls.__name__ == "TestStateKeyPersistence":
        return
    import core.oidc as mod
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(mod, "_state_fernet", None)
    monkeypatch.setattr(mod, "_get_state_fernet", lambda: fernet)


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


def _make_id_token(sub, nonce, issuer=FAKE_ISSUER, aud=FAKE_CLIENT_ID, exp=None, azp=None):
    """Sign a test id_token with the test RSA key.

    When *azp* is provided it is included in the payload."""
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
    if azp is not None:
        payload["azp"] = azp
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

    def test_discovery_issuer_mismatch_fails_closed(self):
        """Discovery doc with a different issuer MUST abort (OIDC Discovery §1.1)."""
        import core.oidc as mod

        bad_doc = dict(DISCOVERY_DOC)
        bad_doc["issuer"] = "https://evil-idp.example.com"

        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.return_value = _FakeResponse(200, bad_doc)
            with pytest.raises(mod.OidcError, match="issuer mismatch"):
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
        # PKCE (RFC 7636) — S256 challenge must always be sent
        assert parsed.get("code_challenge_method") == ["S256"]
        challenge = parsed.get("code_challenge", [""])[0]
        assert len(challenge) == 43  # unpadded base64url SHA-256
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
        # The PKCE verifier rides in the encrypted state and must S256-hash
        # to the code_challenge sent in the authorization URL.
        import base64
        import hashlib
        from urllib.parse import urlparse, parse_qs
        challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
        verifier = decoded["code_verifier"]
        assert 43 <= len(verifier) <= 128  # RFC 7636 §4.1 bounds
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected

    def test_state_without_code_verifier_rejected(self):
        """Legacy/forged state payloads lacking a PKCE verifier are invalid."""
        import core.oidc as mod

        fernet = mod._get_state_fernet()
        payload = json.dumps({
            "nonce": "n" * 64,
            "redirect_uri": "https://app.example.com/callback",
            "created": time.time(),
        })
        state = fernet.encrypt(payload.encode()).decode()
        assert mod._decode_state(state) is None


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
            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")

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
        token = mod._encode_state("nonce", "https://app.example.com/callback", "test-code-verifier")
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

            state = mod._encode_state("nonce", "https://app.example.com/callback", "test-code-verifier")

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

            state = mod._encode_state("nonce", "https://app.example.com/callback", "test-code-verifier")
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
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
        id_token = _make_id_token("user123", nonce, aud=[FAKE_CLIENT_ID, "other-client"], azp=FAKE_CLIENT_ID)
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            claims = mgr.exchange_code("code", state, "https://app.example.com/callback")
            assert claims["sub"] == "user123"

    def test_id_token_aud_array_without_azp_rejected(self):
        """Multi-audience token without azp MUST be rejected (OIDC Core §2)."""
        from authlib.jose import jwt
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "g2" * 32
        # Manually build a multi-audience token without azp
        header = {"alg": "RS256", "kid": "test-key-1"}
        payload = {
            "iss": FAKE_ISSUER,
            "sub": "user123",
            "aud": [FAKE_CLIENT_ID, "other-client"],
            # deliberately omit azp
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
            "nonce": nonce,
        }
        id_token = jwt.encode(header, payload, jwk).decode()
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="no azp"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="aud"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_id_token_single_aud_array_no_azp(self):
        """A single-element aud array (e.g. [client_id]) without azp MUST
        be accepted.  OIDC Core §2 only requires azp for multi-audience
        tokens; a one-element array is a valid representation of the
        audience and must not be rejected."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "s1" * 32
        id_token = _make_id_token("user123", nonce, aud=[FAKE_CLIENT_ID])
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            claims = mgr.exchange_code("code", state, "https://app.example.com/callback")
            assert claims["sub"] == "user123"

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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
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
            state = mod._encode_state(different_nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            with pytest.raises(mod.OidcError, match="nonce"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")


class TestUserInfoProtection:
    def test_userinfo_mismatched_sub_rejected(self):
        """UserInfo with a different sub than the id_token should be rejected."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "m" * 64
        id_token = _make_id_token("user123", nonce)
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)

            # UserInfo returns a different sub
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),     # JWKS
                _FakeResponse(200, {"sub": "evil_user"}),  # mismatched UserInfo
            ]

            with pytest.raises(mod.OidcError, match="UserInfo sub mismatch"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_userinfo_same_sub_merged_safely(self):
        """UserInfo with matching sub should be merged without overwriting identity claims."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "n" * 64
        id_token = _make_id_token("user123", nonce)
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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)

            # UserInfo matches sub, adds extra profile data
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
                _FakeResponse(200, {
                    "sub": "user123",  # matches id_token
                    "name": "Full Name from UserInfo",
                    "picture": "https://example.com/avatar.png",
                }),
            ]

            claims = mgr.exchange_code("code", state, "https://app.example.com/callback")
            assert claims["sub"] == "user123"  # unchanged
            assert claims["name"] == "Full Name from UserInfo"  # merged
            assert claims["picture"] == "https://example.com/avatar.png"  # merged
            # Verified claims not overwritten
            assert claims["nonce"] == nonce
            assert claims["iss"] == FAKE_ISSUER


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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")

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
            state2 = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
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

    def test_jwks_refresh_network_error_wraps_as_oidc_error(self):
        """Transient JWKS network/parse failures must produce OidcError, not raw exceptions."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "p" * 64
        id_token = _make_id_token("user123", nonce)
        import core.oidc as mod

        mod.OidcManager._jwks_cache = {}

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            # Discovery succeeds
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)

            # Simulate a network failure on the JWKS fetch inside _verify_id_token
            # (triggered by an unknown kid).
            mock_get.reset_mock()
            mock_get.side_effect = [
                ConnectionError("Temporary network failure"),  # JWKS fails
            ]

            with pytest.raises(mod.OidcError, match="JWKS fetch"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_jwks_refresh_bad_json_wraps_as_oidc_error(self):
        """JWKS response that isn't valid JSON should produce OidcError."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "q" * 64
        id_token = _make_id_token("user123", nonce)
        import core.oidc as mod

        mod.OidcManager._jwks_cache = {}

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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)

            # Simulate a bad JSON response from the JWKS endpoint
            mock_get.reset_mock()
            mock_get.side_effect = [
                _FakeResponse(200, None, "not json at all"),
            ]

            with pytest.raises(mod.OidcError, match="JWKS fetch"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")

    def test_jwks_refresh_http_error_wraps_as_oidc_error(self):
        """JWKS endpoint returning HTTP 500 should produce OidcError."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "r" * 64
        id_token = _make_id_token("user123", nonce)
        import core.oidc as mod

        mod.OidcManager._jwks_cache = {}

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

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)

            # Simulate HTTP 500 from the JWKS endpoint
            mock_get.reset_mock()
            mock_get.side_effect = [
                _FakeResponse(500, {"error": "internal"}, "internal server error"),
            ]

            with pytest.raises(mod.OidcError, match="JWKS fetch"):
                mgr.exchange_code("code", state, "https://app.example.com/callback")


class TestStateKeyPersistence:
    """Regression: OIDC state encryption key must be shared across workers."""

    def test_fresh_install_creates_shared_key(self, tmp_path, monkeypatch):
        """On a fresh data dir (no .app_key), worker A's encrypted state
        must be decryptable by worker B — proving the shared persistent
        key was created on first use."""
        import core.oidc as mod
        from cryptography.fernet import Fernet

        # Point APP_KEY_FILE at a temp location with no existing key
        key_file = tmp_path / ".app_key"
        monkeypatch.setattr(mod, "_state_fernet", None)
        # Patch the secret_storage module's key path so _get_fernet
        # writes to our temp dir.
        import src.secret_storage as ss
        monkeypatch.setattr(ss, "_KEY_PATH", key_file)
        monkeypatch.setattr(ss, "_fernet", None)

        # Sanity: no key file yet
        assert not key_file.exists()

        # Worker A: encode state (this must create the shared key)
        state_a = mod._encode_state("nonce-abc", "https://app.example.com/callback", "test-code-verifier")
        assert key_file.exists(), "Shared app key must be created on first state encode"
        assert key_file.stat().st_size > 0

        # Simulate worker B: reset the in-process cache and decode
        monkeypatch.setattr(mod, "_state_fernet", None)
        monkeypatch.setattr(ss, "_fernet", None)

        decoded_b = mod._decode_state(state_a)
        assert decoded_b is not None, "Worker B must decode worker A's state"
        assert decoded_b["nonce"] == "nonce-abc"
        assert decoded_b["redirect_uri"] == "https://app.example.com/callback"


    def test_atomic_key_creation_prevents_split_brain(self, tmp_path, monkeypatch):
        """Simulate two racing workers on a fresh data dir: both must
        end up with the same Fernet key even if they race on first access.
        The O_EXCL atomic creation guarantees exactly one writer wins."""
        import core.oidc as mod
        import src.secret_storage as ss

        key_file = tmp_path / ".app_key"
        monkeypatch.setattr(ss, "_KEY_PATH", key_file)

        # Sanity: no key yet
        assert not key_file.exists()

        # Simulate worker A: encode state (creates key file atomically)
        monkeypatch.setattr(mod, "_state_fernet", None)
        monkeypatch.setattr(ss, "_fernet", None)
        state_a = mod._encode_state("nonce-a", "https://cb1.example.com/", "test-code-verifier")
        assert key_file.exists()
        key_bytes_a = key_file.read_bytes()

        # Simulate worker B: reset caches, decode worker A's state
        monkeypatch.setattr(mod, "_state_fernet", None)
        monkeypatch.setattr(ss, "_fernet", None)
        decoded_b = mod._decode_state(state_a)
        assert decoded_b is not None
        assert decoded_b["nonce"] == "nonce-a"

        # Worker B encodes its own state — must use the same key
        state_b = mod._encode_state("nonce-b", "https://cb2.example.com/", "test-code-verifier")
        # After B's encode, the file must still contain worker A's key
        assert key_file.read_bytes() == key_bytes_a, \
            "Worker B must not overwrite the key file created by worker A"

        # Reset and verify cross-worker roundtrip still works
        monkeypatch.setattr(mod, "_state_fernet", None)
        monkeypatch.setattr(ss, "_fernet", None)
        decoded_b2 = mod._decode_state(state_b)
        assert decoded_b2 is not None
        assert decoded_b2["nonce"] == "nonce-b"


class TestJwksCooldown:
    """Regression: failed JWKS refreshes must be throttled by the 60-second cooldown."""

    def test_failed_refresh_triggers_cooldown(self):
        """A failed unknown-kid refresh must be throttled so a second
        attempt inside the cooldown window does NOT call _refresh_jwks again."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "z" * 64
        # Kid "test-key-1" from _make_test_jwks_and_key
        id_token_known_kid = _make_id_token("user123", nonce)
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

            # Step 1: do one successful exchange with "test-key-1" so the
            # JWKS cache is populated and _fetch_jwks() returns cached data.
            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token_known_kid)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),  # _fetch_jwks on empty cache
                _FakeResponse(200, {"sub": "user123"}),  # userinfo
            ]
            claims = mgr.exchange_code("code0", state, "https://app.example.com/callback")
            assert claims["sub"] == "user123"

            # Cache is now populated with kid="test-key-1".

            # Step 2: craft an id_token with a DIFFERENT kid ("unknown-kid").
            # This forces _verify_id_token into the unknown-kid branch.
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from authlib.jose import jwt, JsonWebKey

            # Generate a second key pair with kid "test-key-2"
            key2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            jwk2 = JsonWebKey.import_key(
                key2.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ),
                {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-key-2"},
            )

            id_token_unknown_kid = jwt.encode(
                {"alg": "RS256", "kid": "test-key-2"},
                {
                    "iss": FAKE_ISSUER, "sub": "user123", "aud": FAKE_CLIENT_ID,
                    "exp": int(time.time()) + 3600, "iat": int(time.time()),
                    "nonce": nonce, "email": "user123@example.com",
                },
                jwk2,
            ).decode()

            # First unknown-kid attempt: JWKS refresh FAILS → cooldown set
            state1 = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token_unknown_kid)
            mock_get.reset_mock()
            mock_get.side_effect = [
                ConnectionError("IdP is down"),  # _refresh_jwks fails
            ]
            with pytest.raises(mod.OidcError, match="JWKS fetch"):
                mgr.exchange_code("code1", state1, "https://app.example.com/callback")

            # The cooldown timestamp must now be set
            assert getattr(mgr, "_last_jwks_refresh", 0) > time.time() - 2

            # Second unknown-kid attempt: cooldown still active → throttled,
            # _refresh_jwks must NOT be called.  The exchange will fail
            # because the key for "test-key-2" is not in the stale cache.
            state2 = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token_unknown_kid)
            mock_get.reset_mock()
            # If _refresh_jwks were called, it would hit this side_effect.
            mock_get.side_effect = [
                RuntimeError("_refresh_jwks was called — cooldown broken!"),
            ]
            with pytest.raises(Exception):
                mgr.exchange_code("code2", state2, "https://app.example.com/callback")
            # No GET calls → error came from cooldown + stale cache, not a refresh
            assert mock_get.call_count == 0


class TestRedirectUriBinding:
    """The token exchange must bind to the stored redirect_uri from state."""

    def test_mismatched_redirect_uri_rejected(self):
        """Token exchange with a callback-derived redirect_uri that differs
        from the stored state value must be rejected."""
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

        # State encodes "https://original.example.com/callback"
        state = mod._encode_state("nonce", "https://original.example.com/callback", "test-code-verifier")

        # But the callback derives a different redirect_uri
        with pytest.raises(mod.OidcError, match="redirect_uri mismatch"):
            mgr.exchange_code("code", state, "https://evil.example.com/callback")

    def test_stored_redirect_uri_used_for_token_request(self):
        """The token exchange must POST the stored redirect_uri, not the
        callback-derived one."""
        jwt_jwks, jwk = _make_test_jwks_and_key()
        nonce = "s" * 64
        id_token = _make_id_token("user123", nonce)
        stored_uri = "https://original.example.com/callback"
        import core.oidc as mod

        mod.OidcManager._jwks_cache = {}

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

            state = mod._encode_state(nonce, stored_uri, "test-code-verifier")
            mock_post.return_value = _mock_token_response(id_token)
            mock_get.reset_mock()
            mock_get.side_effect = [
                _mock_jwks_response(jwt_jwks),
            ]

            # Pass a different callback-derived URI — should still use stored_uri
            claims = mgr.exchange_code("code", state, stored_uri)
            assert claims["sub"] == "user123"

            # Verify the token endpoint received the stored URI
            call_data = mock_post.call_args.kwargs["data"]
            assert call_data["redirect_uri"] == stored_uri


class TestUserinfoEndpointMissing:
    """When discovery has no userinfo_endpoint, _fetch_userinfo must return
    None (not {}), and exchange_code must NOT set _userinfo_available=True.
    Otherwise the callback treats "no endpoint" as authoritative group
    evidence and silently demotes an existing OIDC admin."""

    def test_no_userinfo_endpoint_marks_unavailable(self):
        """Discovery lacking userinfo_endpoint → _fetch_userinfo returns
        None → _userinfo_available stays False."""
        jwt_jwks, _ = _make_test_jwks_and_key()
        nonce = "n" * 64
        id_token = _make_id_token("user-no-ui", nonce)
        import core.oidc as mod

        # Discovery doc without a userinfo_endpoint
        discovery_no_ui = dict(DISCOVERY_DOC)
        del discovery_no_ui["userinfo_endpoint"]

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _FakeResponse(200, discovery_no_ui),
                _mock_jwks_response(jwt_jwks),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            # _mock_token_response already includes an access_token.
            mock_post.return_value = _mock_token_response(id_token)

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            claims = mgr.exchange_code("code", state, "https://app.example.com/callback")

            assert claims["sub"] == "user-no-ui"
            # _fetch_userinfo must have returned None because there is
            # no userinfo_endpoint in discovery.
            assert claims["_userinfo_available"] is False, (
                "_userinfo_available must be False when discovery has no "
                "userinfo_endpoint — an empty dict return would be ambiguous"
            )

    def test_userinfo_endpoint_present_marks_available(self):
        """Discovery with userinfo_endpoint + successful fetch →
        _userinfo_available must be True (positive control)."""
        jwt_jwks, _ = _make_test_jwks_and_key()
        nonce = "n" * 64
        id_token = _make_id_token("user-with-ui", nonce)
        import core.oidc as mod

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [
                _mock_discovery_response(),
                _mock_jwks_response(jwt_jwks),
                _FakeResponse(200, {"sub": "user-with-ui", "email": "u@example.com"}),
            ]
            mgr = mod.OidcManager(
                issuer=FAKE_ISSUER,
                client_id=FAKE_CLIENT_ID,
                client_secret=FAKE_CLIENT_SECRET,
            )

            mock_post.return_value = _mock_token_response(id_token)

            state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
            claims = mgr.exchange_code("code", state, "https://app.example.com/callback")

            assert claims["sub"] == "user-with-ui"
            assert claims["_userinfo_available"] is True
            assert claims.get("email") == "u@example.com"


class TestAppKeyAtomicCreation:
    """Regression: on a fresh multi-worker deployment, the shared app key
    must be created atomically so no racing reader ever sees an empty or
    partial key file."""

    def test_key_file_never_empty(self, tmp_path, monkeypatch):
        """The final key file must never be observable as empty or partial —
        it either does not exist, or it contains a complete Fernet key."""
        import src.secret_storage as ss
        from pathlib import Path

        tmp_key = tmp_path / ".app_key"
        monkeypatch.setattr(ss, "_KEY_PATH", tmp_key)
        monkeypatch.setattr(ss, "_fernet", None)

        # Sanity: no key yet
        assert not tmp_key.exists()

        # Trigger key creation
        fernet = ss._get_fernet()
        assert fernet is not None
        assert tmp_key.exists()

        # The file must contain a valid Fernet key (44 URL-safe base64 bytes)
        key_bytes = tmp_key.read_bytes()
        assert len(key_bytes) >= 44, (
            f"Key file must contain a complete Fernet key, got {len(key_bytes)} bytes"
        )
        # Must be usable as a Fernet key
        from cryptography.fernet import Fernet
        f = Fernet(key_bytes)
        token = f.encrypt(b"test")
        assert f.decrypt(token) == b"test"

    def test_racing_reader_gets_valid_key(self, tmp_path, monkeypatch):
        """Simulate a race: pause the writer after temp-file write but
        before the atomic link. A concurrent reader must either see no
        key (and create its own, which will hit FileExistsError) or see
        a complete key — never an empty file."""
        import src.secret_storage as ss
        from pathlib import Path
        from cryptography.fernet import Fernet

        tmp_key = tmp_path / ".app_key"
        monkeypatch.setattr(ss, "_KEY_PATH", tmp_key)
        monkeypatch.setattr(ss, "_fernet", None)

        # Intercept os.link so we can pause between temp-file write and link.
        real_link = Path.__class__.link if hasattr(Path, "link") else type(tmp_key).__dict__.get("link")
        # os.link is a module-level function, not a Path method.
        import os as real_os
        original_link = real_os.link
        link_called = []

        def intercept_link(src, dst, *args, **kwargs):
            link_called.append(str(src))
            # Before the link completes, simulate a racing reader.
            # The reader must not see an empty key file at dst.
            if tmp_key.exists():
                content = tmp_key.read_bytes()
                # This would fail if the file were empty/partial; in our
                # implementation the final path is never exposed until
                # os.link completes, so tmp_key.exists() should be False.
                assert False, (
                    f"Key file already visible before atomic link — "
                    f"reader would see {len(content)} bytes"
                )
            return original_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(real_os, "link", intercept_link)

        # Trigger key creation — must succeed despite the interceptor.
        fernet = ss._get_fernet()
        assert fernet is not None
        assert len(link_called) >= 1
        assert tmp_key.exists()

        # The key file must be complete and usable.
        key_bytes = tmp_key.read_bytes()
        f = Fernet(key_bytes)
        token = f.encrypt(b"test")
        assert f.decrypt(token) == b"test"


# ---------------------------------------------------------------------------
# Regression coverage for UserInfo, NumericDate claims, and max_age
# ---------------------------------------------------------------------------


def _new_security_test_manager(max_age=None):
    """Construct a manager with discovery/JWKS mocked for claim tests."""
    import core.oidc as mod

    jwks, _ = _make_test_jwks_and_key()
    with patch.object(mod.httpx, "get") as mock_get:
        mock_get.side_effect = [
            _mock_discovery_response(),
            _mock_jwks_response(jwks),
        ]
        mgr = mod.OidcManager(
            issuer=FAKE_ISSUER,
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
            max_age=max_age,
        )
    # Direct claim tests still exercise authlib signature verification while
    # avoiding another HTTP request for the already-known test key.
    mgr._jwks_cache = {"test-key-1": jwks["keys"][0]}
    mgr._fetch_jwks = MagicMock(return_value=jwks)
    return mgr


def _make_claim_test_token(sub, nonce, *, auth_time="__unset__", iat="current"):
    """Sign a token with selectively controlled auth_time and iat claims."""
    from authlib.jose import jwt

    _, jwk = _make_test_jwks_and_key()
    payload = {
        "iss": FAKE_ISSUER,
        "sub": sub,
        "aud": FAKE_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "nonce": nonce,
    }
    if auth_time != "__unset__":
        payload["auth_time"] = auth_time
    if iat == "current":
        payload["iat"] = int(time.time())
    elif iat is not None:
        payload["iat"] = iat
    return jwt.encode(
        {"alg": "RS256", "kid": "test-key-1"}, payload, jwk,
    ).decode()


def _exchange_with_userinfo(userinfo):
    """Run exchange_code with a verified id-token and controlled UserInfo."""
    import core.oidc as mod

    mgr = _new_security_test_manager()
    nonce = "u" * 64
    state = mod._encode_state(nonce, "https://app.example.com/callback", "test-code-verifier")
    mgr._token_request = MagicMock(return_value={
        "access_token": "access-token",
        "id_token": "unused-in-this-unit-test",
    })
    mgr._verify_id_token = MagicMock(return_value={
        "sub": "user123",
        "email": "alice@example.com",
    })
    mgr._fetch_userinfo = MagicMock(return_value=userinfo)
    return mgr.exchange_code(
        "code", state, "https://app.example.com/callback",
    )


class TestUserInfoClaimBinding:
    def test_userinfo_missing_sub_discarded(self):
        claims = _exchange_with_userinfo({
            "groups": ["odysseus-admins"],
            "email": "alice@example.com",
        })
        assert "groups" not in claims
        assert claims["_userinfo_available"] is False

    def test_userinfo_non_dict_discarded(self):
        claims = _exchange_with_userinfo(["bad", "data"])
        assert claims["_userinfo_available"] is False
        assert claims["email"] == "alice@example.com"

    def test_userinfo_empty_sub_discarded(self):
        claims = _exchange_with_userinfo({
            "sub": "",
            "groups": ["odysseus-admins"],
        })
        assert "groups" not in claims
        assert claims["_userinfo_available"] is False


class TestNumericDateClaimValidation:
    def test_auth_time_expired_rejected(self):
        import core.oidc as mod
        mgr = _new_security_test_manager(max_age=3600)
        token = _make_claim_test_token("user123", "a" * 64, auth_time=100)
        with pytest.raises(mod.OidcError):
            mgr._verify_id_token(token, "a" * 64)

    def test_auth_time_future_rejected(self):
        mgr = _new_security_test_manager(max_age=3600)
        token = _make_claim_test_token(
            "user123", "b" * 64, auth_time=time.time() + 120,
        )
        import core.oidc as mod
        with pytest.raises(mod.OidcError):
            mgr._verify_id_token(token, "b" * 64)

    def test_auth_time_missing_rejected(self):
        mgr = _new_security_test_manager(max_age=3600)
        token = _make_claim_test_token("user123", "c" * 64)
        import core.oidc as mod
        with pytest.raises(mod.OidcError):
            mgr._verify_id_token(token, "c" * 64)

    def test_auth_time_valid_accepted(self):
        mgr = _new_security_test_manager(max_age=3600)
        token = _make_claim_test_token(
            "user123", "d" * 64, auth_time=time.time() - 10,
        )
        mgr._verify_id_token(token, "d" * 64)

    def test_iat_future_rejected(self):
        mgr = _new_security_test_manager()
        token = _make_claim_test_token(
            "user123", "e" * 64, iat=time.time() + 120,
        )
        import core.oidc as mod
        with pytest.raises(mod.OidcError):
            mgr._verify_id_token(token, "e" * 64)

    def test_iat_nonnumeric_rejected(self):
        mgr = _new_security_test_manager()
        token = _make_claim_test_token(
            "user123", "f" * 64, iat="not-a-number",
        )
        import core.oidc as mod
        with pytest.raises(mod.OidcError):
            mgr._verify_id_token(token, "f" * 64)

    def test_iat_missing_rejected(self):
        # OIDC Core §2: iat is REQUIRED — a token without it must not verify.
        import core.oidc as mod
        mgr = _new_security_test_manager()
        token = _make_claim_test_token("user123", "g" * 64, iat=None)
        with pytest.raises(mod.OidcError, match="missing iat"):
            mgr._verify_id_token(token, "g" * 64)

    def test_iat_valid_accepted(self):
        mgr = _new_security_test_manager()
        token = _make_claim_test_token(
            "user123", "h" * 64, iat=time.time() - 10,
        )
        mgr._verify_id_token(token, "h" * 64)


def _make_manager(discovery_doc=None):
    """Build an OidcManager against a mocked discovery endpoint."""
    import core.oidc as mod
    jwt_jwks, _ = _make_test_jwks_and_key()
    with patch.object(mod.httpx, "get") as mock_get:
        mock_get.side_effect = [
            _FakeResponse(200, discovery_doc or DISCOVERY_DOC),
            _mock_jwks_response(jwt_jwks),
        ]
        return mod.OidcManager(
            issuer=FAKE_ISSUER,
            client_id=FAKE_CLIENT_ID,
            client_secret=FAKE_CLIENT_SECRET,
        )


class TestPkceTokenRequest:
    def test_code_verifier_sent_to_token_endpoint(self):
        """The verifier recovered from state must be POSTed to the token
        endpoint and must hash to the challenge from the auth URL."""
        import base64
        import hashlib
        from urllib.parse import urlparse, parse_qs
        import core.oidc as mod

        jwt_jwks, _ = _make_test_jwks_and_key()
        mgr = _make_manager()
        url, state, nonce = mgr.get_authorization_url("https://app.example.com/callback")
        challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
        id_token = _make_id_token("user123", nonce)

        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [_mock_jwks_response(jwt_jwks)]
            mock_post.return_value = _mock_token_response(id_token)
            mgr.exchange_code("auth_code_xyz", state, "https://app.example.com/callback")

            posted = mock_post.call_args.kwargs["data"]
            verifier = posted["code_verifier"]
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            assert challenge == expected


class TestTokenEndpointAuth:
    def _exchange(self, discovery_doc):
        """Run a full exchange and return the httpx.post call kwargs."""
        import core.oidc as mod
        jwt_jwks, _ = _make_test_jwks_and_key()
        mgr = _make_manager(discovery_doc)
        _, state, nonce = mgr.get_authorization_url("https://app.example.com/callback")
        id_token = _make_id_token("user123", nonce)
        with patch.object(mod.httpx, "get") as mock_get, \
             patch.object(mod.httpx, "post") as mock_post:
            mock_get.side_effect = [_mock_jwks_response(jwt_jwks)]
            mock_post.return_value = _mock_token_response(id_token)
            mgr.exchange_code("code", state, "https://app.example.com/callback")
            return mock_post.call_args.kwargs

    def test_default_uses_client_secret_basic(self):
        """No token_endpoint_auth_methods_supported in discovery → the OIDC
        default client_secret_basic: HTTP Basic auth, no secret in the body."""
        kwargs = self._exchange(DISCOVERY_DOC)
        assert kwargs["auth"] == (FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
        assert "client_secret" not in kwargs["data"]
        assert "client_id" not in kwargs["data"]

    def test_basic_preferred_when_advertised(self):
        doc = dict(DISCOVERY_DOC)
        doc["token_endpoint_auth_methods_supported"] = [
            "client_secret_post", "client_secret_basic",
        ]
        kwargs = self._exchange(doc)
        assert kwargs["auth"] == (FAKE_CLIENT_ID, FAKE_CLIENT_SECRET)
        assert "client_secret" not in kwargs["data"]

    def test_post_fallback_when_basic_unsupported(self):
        doc = dict(DISCOVERY_DOC)
        doc["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
        kwargs = self._exchange(doc)
        assert kwargs["auth"] is None
        assert kwargs["data"]["client_secret"] == FAKE_CLIENT_SECRET
        assert kwargs["data"]["client_id"] == FAKE_CLIENT_ID


class TestHttpsEnforcement:
    def test_http_issuer_rejected(self):
        import core.oidc as mod
        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.return_value = _mock_discovery_response()
            with pytest.raises(mod.OidcError, match="issuer must use HTTPS"):
                mod.OidcManager(
                    issuer="http://idp.example.com",
                    client_id=FAKE_CLIENT_ID,
                    client_secret=FAKE_CLIENT_SECRET,
                )

    def test_http_authorization_endpoint_rejected(self):
        import core.oidc as mod
        doc = dict(DISCOVERY_DOC)
        doc["authorization_endpoint"] = "http://idp.example.com/authorize"
        with patch.object(mod.httpx, "get") as mock_get:
            mock_get.return_value = _FakeResponse(200, doc)
            with pytest.raises(mod.OidcError, match="authorization_endpoint must use HTTPS"):
                mod.OidcManager(
                    issuer=FAKE_ISSUER,
                    client_id=FAKE_CLIENT_ID,
                    client_secret=FAKE_CLIENT_SECRET,
                )


class TestMaxAgeConfiguration:
    def test_max_age_added_to_auth_url(self):
        mgr = _new_security_test_manager(max_age=3600)
        url, _, _ = mgr.get_authorization_url("https://app.example.com/callback")
        from urllib.parse import parse_qs, urlparse
        assert parse_qs(urlparse(url).query)["max_age"] == ["3600"]

    def test_max_age_unset_not_in_url(self):
        mgr = _new_security_test_manager()
        url, _, _ = mgr.get_authorization_url("https://app.example.com/callback")
        from urllib.parse import parse_qs, urlparse
        assert "max_age" not in parse_qs(urlparse(url).query)

    def test_parse_max_age_valid(self, monkeypatch):
        import core.oidc as mod
        monkeypatch.setenv("OIDC_MAX_AGE", "3600")
        assert mod._parse_max_age() == 3600
        monkeypatch.setenv("OIDC_MAX_AGE", "0")
        assert mod._parse_max_age() == 0
        monkeypatch.delenv("OIDC_MAX_AGE", raising=False)
        assert mod._parse_max_age() is None

    def test_parse_max_age_invalid(self, monkeypatch):
        import core.oidc as mod
        for value in ("not-int", "-1"):
            monkeypatch.setenv("OIDC_MAX_AGE", value)
            with pytest.raises(mod.OidcError):
                mod._parse_max_age()
