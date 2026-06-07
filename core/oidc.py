"""Generic OpenID Connect client — provider discovery, auth flow, id_token verification.

Configuration (env vars):
    OIDC_ENABLED=true|false          — master toggle
    OIDC_ISSUER=https://...          — provider issuer URL (must expose .well-known)
    OIDC_CLIENT_ID=odysseus          — client ID registered with the provider
    OIDC_CLIENT_SECRET=...           — client secret
    OIDC_SCOPES=openid profile email — space-separated scope list

State is stored in-memory with a 10-minute TTL.  No database / file
persistence is needed — a lost state only forces the user to restart the
OIDC flow, which is the expected UX anyway.
"""

import logging
import os
import secrets
import time
import threading
from typing import Optional, Dict, Any, Tuple

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory state store
# ---------------------------------------------------------------------------
_STATE_TTL = 600  # 10 minutes

_state_store: Dict[str, Dict[str, Any]] = {}
_state_lock = threading.Lock()


def _store_state(state: str, nonce: str, redirect_uri: str) -> None:
    entry = {"nonce": nonce, "redirect_uri": redirect_uri, "created": time.time()}
    with _state_lock:
        _prune_expired()
        _state_store[state] = entry


def _pop_state(state: str) -> Optional[Dict[str, Any]]:
    with _state_lock:
        _prune_expired()
        return _state_store.pop(state, None)


def _prune_expired() -> None:
    now = time.time()
    expired = [s for s, v in _state_store.items() if now - v["created"] > _STATE_TTL]
    for s in expired:
        del _state_store[s]


# ---------------------------------------------------------------------------
# OidcManager
# ---------------------------------------------------------------------------


class OidcError(Exception):
    """Raised for OIDC configuration or flow errors."""


class OidcManager:
    """Generic OpenID Connect client.

    On init, discovers the provider's endpoints via
    ``.well-known/openid-configuration`` and fetches the JWKS for
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
        self._discover()

    # -- discovery -----------------------------------------------------------

    def _discover(self) -> None:
        """Fetch .well-known/openid-configuration and JWKS."""
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

        logger.info(
            "OIDC provider discovered: issuer=%r auth=%r token=%r",
            self.issuer,
            self._config["authorization_endpoint"],
            self._config["token_endpoint"],
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

    # -- authorization URL ---------------------------------------------------

    def get_authorization_url(self, redirect_uri: str) -> Tuple[str, str, str]:
        """Build the provider's authorization URL.

        Returns ``(url, state, nonce)``.  The caller MUST store *state*
        and *nonce* and pass them to :meth:`exchange_code` on callback.
        """
        state = secrets.token_hex(32)
        nonce = secrets.token_hex(32)

        _store_state(state, nonce, redirect_uri)

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
        # 1. Verify state and recover the nonce
        stored = _pop_state(state)
        if stored is None:
            raise OidcError("OIDC state not found — may be expired or reused")
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

    def _verify_id_token(self, id_token: str, nonce: str) -> Dict[str, Any]:
        """Verify the id_token signature and claims. Returns the decoded payload."""
        from authlib.jose import jwt, JsonWebKey
        from authlib.jose.errors import JoseError

        # Fetch JWKS
        try:
            resp = httpx.get(self._config["jwks_uri"], timeout=15.0)
            resp.raise_for_status()
            jwks = resp.json()
        except Exception as exc:
            raise OidcError(f"Failed to fetch JWKS: {exc}") from exc

        # authlib needs a key set in the format it expects
        try:
            key_set = JsonWebKey.import_key_set(jwks)
        except Exception as exc:
            raise OidcError(f"Failed to import JWKS: {exc}") from exc

        # Decode (signature verification via JWKS)
        try:
            claims = jwt.decode(id_token, key_set)
        except JoseError as exc:
            raise OidcError(f"id_token signature verification failed: {exc}") from exc

        claims = dict(claims)

        # Manual claim validation — more explicit and version-agnostic
        expected_issuer = self._config.get("issuer") or self.issuer
        if claims.get("iss") != expected_issuer:
            raise OidcError(
                f"id_token iss mismatch: expected {expected_issuer!r}, got {claims.get('iss')!r}"
            )

        if claims.get("aud") != self.client_id:
            raise OidcError(
                f"id_token aud mismatch: expected {self.client_id!r}, got {claims.get('aud')!r}"
            )

        exp = claims.get("exp", 0)
        if time.time() > exp:
            raise OidcError(f"id_token expired at {exp}")

        # Verify nonce
        if claims.get("nonce") != nonce:
            raise OidcError("id_token nonce mismatch")

        return claims

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
