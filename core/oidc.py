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
    OIDC_MAX_AGE=3600                — optional maximum authentication age in
                                       seconds.  When set, the IdP is asked to
                                       re-authenticate the user and the
                                       ``auth_time`` claim is verified.

State is carried inside a Fernet-encrypted token embedded in the OIDC
``state`` parameter, so no server-side storage is needed — callbacks are
stateless and work across multiple uvicorn workers / processes.  The
encryption key is the shared persistent app key (``data/.app_key``,
managed by ``src.secret_storage``).

JWKS keys are cached after first fetch and refreshed only when an unknown
``kid`` is encountered, avoiding a live IdP round-trip on every login.  A
60-second cooldown throttles both successful and failed refreshes.
"""

import base64
import hashlib
import json
import logging
import math
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

# DESIGN NOTE — state tokens are deliberately NOT single-use.  Enforcing
# one-time consumption would require shared server-side storage, which
# this stateless design intentionally avoids (multi-worker support with
# no session store).  Replay of a state within its TTL is mitigated by:
#   - the authorization code being single-use at the IdP (a replayed
#     callback fails the token exchange),
#   - the nonce being bound into the signed id_token and verified,
#   - the PKCE verifier being bound to the same encrypted state, and
#   - the CSRF cookie requiring the completing browser to hold the state.

_state_fernet_lock = threading.Lock()
_state_fernet = None


def _get_state_fernet():
    """Lazily get or create a Fernet instance for state encryption.

    Uses the shared persistent app key (data/.app_key) from
    ``src.secret_storage._get_fernet()``, which creates the key file on
    first access.  This guarantees the same Fernet key is available to
    all uvicorn workers, even on a fresh data directory — the OIDC
    authorization state encrypted by worker A can always be decrypted
    by worker B on the callback.
    """
    global _state_fernet
    if _state_fernet is not None:
        return _state_fernet
    with _state_fernet_lock:
        if _state_fernet is not None:
            return _state_fernet
        from src.secret_storage import _get_fernet
        _state_fernet = _get_fernet()
        return _state_fernet


def _encode_state(nonce: str, redirect_uri: str, code_verifier: str) -> str:
    """Return a Fernet-encrypted state token containing nonce + metadata."""
    fernet = _get_state_fernet()
    payload = json.dumps({
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
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
    if not isinstance(data, dict):
        return None
    nonce = data.get("nonce")
    redirect_uri = data.get("redirect_uri")
    code_verifier = data.get("code_verifier")
    created = data.get("created")
    if not isinstance(nonce, str) or not nonce:
        return None
    if not isinstance(redirect_uri, str):
        return None
    if not isinstance(code_verifier, str) or not code_verifier:
        return None
    if not _is_numericdate(created):
        return None
    now = time.time()
    if created > now + 60 or now - created > _STATE_TTL:
        return None
    return data


# ---------------------------------------------------------------------------
# OidcManager
# ---------------------------------------------------------------------------


def _is_numericdate(value) -> bool:
    """Return True when *value* is a finite int/float that is not bool.

    Python's json module parses NaN/Inf by default, and isinstance(True,
    int) is True.  This helper rejects booleans, NaN, ±Inf, and non-
    numeric types so numeric claim checks don't silently pass on bogus
    input.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


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
        max_age: Optional[int] = None,
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        # Immutable — set once at init so concurrent callbacks sharing the
        # singleton manager see the same value (gpt-5.6-sol gap #1).
        self.max_age = max_age
        self._provider_name: Optional[str] = None
        self._config: Dict[str, Any] = {}
        # JWKS cache: kid → key dict, populated on first verification and
        # refreshed when an unknown kid is encountered.
        self._jwks_cache: Dict[str, Dict[str, Any]] = {}
        self._jwks_cache_lock = threading.Lock()
        self._allowed_algs: Optional[List[str]] = None
        self._token_auth_methods: List[str] = ["client_secret_basic"]
        self._discover()

    def _use_basic_auth(self) -> bool:
        """True when the token endpoint should use client_secret_basic."""
        if "client_secret_basic" in self._token_auth_methods:
            return True
        if "client_secret_post" in self._token_auth_methods:
            return False
        # Provider advertises neither shared-secret method — use the OIDC
        # default rather than silently leaking the secret in the body.
        return True

    # -- discovery -----------------------------------------------------------

    def _discover(self) -> None:
        """Fetch .well-known/openid-configuration."""
        # urljoin drops the issuer's path when the second arg is absolute
        # (starts with "/").  Use simple concatenation so issuers with a
        # sub-path (e.g. Authentik /application/o/<slug>/) work correctly.
        well_known_url = self.issuer + "/.well-known/openid-configuration"
        if not well_known_url.startswith(("http://", "https://")):
            well_known_url = f"https://{well_known_url}"
        # The issuer (and therefore the discovery document) must be HTTPS:
        # the authorization redirect carries state/nonce, and an http://
        # issuer lets an active network attacker substitute authorization
        # codes or rewrite the discovery document entirely.
        if not well_known_url.startswith("https://"):
            raise OidcError(
                f"OIDC issuer must use HTTPS, got {self.issuer!r}. "
                "Configure the IdP with TLS or set OIDC_ENABLED=false."
            )

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

        # The issuer in the discovery doc MUST match the configured issuer
        # (OIDC Discovery §1.1).  Failing closed prevents trust-path confusion
        # where a misconfigured or malicious discovery document could cause
        # id_token validation to accept a different issuer.
        doc_issuer = (self._config.get("issuer") or "").rstrip("/")
        if doc_issuer and doc_issuer != self.issuer:
            raise OidcError(
                f"OIDC issuer mismatch: configured {self.issuer!r}, "
                f"discovery doc returned {doc_issuer!r}"
            )

        # No OIDC endpoint may use cleartext transport.  The back-channel
        # endpoints carry client credentials and bearer tokens; the browser-
        # facing authorization endpoint carries state/nonce and returns the
        # authorization code, so an http:// endpoint enables code
        # substitution by an active network observer.
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint"):
            url = self._config.get(name)
            if url and not isinstance(url, str):
                raise OidcError(f"OIDC {name} must be a URL string")
            if url and not url.startswith("https://"):
                raise OidcError(
                    f"OIDC {name} must use HTTPS, got {url!r}. "
                    "Configure the IdP with TLS or set OIDC_ENABLED=false."
                )

        # Pin signing algorithms to those the provider supports.
        # Restrict to RS256/ES256 to avoid algorithm confusion attacks;
        # HS256 and 'none' are never allowed.
        supported = self._config.get("id_token_signing_alg_values_supported", [])
        safe = [a for a in supported if a in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512")]
        self._allowed_algs = safe or ["RS256"]

        # Token-endpoint auth methods.  Per OIDC Discovery §3, an omitted
        # token_endpoint_auth_methods_supported means client_secret_basic.
        methods = self._config.get("token_endpoint_auth_methods_supported")
        if not isinstance(methods, list) or not methods:
            methods = ["client_secret_basic"]
        self._token_auth_methods = methods

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

        # PKCE (RFC 7636, S256).  The verifier travels inside the encrypted
        # state token, so the callback can recover it without server-side
        # storage — same carrier as the nonce.
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )

        # Encode the nonce + metadata into the state parameter (Fernet-
        # encrypted, stateless — works across multiple workers/processes).
        state = _encode_state(nonce, redirect_uri, code_verifier)

        from urllib.parse import urlencode
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        # Request forced re-authentication when OIDC_MAX_AGE is configured.
        # The claim is later verified in _verify_id_token against auth_time.
        if self.max_age is not None:
            params["max_age"] = str(self.max_age)
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
        # 1. Decrypt state and recover the nonce and original redirect_uri
        stored = _decode_state(state)
        if stored is None:
            raise OidcError("OIDC state not found — may be expired, reused, or from a different worker")
        nonce = stored.get("nonce", "")
        stored_redirect_uri = stored.get("redirect_uri", "")
        code_verifier = stored.get("code_verifier", "")

        # Bind the token exchange to the redirect_uri that was used in the
        # authorization request (carried in the signed state token).  Reject
        # any callback-derived redirect_uri that differs — this removes the
        # last callback dependence on request-derived redirect URI behaviour.
        if stored_redirect_uri and stored_redirect_uri != redirect_uri:
            raise OidcError(
                f"OIDC redirect_uri mismatch: state={stored_redirect_uri!r} "
                f"callback={redirect_uri!r}"
            )

        # 2. Exchange code for tokens (using the stored redirect_uri)
        token_data = self._token_request(
            code, stored_redirect_uri or redirect_uri, code_verifier
        )

        # 3. Verify id_token
        id_token = token_data.get("id_token")
        if not id_token:
            raise OidcError("No id_token in token response")

        claims = self._verify_id_token(id_token, nonce)

        # Optionally merge userinfo if we got an access_token.
        # Per OIDC spec, userinfo is authoritative for profile claims (name,
        # email, picture, etc.) but MUST NOT overwrite verified identity
        # claims from the id_token (sub, iss, aud, exp, iat, nonce, azp).
        #
        # SECURITY: a UserInfo response without a ``sub`` is not bound to
        # the authenticated subject.  Any endpoint can return arbitrary
        # groups/roles/permissions data; refusing to merge or mark available
        # prevents unbound claims from driving local authorisation decisions.
        access_token = token_data.get("access_token")
        userinfo_available = False
        userinfo = {}  # ensure defined even if _fetch_userinfo raises
        if access_token:
            try:
                userinfo = self._fetch_userinfo(access_token)
                if userinfo is None:
                    # No userinfo_endpoint in discovery — not an error.
                    userinfo = {}
                elif not isinstance(userinfo, dict):
                    # Malformed response (list, string, null, …) — log and
                    # treat as unavailable.  Do not merge any claims.
                    logger.warning(
                        "UserInfo endpoint returned non-dict type %s — "
                        "treating as unavailable",
                        type(userinfo).__name__,
                    )
                    userinfo = {}
                else:
                    # Require a non-empty sub that exactly matches the
                    # verified id_token subject before trusting any UserInfo
                    # claims.  No normalization: subs are opaque identifiers
                    # and trimming could equate two distinct subjects.
                    ui_sub = userinfo.get("sub")
                    if not isinstance(ui_sub, str):
                        ui_sub = ""
                    if not ui_sub:
                        logger.warning(
                            "UserInfo response missing sub claim — "
                            "discarding entire response to prevent "
                            "unbound claim injection"
                        )
                        userinfo = {}
                    elif ui_sub != (claims.get("sub") or ""):
                        raise OidcError(
                            f"UserInfo sub mismatch: id_token={claims.get('sub')!r} "
                            f"userinfo={ui_sub!r}"
                        )
                    else:
                        # Sub present and matches — safe to merge.
                        userinfo_available = True
                        # Merge only safe profile claims — never overwrite
                        # verified identity/security fields.
                        _IDENTITY_CLAIMS = frozenset({
                            "sub", "iss", "aud", "exp", "iat", "nonce", "azp",
                        })
                        for k, v in userinfo.items():
                            if k not in _IDENTITY_CLAIMS:
                                claims[k] = v
            except OidcError:
                raise
            except Exception as exc:
                logger.warning("Failed to fetch userinfo: %s", exc)
                userinfo = {}

        # Let the callback know whether UserInfo was successfully fetched.
        # When UserInfo is unavailable, group membership claims may be
        # incomplete — the callback must not demote existing admins based
        # on missing evidence.
        claims["_userinfo_available"] = userinfo_available
        return claims

    def _token_request(
        self, code: str, redirect_uri: str, code_verifier: str
    ) -> Dict[str, Any]:
        """POST the token endpoint to exchange code for tokens."""
        token_endpoint = self._config["token_endpoint"]
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        # client_secret_basic is the OIDC default and the method the
        # conformance suite expects; use it whenever the provider supports
        # it (or doesn't advertise methods at all, which per Discovery §3
        # means client_secret_basic).  Fall back to client_secret_post only
        # when the provider explicitly excludes basic.
        auth = None
        if self._use_basic_auth():
            from urllib.parse import quote
            auth = (
                quote(self.client_id, safe=""),
                quote(self.client_secret, safe=""),
            )
        else:
            payload["client_id"] = self.client_id
            payload["client_secret"] = self.client_secret
        try:
            resp = httpx.post(token_endpoint, data=payload, auth=auth, timeout=15.0)
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
        try:
            resp = httpx.get(self._config["jwks_uri"], timeout=15.0)
            resp.raise_for_status()
            jwks = resp.json()
        except OidcError:
            raise
        except Exception as exc:
            raise OidcError(f"JWKS fetch/parse failed: {exc}") from exc
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
        alg = header.get("alg", "")
        kid = header.get("kid", "")

        # Reject disallowed algorithms before importing keys or verifying the
        # signature. This keeps the JOSE policy independent of Authlib's
        # decoded-claims header API.
        if not alg or (self._allowed_algs and alg not in self._allowed_algs):
            raise OidcError(
                f"id_token signed with disallowed algorithm {alg!r} "
                f"(allowed: {self._allowed_algs!r})"
            )

        # Fetch or refresh JWKS
        jwks = self._fetch_jwks()

        # If the kid from the token header is unknown, refresh the cache.
        # Guarded by a cooldown so an attacker can't drive unbounded
        # outbound fetches by sending random kid values to the callback.
        refresh_jwks = False
        if kid:
            with self._jwks_cache_lock:
                if kid not in self._jwks_cache:
                    now = time.time()
                    last_refresh = getattr(self, "_last_jwks_refresh", 0)
                    if now - last_refresh >= 60:
                        logger.info("OIDC JWKS cache miss for kid=%r — refreshing", kid)
                        # Record the attempt before I/O so failed refreshes
                        # are throttled too. The lock protects this marker
                        # against concurrent callback threads.
                        self._last_jwks_refresh = now
                        refresh_jwks = True
                    else:
                        logger.warning(
                            "OIDC JWKS cache miss for kid=%r but refresh on cooldown "
                            "(%.0fs remaining)",
                            kid, 60 - (now - last_refresh),
                        )
        if refresh_jwks:
            jwks = self._refresh_jwks()

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

        claims = dict(claims)

        # Manual claim validation — more explicit and version-agnostic
        expected_issuer = self._config.get("issuer") or self.issuer
        if claims.get("iss") != expected_issuer:
            raise OidcError(
                f"id_token iss mismatch: expected {expected_issuer!r}, got {claims.get('iss')!r}"
            )

        # Validate audience: aud may be a string or a JSON array.
        # Normalize to a list first so a single-element array (e.g.
        # ["client_id"]) is treated identically to a string aud.
        # OIDC Core 1.0 § 2: azp is REQUIRED when aud contains multiple
        # values, and MUST equal client_id.  We reject multi-audience tokens
        # without azp — there is no trusted-additional-audience model.
        aud = claims.get("aud")
        aud_list = aud if isinstance(aud, list) else [aud]
        if self.client_id not in aud_list:
            raise OidcError(
                f"id_token aud mismatch: client_id {self.client_id!r} not in aud {aud!r}"
            )
        if len(aud_list) > 1 and not claims.get("azp"):
            raise OidcError(
                "id_token has multiple audiences but no azp claim "
                "(required by OIDC Core 1.0 § 2)"
            )
        # OIDC Core § 2: whenever azp is present, it must identify this RP,
        # including single-audience tokens.
        azp = claims.get("azp")
        if azp is not None and azp != self.client_id:
            raise OidcError(
                f"id_token azp mismatch: expected {self.client_id!r}, got {azp!r}"
            )

        exp = claims.get("exp", 0)
        if not _is_numericdate(exp) or time.time() > exp:
            raise OidcError(f"id_token expired at {exp}")

        # Verify nonce with constant-time comparison.
        token_nonce = claims.get("nonce", "")
        if not isinstance(token_nonce, str) or not secrets.compare_digest(token_nonce, nonce):
            raise OidcError("id_token nonce mismatch")

        # Verify auth_time when max_age was requested.
        # Validate NumericDate strictly — reject non-numeric,
        # boolean, NaN/infinite, missing, or future values.
        if self.max_age is not None:
            auth_time = claims.get("auth_time")
            if not _is_numericdate(auth_time):
                raise OidcError(
                    f"id_token missing or non-numeric auth_time claim "
                    f"(required when OIDC_MAX_AGE={self.max_age})"
                )
            now = time.time()
            if auth_time > now + 60:
                raise OidcError(
                    f"id_token auth_time {auth_time} is more than 60 s in "
                    f"the future (clock skew?)"
                )
            if now - auth_time > self.max_age + 60:
                raise OidcError(
                    f"id_token auth_time {auth_time} exceeds max_age "
                    f"{self.max_age} s (now={now:.0f}, age={now - auth_time:.0f} s)"
                )

        # Verify iat (issued-at): required by OIDC Core §2, must be numeric
        # and not in the far future.
        iat = claims.get("iat")
        if iat is None:
            raise OidcError("id_token missing iat claim")
        if not _is_numericdate(iat):
            raise OidcError(f"id_token iat claim is non-numeric: {iat!r}")
        if iat > time.time() + 60:
            raise OidcError(
                f"id_token iat {iat} is more than 60 s in the future"
            )

        return claims

    @staticmethod
    def _peek_jwt_header(id_token: str) -> Dict[str, Any]:
        """Extract the JWT header without verifying the signature."""
        try:
            parts = id_token.split(".")
            if len(parts) >= 2:
                import base64
                # Pad to a multiple of 4 (base64url)
                pad_len = (-len(parts[0])) % 4
                padded = parts[0] + ("=" * pad_len)
                header = json.loads(base64.urlsafe_b64decode(padded))
                return header if isinstance(header, dict) else {}
        except Exception:
            pass
        return {}

    def _fetch_userinfo(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fetch claims from the UserInfo endpoint.

        Returns None when discovery has no userinfo_endpoint so the
        caller can distinguish "no endpoint configured" from "endpoint
        returned an empty profile".  An empty dict means the endpoint
        was reached but returned no claims.
        """
        userinfo_endpoint = self._config.get("userinfo_endpoint")
        if not userinfo_endpoint:
            return None
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
    scope_list = [s for s in scopes.split() if s]
    if "openid" not in scope_list:
        scope_list.insert(0, "openid")
    scopes = " ".join(scope_list)

    if not issuer or not client_id or not client_secret:
        _oidc_init_error = (
            "OIDC_ENABLED=true but OIDC_ISSUER, OIDC_CLIENT_ID, or "
            "OIDC_CLIENT_SECRET is missing"
        )
        logger.warning(_oidc_init_error)
        return None

    try:
        max_age = _parse_max_age()
        _oidc_manager = OidcManager(
            issuer=issuer,
            client_id=client_id,
            client_secret=client_secret,
            scopes=scopes,
            max_age=max_age,
        )
    except OidcError as exc:
        _oidc_init_error = str(exc)
        logger.error("OIDC init failed: %s", exc)
        return None

    return _oidc_manager


def _parse_max_age() -> Optional[int]:
    """Parse OIDC_MAX_AGE into an integer or None.  Returns None when
    unset/empty, raises OidcError on invalid values."""
    raw = os.getenv("OIDC_MAX_AGE", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise OidcError(
            f"OIDC_MAX_AGE must be an integer, got {raw!r}"
        ) from None
    if value < 0:
        raise OidcError(
            f"OIDC_MAX_AGE must be >= 0, got {value}"
        )
    return value


def get_oidc_manager() -> Optional[OidcManager]:
    """Return the singleton OidcManager (may be None if disabled or init failed)."""
    return _oidc_manager


def get_oidc_init_error() -> Optional[str]:
    """Return the init error string, if any."""
    return _oidc_init_error
