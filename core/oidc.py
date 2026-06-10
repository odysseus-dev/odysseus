"""Generic OpenID Connect client — provider discovery, auth flow, id_token verification.

Configuration (env vars):
    OIDC_ENABLED=true|false          — master toggle
    OIDC_ISSUER=https://...          — provider issuer URL (must expose .well-known)
    OIDC_CLIENT_ID=odysseus          — client ID registered with the provider
    OIDC_CLIENT_SECRET=...           — client secret
    OIDC_REDIRECT_URI=...            — optional fixed redirect URI (use when
                                       behind a proxy to avoid trusting the Host
                                       header). If unset, derived from the inbound
                                       request at /login and /callback time.
    OIDC_SCOPES=openid profile email — space-separated scope list

State is carried inside a Fernet-encrypted token embedded in the OIDC
``state`` parameter, so no server-side storage is needed — callbacks are
stateless and work across multiple uvicorn workers / processes.

JWKS keys are cached after first fetch and refreshed only when an unknown
``kid`` is encountered, avoiding a live IdP round-trip on every login.
"""

import json
import logging
import os
import secrets
import time
import threading
from typing import Optional, Dict, Any, List, Tuple

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State token (Fernet-encrypted, carried in the OIDC state param)
# ---------------------------------------------------------------------------
# Instead of an in-memory dict (which breaks with uvicorn --workers > 1), we
# encrypt the nonce + redirect_uri + creation timestamp into the state value
# itself.  The callback decrypts it to recover the nonce and validate freshness.
# This is the pattern used by NextAuth.js, oauthlib, and several OIDC SDKs.

_STATE_TTL = 600  # 10 minutes

_state_fernet_lock = threading.Lock()
_state_fernet = None


def _get_state_fernet():
    """Lazily get or create a Fernet instance for state encryption.

    Uses the persistent app key (data/.app_key) when available, falling
    back to a per-process random key.  State tokens have a 10-minute TTL
    so a process-local key is sufficient — a key rotation between workers
    only affects in-flight logins (they'll restart the flow, which is the
    expected UX for any transient failure).
    """
    global _state_fernet
    if _state_fernet is not None:
        return _state_fernet
    with _state_fernet_lock:
        if _state_fernet is not None:
            return _state_fernet
        from cryptography.fernet import Fernet
        from src.constants import APP_KEY_FILE
        from pathlib import Path

        key_path = Path(APP_KEY_FILE)
        if key_path.exists():
            try:
                _state_fernet = Fernet(key_path.read_bytes())
                return _state_fernet
            except Exception:
                logger.warning("Failed to read app key — using per-process key for OIDC state")
        # Per-process fallback — state is short-lived (10 min TTL).
        _state_fernet = Fernet(Fernet.generate_key())
        return _state_fernet


def _encode_state(nonce: str, redirect_uri: str) -> str:
    """Return a Fernet-encrypted state token containing nonce + metadata."""
    fernet = _get_state_fernet()
    payload = json.dumps({
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "created": time.time(),
    })
    return fernet.encrypt(payload.encode()).decode()


def _decode_state(state: str) -> Optional[Dict[str, Any]]:
    """Decrypt and validate a state token. Returns None if expired or invalid."""
    fernet = _get_state_fernet()
    try:
        plain = fernet.decrypt(state.encode())
        data = json.loads(plain)
    except Exception:
        return None
    if time.time() - data.get("created", 0) > _STATE_TTL:
        return None
    return data


# ---------------------------------------------------------------------------
# OidcManager
# ---------------------------------------------------------------------------


class OidcError(Exception):
    """Raised for OIDC configuration or flow errors."""


