"""Sessionless in-stack 9router connect client.

Odysseus may list/create/delete connections and start/complete OAuth against
unpublished overlay 9router. It must never call inference (``/v1/*``) or send
operators to ``/dashboard/providers``.

Guest probe (9router 0.5.69, 2026-09-20):
- Auth: header ``x-9r-cli-token`` = sha256(machine-id + ``9r-cli-auth`` +
  ``auth/cli-secret``).hex[:16]. Cookie dashboard login is not required.
- ``GET /api/providers`` → ``{connections: [...]}``.
- ``POST /api/providers`` body ``{provider, apiKey}`` → 201 ``{connection: {...}}``.
- ``GET /api/oauth/{provider}/authorize`` → ``{authUrl, state}`` (IdP, not dashboard).
- ``POST /api/oauth/{provider}/exchange`` completes OAuth.
- ``DELETE /api/providers/{id}`` removes a connection.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

_PROVIDER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CONNECTION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

import httpx

from services.ninerouter.metadata import (
    NineRouterMetadataError,
    _redact_mapping,
    metadata_token,
    ninerouter_metadata_url,
    redact_providers,
)

# Locked from overlay compose exec probe. Do not add /v1 or /dashboard.
CONNECT_GET_EXACT: frozenset[str] = frozenset({"/api/providers"})
CONNECT_POST_EXACT: frozenset[str] = frozenset({"/api/providers"})
CONNECT_GET_PREFIXES: tuple[str, ...] = ("/api/oauth/",)
CONNECT_POST_PREFIXES: tuple[str, ...] = ("/api/oauth/",)
CONNECT_DELETE_PREFIXES: tuple[str, ...] = ("/api/providers/",)

CLI_TOKEN_HEADER = "x-9r-cli-token"
CLI_TOKEN_SALT = "9r-cli-auth"


class NineRouterConnectError(NineRouterMetadataError):
    """Connect call failed or returned a 9router dashboard URL."""


def normalize_connect_path(method: str, path: str) -> str:
    """Return a cleaned path if (method, path) is on the connect allowlist.

    Parameters
    ----------
    method
        HTTP verb.
    path
        Absolute path, optionally including a query string.

    Returns
    -------
    str
        Path without query or fragment.

    Raises
    ------
    NineRouterMetadataError
        If the path is inference, dashboard, or otherwise forbidden.
    """
    verb = (method or "GET").upper().strip()
    raw = (path or "").strip() or "/"
    parsed = urlparse(raw if raw.startswith("/") else f"/{raw}")
    cleaned = parsed.path or "/"
    cleaned = posixpath.normpath(cleaned)
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    if cleaned != "/" and cleaned.endswith("/"):
        cleaned = cleaned.rstrip("/")
    if (
        cleaned.startswith("/v1/")
        or cleaned == "/v1"
        or "chat/completions" in cleaned
        or "/dashboard" in cleaned
    ):
        raise NineRouterMetadataError("path is outside the 9router connect allowlist")
    if verb == "GET" and (cleaned in CONNECT_GET_EXACT or cleaned.startswith(CONNECT_GET_PREFIXES)):
        return cleaned
    if verb == "POST" and (cleaned in CONNECT_POST_EXACT or cleaned.startswith(CONNECT_POST_PREFIXES)):
        return cleaned
    if verb == "DELETE" and cleaned.startswith(CONNECT_DELETE_PREFIXES) and cleaned != "/api/providers":
        return cleaned
    raise NineRouterMetadataError("path is outside the 9router connect allowlist")


def _safe_provider(provider: str) -> str:
    """Reject path-injection in 9router provider slugs."""
    slug = (provider or "").strip()
    if not _PROVIDER_RE.match(slug):
        raise NineRouterConnectError("invalid 9router provider id")
    return slug


def _safe_connection_id(connection_id: str) -> str:
    """Reject path-injection in opaque connection ids."""
    cid = (connection_id or "").strip()
    if not _CONNECTION_ID_RE.match(cid):
        raise NineRouterConnectError("invalid connection_id")
    return cid


def derive_cli_token(data_dir: str) -> str:
    """Hash overlay 9router machine-id + cli-secret into the CLI header value.

    Parameters
    ----------
    data_dir
        9router ``DATA_DIR`` (contains ``machine-id`` and ``auth/cli-secret``).

    Returns
    -------
    str
        16-hex-character token for ``x-9r-cli-token``.

    Raises
    ------
    NineRouterConnectError
        If the files are missing or unreadable.
    """
    root = Path(data_dir)
    try:
        machine = (root / "machine-id").read_text(encoding="utf-8").strip()
        secret = (root / "auth" / "cli-secret").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise NineRouterConnectError(f"9router CLI secret files missing: {exc}") from exc
    if not machine or not secret:
        raise NineRouterConnectError("9router CLI secret files are empty")
    digest = hashlib.sha256(f"{machine}{CLI_TOKEN_SALT}{secret}".encode("utf-8")).hexdigest()
    return digest[:16]


def connect_token() -> str:
    """Return CLI header token from env or derived overlay data dir.

    Returns
    -------
    str
        Token string, possibly empty if neither source is configured.
    """
    explicit = metadata_token()
    if explicit:
        return explicit
    data_dir = os.getenv("NINE_ROUTER_DATA_DIR", "").strip()
    if not data_dir:
        sqlite = os.getenv("NINE_ROUTER_SQLITE_PATH", "").strip()
        if sqlite:
            # Guest worker mount: .../9router/db/data.sqlite → DATA_DIR is parents[1].
            data_dir = str(Path(sqlite).resolve().parent.parent)
    if not data_dir:
        return ""
    try:
        return derive_cli_token(data_dir)
    except NineRouterConnectError:
        return ""


def _reject_dashboard_url(url: str) -> str:
    """Require an upstream IdP URL, never 9router dashboard.

    Parameters
    ----------
    url
        Candidate authorization URL.

    Returns
    -------
    str
        The same URL if it is an IdP.

    Raises
    ------
    NineRouterConnectError
        If the URL is empty, dashboard, or in-stack 9router.
    """
    raw = (url or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if not raw or "/dashboard" in path or host in {"9router", "localhost", "127.0.0.1"}:
        raise NineRouterConnectError("authorization_url must be an upstream IdP, not 9router dashboard")
    return raw


class NineRouterConnectClient:
    """POST/GET/DELETE 9router connect API; never inference."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        fetch: Callable[[str, str, dict[str, str], Optional[dict]], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or ninerouter_metadata_url()).rstrip("/")
        self.token = token if token is not None else connect_token()
        self._fetch = fetch

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers[CLI_TOKEN_HEADER] = self.token
        return headers

    def _request(self, method: str, path: str, json_body: dict | None = None) -> Any:
        parsed = urlparse(path if path.startswith("/") else f"/{path}")
        cleaned = normalize_connect_path(method, parsed.path)
        query = parsed.query
        url = f"{self.base_url}{cleaned}"
        if query:
            url = f"{url}?{query}"
        if self._fetch is not None:
            return self._fetch(method.upper(), cleaned if not query else f"{cleaned}?{query}", self._headers(), json_body)
        try:
            response = httpx.request(
                method.upper(),
                url,
                headers=self._headers(),
                json=json_body,
                timeout=15.0,
            )
            response.raise_for_status()
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        except NineRouterMetadataError:
            raise
        except Exception as exc:
            raise NineRouterConnectError(f"9router connect request failed: {exc}") from exc

    def _one_row(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("connection"), dict):
            payload = payload["connection"]
        rows = redact_providers(payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else [])
        if rows:
            return rows[0]
        if isinstance(payload, dict):
            cleaned = _redact_mapping(payload)
            return cleaned if isinstance(cleaned, dict) else {}
        return {}

    def list_providers(self) -> list[dict[str, Any]]:
        """Return redacted connection rows from overlay 9router."""
        return redact_providers(self._request("GET", "/api/providers"))

    def create_api_key(self, provider: str, api_key: str) -> dict[str, Any]:
        """Create an API-key connection; response is redacted (no key)."""
        payload = self._request(
            "POST",
            "/api/providers",
            {"provider": _safe_provider(provider), "apiKey": api_key},
        )
        row = self._one_row(payload)
        if not str(row.get("id") or "").strip():
            raise NineRouterConnectError("9router create did not return a connection id")
        return row

    def import_codex_token(self, access_token: str) -> dict[str, Any]:
        """Deposit a ChatGPT device-flow access token in overlay 9router.

        Agents: POST ``/api/oauth/codex/import-token`` with ``accessToken`` only.
        Odysseus must not persist that token. Do not log the token.
        """
        token = (access_token or "").strip()
        if not token:
            raise NineRouterConnectError("missing ChatGPT access token")
        payload = self._request(
            "POST",
            "/api/oauth/codex/import-token",
            {"accessToken": token},
        )
        row = self._one_row(payload)
        if not str(row.get("id") or "").strip():
            raise NineRouterConnectError("9router import-token did not return a connection id")
        return row

    def start_oauth(self, provider: str, redirect_uri: str) -> dict[str, str]:
        """Start OAuth; returns IdP ``authorization_url`` plus 9router ``state``."""
        from urllib.parse import quote

        path = f"/api/oauth/{_safe_provider(provider)}/authorize?redirect_uri={quote(redirect_uri, safe='')}"
        payload = self._request("GET", path)
        if not isinstance(payload, dict):
            raise NineRouterConnectError("oauth start did not return JSON")
        url = payload.get("authUrl") or payload.get("authorization_url") or payload.get("url") or ""
        out = {"authorization_url": _reject_dashboard_url(str(url))}
        if payload.get("state"):
            out["state"] = str(payload["state"])
        return out

    def complete_oauth(self, provider: str, code: str, state: str | None = None) -> dict[str, Any]:
        """Exchange an IdP authorization code on overlay 9router."""
        body: dict[str, Any] = {"code": code}
        if state:
            body["state"] = state
        payload = self._request("POST", f"/api/oauth/{_safe_provider(provider)}/exchange", body)
        return self._one_row(payload)

    def delete_connection(self, connection_id: str) -> None:
        """Delete a 9router connection by opaque id."""
        cid = _safe_connection_id(connection_id)
        self._request("DELETE", f"/api/providers/{cid}")
