# services/laya/client.py
"""Thin async HTTP client for the laya-serve sidecar.

Only this module speaks to laya over the wire. It knows two endpoints:

  * ``GET  /health``        — liveness + which checkpoints/device are resident.
  * ``POST /v1/systemone``  — the single-forward-pass decision call.

Design notes:
  * laya has NO side effects (it classifies text and returns probabilities), so
    retrying a failed call can never repeat a destructive action. We retry only
    *transient* failures (connect/read timeouts, 5xx, 503-busy) and never 4xx.
  * The client never raises into Odysseus request handlers on its own — callers
    (the service layer) decide the fail-open policy. It raises the typed
    exceptions below so the service layer can distinguish "laya unavailable"
    from "laya returned something unusable".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from .config import LayaConfig

logger = logging.getLogger(__name__)


class LayaError(Exception):
    """Base class for laya client errors."""


class LayaUnavailable(LayaError):
    """laya could not be reached, timed out, or returned a 5xx after retries."""


class LayaInvalidResponse(LayaError):
    """laya replied, but with a non-2xx 4xx or an unparseable body."""


# Status codes we treat as transient and worth retrying.
_TRANSIENT_STATUS = {500, 502, 503, 504}


class LayaClient:
    """Reusable async client. One instance per (url, api_key) is plenty.

    A custom ``transport`` can be injected for tests (e.g. httpx.MockTransport);
    production code leaves it None and httpx opens real connections.
    """

    def __init__(
        self,
        config: LayaConfig,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._config.url,
                timeout=self._config.timeout,
                headers=self._headers(),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _request(self, method: str, path: str, *, json: Any = None) -> Dict[str, Any]:
        """Issue a request with bounded transient-retry. Returns parsed JSON."""
        client = self._get_client()
        attempts = self._config.retries + 1
        last_exc: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                resp = await client.request(method, path, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning("laya %s %s transport error (attempt %d/%d): %s",
                               method, path, attempt + 1, attempts, exc)
            else:
                if resp.status_code in _TRANSIENT_STATUS:
                    last_exc = LayaUnavailable(f"laya {resp.status_code} on {path}")
                    logger.warning("laya %s %s -> %d (attempt %d/%d)",
                                   method, path, resp.status_code, attempt + 1, attempts)
                elif resp.is_success:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise LayaInvalidResponse(f"laya non-JSON body on {path}") from exc
                else:
                    # 4xx (auth, bad request, payload too large) — not retryable.
                    raise LayaInvalidResponse(
                        f"laya {resp.status_code} on {path}: {resp.text[:200]}"
                    )

            # transient: back off before the next attempt (skip after the last)
            if attempt + 1 < attempts and self._config.backoff:
                await asyncio.sleep(self._config.backoff * (attempt + 1))

        raise LayaUnavailable(f"laya unreachable after {attempts} attempt(s) on {path}") from last_exc

    async def health(self) -> Dict[str, Any]:
        """GET /health. Raises LayaUnavailable if the sidecar can't be reached."""
        return await self._request("GET", "/health")

    async def systemone(
        self,
        state: Any,
        questions: Dict[str, Any],
        **options: Any,
    ) -> Dict[str, Any]:
        """POST /v1/systemone — one decision pass.

        Not wired into any Odysseus request path yet (M1 is connectivity only);
        the decision service added in M2 builds on this.
        """
        body: Dict[str, Any] = {"state": state, "questions": questions}
        body.update({k: v for k, v in options.items() if v is not None})
        return await self._request("POST", "/v1/systemone", json=body)