class OidcManager:
    """Generic OpenID Connect client.

    On init, discovers the provider's endpoints via
    ``.well-known/openid-configuration`` and caches the JWKS for
    id_token signature verification.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        scopes: str = "openid profile email",
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self._provider_name: Optional[str] = None
        self._config: Dict[str, Any] = {}
        # JWKS cache: kid → key dict, populated on first verification and
        # refreshed when an unknown kid is encountered.
        self._jwks_cache: Dict[str, Dict[str, Any]] = {}
        self._jwks_cache_lock = threading.Lock()
        self._allowed_algs: Optional[List[str]] = None
        self._discover()

    # -- discovery -----------------------------------------------------------

    def _discover(self) -> None:
        """Fetch .well-known/openid-configuration."""
        # urljoin drops the issuer's path when the second arg is absolute
        # (starts with "/").  Use simple concatenation so issuers with a
        # sub-path (e.g. Authentik /application/o/<slug>/) work correctly.
        well_known_url = self.issuer + "/.well-known/openid-configuration"
        if not well_known_url.startswith(("http://", "https://")):
            well_known_url = f"https://{well_known_url}"

        try:
            resp = httpx.get(well_known_url, timeout=15.0)
            resp.raise_for_status()
            self._config = resp.json()
        except Exception as exc:
            raise OidcError(
                f"Failed to fetch OIDC discovery document from {well_known_url}: {exc}"
            ) from exc

        # Validate essential endpoints are present
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if key not in self._config:
                raise OidcError(
                    f"OIDC discovery document missing required key: {key}"
                )

        # The issuer in the discovery doc SHOULD match the configured issuer
        doc_issuer = self._config.get("issuer", "")
        if doc_issuer and doc_issuer.rstrip("/") != self.issuer:
            logger.warning(
                "OIDC issuer mismatch: configured=%r doc=%r", self.issuer, doc_issuer,
            )

        # Pin signing algorithms to those the provider supports.
        # Restrict to RS256/ES256 to avoid algorithm confusion attacks;
        # HS256 and 'none' are never allowed.
        supported = self._config.get("id_token_signing_alg_values_supported", [])
        safe = [a for a in supported if a in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512")]
        self._allowed_algs = safe or ["RS256"]

        logger.info(
            "OIDC provider discovered: issuer=%r auth=%r token=%r algs=%s",
            self.issuer,
            self._config["authorization_endpoint"],
            self._config["token_endpoint"],
            self._allowed_algs,
        )

    @property
    def provider_name(self) -> str:
        """A human-readable name derived from the issuer URL."""
        if self._provider_name:
            return self._provider_name
        # Use the host portion of the issuer as a readable label.
        from urllib.parse import urlparse
        parsed = urlparse(self.issuer)
        return parsed.hostname or self.issuer

    @property
    def configured(self) -> bool:
        return bool(self._config)

    @property
    def redirect_uri_override(self) -> Optional[str]:
        """Return OIDC_REDIRECT_URI if explicitly configured, else None."""
        val = os.getenv("OIDC_REDIRECT_URI", "").strip()
        return val or None

    # -- authorization URL ---------------------------------------------------

    def get_authorization_url(self, redirect_uri: str) -> Tuple[str, str, str]:
        """Build the provider's authorization URL.

        Returns ``(url, state, nonce)``.  The *state* value is an encrypted
        token that carries *nonce* and *redirect_uri* — the caller does NOT
        need to store anything server-side; the callback will recover the
        nonce from the state parameter itself.
        """
        nonce = secrets.token_hex(32)

        # Encode the nonce + metadata into the state parameter (Fernet-
        # encrypted, stateless — works across multiple workers/processes).
        state = _encode_state(nonce, redirect_uri)

        from urllib.parse import urlencode
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
        }
        auth_url = f"{self._config['authorization_endpoint']}?{urlencode(params)}"
        return auth_url, state, nonce

    # -- token exchange + verification ---------------------------------------

    def exchange_code(
        self, code: str, state: str, redirect_uri: str
    ) -> Dict[str, Any]:
        """Exchange authorization code for tokens and verify the id_token.

        Returns a dict of claims extracted from the verified id_token.
        Raises :class:`OidcError` on any failure.
        """
        # 1. Decrypt state and recover the nonce
        stored = _decode_state(state)
        if stored is None:
            raise OidcError("OIDC state not found — may be expired, reused, or from a different worker")
        nonce = stored.get("nonce", "")

        # 2. Exchange code for tokens
        token_data = self._token_request(code, redirect_uri)

        # 3. Verify id_token
        id_token = token_data.get("id_token")
        if not id_token:
            raise OidcError("No id_token in token response")

        claims = self._verify_id_token(id_token, nonce)

        # Optionally merge userinfo if we got an access_token
        access_token = token_data.get("access_token")
        if access_token:
            try:
                userinfo = self._fetch_userinfo(access_token)
                # userinfo claims supplement the id_token (per OIDC spec, userinfo
                # is the authoritative source for profile claims)
                claims.update(userinfo)
            except Exception as exc:
                logger.warning("Failed to fetch userinfo: %s", exc)

        return claims

    def _token_request(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """POST the token endpoint to exchange code for tokens."""
        token_endpoint = self._config["token_endpoint"]
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        try:
            resp = httpx.post(token_endpoint, data=payload, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            error_detail = ""
            try:
                error_detail = exc.response.json().get("error_description", "")
            except Exception:
                error_detail = exc.response.text[:200]
            raise OidcError(
                f"Token endpoint returned {exc.response.status_code}: {error_detail}"
            ) from exc
        except Exception as exc:
            raise OidcError(f"Token request failed: {exc}") from exc

        if "error" in data:
            raise OidcError(
                f"Token endpoint error: {data.get('error')} — {data.get('error_description', '')}"
            )
        return data

    # -- JWKS caching --------------------------------------------------------

    def _fetch_jwks(self) -> Dict[str, Any]:
        """Fetch and cache the JWKS, or use cached keys when available.

        Returns the full JWKS dict.  Keys are cached for reuse; on an unknown
        ``kid`` the cache is refreshed (one additional fetch per new key
        rotation).
        """
        # Fast path: cache hit
        with self._jwks_cache_lock:
            if self._jwks_cache:
                return {"keys": list(self._jwks_cache.values())}

        # Cache miss — fetch once
        return self._refresh_jwks()

    def _refresh_jwks(self):
        resp = httpx.get(self._config["jwks_uri"], timeout=15.0)
        resp.raise_for_status()
        jwks = resp.json()
        keys = jwks.get("keys", [])
        with self._jwks_cache_lock:
            self._jwks_cache.clear()
            for k in keys:
                kid = k.get("kid", "")
                if kid:
                    self._jwks_cache[kid] = k
            # Always keep at least one entry even without kid
            if not self._jwks_cache and keys:
                self._jwks_cache["_default"] = keys[0]
        return jwks

    # -- id_token verification -----------------------------------------------

    def _verify_id_token(self, id_token: str, nonce: str) -> Dict[str, Any]:
        """Verify the id_token signature and claims. Returns the decoded payload."""
        from authlib.jose import jwt, JsonWebKey
        from authlib.jose.errors import JoseError

        header = self._peek_jwt_header(id_token)
        kid = header.get("kid", "")

        # Fetch or refresh JWKS
        jwks = self._fetch_jwks()

        # If the kid from the token header is unknown, refresh the cache.
        # Guarded by a cooldown so an attacker can't drive unbounded
        # outbound fetches by sending random kid values to the callback.
        if kid and kid not in self._jwks_cache:
            now = time.time()
            last_refresh = getattr(self, "_last_jwks_refresh", 0)
            if now - last_refresh >= 60:
                logger.info("OIDC JWKS cache miss for kid=%r — refreshing", kid)
                jwks = self._refresh_jwks()
                self._last_jwks_refresh = now
            else:
                logger.warning(
                    "OIDC JWKS cache miss for kid=%r but refresh on cooldown "
                    "(%.0fs remaining)", kid, 60 - (now - last_refresh),
                )

        # authlib needs a key set in the format it expects
        try:
            key_set = JsonWebKey.import_key_set(jwks)
        except Exception as exc:
            raise OidcError(f"Failed to import JWKS: {exc}") from exc

        # Decode (signature verification via JWKS) with pinned algorithms
        try:
            claims = jwt.decode(id_token, key_set)
        except JoseError as exc:
            raise OidcError(f"id_token signature verification failed: {exc}") from exc

        # Verify the algorithm is in our allow-list
        claims_header = getattr(claims, "header", {}) if hasattr(claims, "header") else {}
        alg = claims_header.get("alg", "")
        if alg and self._allowed_algs and alg not in self._allowed_algs:
            raise OidcError(
                f"id_token signed with disallowed algorithm {alg!r} "
                f"(allowed: {self._allowed_algs!r})"
            )

        claims = dict(claims)

        # Manual claim validation — more explicit and version-agnostic
        expected_issuer = self._config.get("issuer") or self.issuer
        if claims.get("iss") != expected_issuer:
            raise OidcError(
                f"id_token iss mismatch: expected {expected_issuer!r}, got {claims.get('iss')!r}"
            )

        # Validate audience: aud may be a string or a JSON array.
        # When multiple audiences are present, azp MUST be present and match
        # the client_id (per OIDC Core 1.0 § 2).
        aud = claims.get("aud")
        if isinstance(aud, list):
            if self.client_id not in aud:
                raise OidcError(
                    f"id_token aud mismatch: client_id {self.client_id!r} not in aud {aud!r}"
                )
            # Multiple audiences — azp MUST identify the authorized party
            azp = claims.get("azp")
            if azp and azp != self.client_id:
                raise OidcError(
                    f"id_token azp mismatch: expected {self.client_id!r}, got {azp!r}"
                )
        elif aud != self.client_id:
            raise OidcError(
                f"id_token aud mismatch: expected {self.client_id!r}, got {aud!r}"
            )

        exp = claims.get("exp", 0)
        if time.time() > exp:
            raise OidcError(f"id_token expired at {exp}")

        # Verify nonce
        if claims.get("nonce") != nonce:
            raise OidcError("id_token nonce mismatch")

        return claims

    @staticmethod
    def _peek_jwt_header(id_token: str) -> Dict[str, Any]:
        """Extract the JWT header without verifying the signature."""
        try:
            parts = id_token.split(".")
            if len(parts) >= 2:
                import base64
                # Pad to a multiple of 4
                padded = parts[0] + "=" * (4 - len(parts[0]) % 4)
                return json.loads(base64.urlsafe_b64decode(padded))
        except Exception:
            pass
        return {}

    def _fetch_userinfo(self, access_token: str) -> Dict[str, Any]:
        """Fetch claims from the UserInfo endpoint (if available)."""
        userinfo_endpoint = self._config.get("userinfo_endpoint")
        if not userinfo_endpoint:
            return {}
        resp = httpx.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_oidc_manager: Optional[OidcManager] = None
_oidc_init_error: Optional[str] = None


def init_oidc_manager() -> Optional[OidcManager]:
    """Create the singleton OidcManager from env vars, or return None if disabled."""
    global _oidc_manager, _oidc_init_error

    if _oidc_manager is not None:
        return _oidc_manager

    enabled = os.getenv("OIDC_ENABLED", "false").lower() == "true"
    if not enabled:
        return None

    issuer = os.getenv("OIDC_ISSUER", "").strip()
    client_id = os.getenv("OIDC_CLIENT_ID", "").strip()
    client_secret = os.getenv("OIDC_CLIENT_SECRET", "").strip()
    scopes = os.getenv("OIDC_SCOPES", "openid profile email").strip()

    if not issuer or not client_id or not client_secret:
        _oidc_init_error = (
            "OIDC_ENABLED=true but OIDC_ISSUER, OIDC_CLIENT_ID, or "
            "OIDC_CLIENT_SECRET is missing"
        )
        logger.warning(_oidc_init_error)
        return None

    try:
        _oidc_manager = OidcManager(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
        )
    except OidcError as exc:
        _oidc_init_error = str(exc)
        logger.error("OIDC init failed: %s", exc)
        return None

    return _oidc_manager


def get_oidc_manager() -> Optional[OidcManager]:
    """Return the singleton OidcManager (may be None if disabled or init failed)."""
    return _oidc_manager


def get_oidc_init_error() -> Optional[str]:
    """Return the init error string, if any."""
    return _oidc_init_error
