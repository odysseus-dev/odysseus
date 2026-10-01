"""Allowlisted 9router metadata client and curated route projection.

Odysseus may read health, redacted providers, and usage. It must never call
``/v1/chat/completions`` or treat a 9router virtual inference key as a
metadata credential.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import httpx


CURATED_ALIAS_LABELS = {
    "fast": "Fast",
    "balanced": "Balanced",
    "best": "Best",
}

_ALLOWED_EXACT = {"/api/health", "/api/providers"}
_ALLOWED_PREFIXES = ("/api/usage/",)
_SECRET_KEY_MARKERS = (
    "apikey",
    "api_key",
    "accesstoken",
    "access_token",
    "refreshtoken",
    "refresh_token",
    "idtoken",
    "id_token",
    "token",
    "secret",
    "password",
    "baseurl",
    "base_url",
    "url",
)


class NineRouterMetadataError(RuntimeError):
    """Raised when a metadata path is forbidden or the request fails."""


def ninerouter_metadata_url() -> str:
    """Return the in-network 9router origin used for allowlisted GETs.

    Returns
    -------
    str
        Origin without a trailing slash.

    Examples
    --------
    >>> isinstance(ninerouter_metadata_url(), str)
    True
    """
    return (
        os.getenv("NINE_ROUTER_METADATA_URL", "").strip().rstrip("/")
        or "http://9router:20128"
    )


def metadata_token() -> str:
    """Return an optional dashboard metadata token, never an inference key.

    Returns
    -------
    str
        Token from ``NINE_ROUTER_METADATA_TOKEN`` only.

    Examples
    --------
    >>> isinstance(metadata_token(), str)
    True
    """
    return os.getenv("NINE_ROUTER_METADATA_TOKEN", "").strip()


def normalize_metadata_path(path: str) -> str:
    """Normalize a metadata path and reject completions or other APIs.

    Parameters
    ----------
    path
        Absolute path, optionally including a query string.

    Returns
    -------
    str
        Path without query or fragment.

    Raises
    ------
    NineRouterMetadataError
        If the path is outside the health/providers/usage allowlist.

    Examples
    --------
    >>> normalize_metadata_path("/api/health")
    '/api/health'
    """
    raw = (path or "").strip() or "/"
    parsed = urlparse(raw if raw.startswith("/") else f"/{raw}")
    cleaned = parsed.path or "/"
    if cleaned != "/" and cleaned.endswith("/"):
        cleaned = cleaned.rstrip("/")
    if cleaned.startswith("/v1/") or cleaned == "/v1" or "chat/completions" in cleaned:
        raise NineRouterMetadataError("path is outside the 9router metadata allowlist")
    if cleaned in _ALLOWED_EXACT:
        return cleaned
    if cleaned.startswith(_ALLOWED_PREFIXES):
        return cleaned
    raise NineRouterMetadataError("path is outside the 9router metadata allowlist")


def _redact_mapping(value: Any) -> Any:
    if isinstance(value, list):
        return [_redact_mapping(item) for item in value]
    if not isinstance(value, dict):
        return value
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        marker = str(key).replace("-", "_").lower()
        if marker in _SECRET_KEY_MARKERS or marker.endswith("_token") or marker.endswith("_key"):
            continue
        redacted[key] = _redact_mapping(item)
    return redacted


def redact_providers(payload: Any) -> list[dict[str, Any]]:
    """Return provider rows with secrets and raw URLs removed.

    Parameters
    ----------
    payload
        9router ``GET /api/providers`` JSON.

    Returns
    -------
    list of dict
        Redacted connection rows.

    Examples
    --------
    >>> redact_providers([{"id": "c1", "apiKey": "sk"}])
    [{'id': 'c1'}]
    """
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("providers") or payload.get("data") or payload.get("connections") or []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            cleaned = _redact_mapping(row)
            if isinstance(cleaned, dict):
                out.append(cleaned)
    return out


def _alias_tokens(row: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for key in ("aliases", "routingAliases", "alias", "routes"):
        raw = row.get(key)
        if isinstance(raw, str):
            tokens.add(raw.strip().lower())
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    tokens.add(item.strip().lower())
                elif isinstance(item, dict):
                    for field in ("id", "name", "alias"):
                        val = item.get(field)
                        if isinstance(val, str):
                            tokens.add(val.strip().lower())
    for field in ("id", "name", "displayName", "defaultModel"):
        val = row.get(field)
        if isinstance(val, str):
            tokens.add(val.strip().lower())
    return tokens


def build_curated_chat_routes(providers: Any) -> list[dict[str, str]]:
    """Build Automatic plus optional Fast/Balanced/Best aliases.

    Parameters
    ----------
    providers
        Redacted 9router provider list or envelope.

    Returns
    -------
    list of dict
        Route objects with ``id`` and ``label`` only.

    Examples
    --------
    >>> build_curated_chat_routes([])[0]
    {'id': 'automatic', 'label': 'Automatic'}
    """
    rows = providers
    if isinstance(providers, dict):
        rows = providers.get("providers") or providers.get("data") or providers.get("connections") or []
    found: set[str] = set()
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                found.update(_alias_tokens(row) & set(CURATED_ALIAS_LABELS))
    routes = [{"id": "automatic", "label": "Automatic"}]
    for key in ("fast", "balanced", "best"):
        if key in found:
            routes.append({"id": key, "label": CURATED_ALIAS_LABELS[key]})
    return routes


class NineRouterMetadataClient:
    """GET-only 9router client restricted to health, providers, and usage."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        fetch: Callable[[str, dict[str, str]], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or ninerouter_metadata_url()).rstrip("/")
        self.token = token if token is not None else metadata_token()
        self._fetch = fetch

    def get(self, path: str) -> Any:
        """GET an allowlisted metadata path.

        Parameters
        ----------
        path
            Path beginning with ``/api/health``, ``/api/providers``, or
            ``/api/usage/``.

        Returns
        -------
        Any
            Decoded JSON body.

        Raises
        ------
        NineRouterMetadataError
            If the path is forbidden or the request fails.

        Examples
        --------
        >>> NineRouterMetadataClient(fetch=lambda p, h: {"ok": True}).get("/api/health")
        {'ok': True}
        """
        cleaned = normalize_metadata_path(path)
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._fetch is not None:
            return self._fetch(cleaned, headers)
        try:
            response = httpx.get(
                f"{self.base_url}{cleaned}",
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except NineRouterMetadataError:
            raise
        except Exception as exc:
            raise NineRouterMetadataError(f"9router metadata request failed: {exc}") from exc
