"""Fail-closed PDV provider authorization preflight for integrated deployments."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


_last_authorization: ContextVar[dict | None] = ContextVar("pdv_last_provider_authorization", default=None)


def get_last_authorization_receipt() -> dict | None:
    receipt = _last_authorization.get()
    return dict(receipt) if receipt is not None else None


def record_provider_outcome_sync(
    authorization: dict,
    outcome: str,
    duration_ms: int,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_microusd: int | None = None,
) -> dict | None:
    if not required():
        return None
    if not isinstance(authorization, dict):
        raise RuntimeError("PDV provider outcome requires an authorization receipt")
    base, key = _boundary()
    payload = {
        "authorization_receipt_id": authorization.get("authorization_receipt_id"),
        "provider_request_id": authorization.get("provider_request_id"),
        "outcome": outcome,
        "duration_ms": max(0, int(duration_ms)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_microusd": cost_microusd,
    }
    response = httpx.post(
        f"{base}/v1/integrations/odysseus/provider/outcome",
        headers={"X-PDV-Odysseus-Key": key},
        json=payload,
        timeout=3.0,
    )
    try:
        receipt = response.json()
    except ValueError as error:
        raise RuntimeError("PDV provider outcome returned malformed JSON") from error
    if (response.status_code != 201 or not isinstance(receipt, dict)
            or receipt.get("authorization_receipt_id") != payload["authorization_receipt_id"]
            or receipt.get("provider_request_id") != payload["provider_request_id"]
            or receipt.get("outcome") != outcome or not receipt.get("outcome_receipt_id")):
        raise RuntimeError("PDV provider outcome receipt validation failed")
    return receipt


async def record_provider_outcome(
    authorization: dict,
    outcome: str,
    duration_ms: int,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_microusd: int | None = None,
) -> dict | None:
    if not required():
        return None
    if not isinstance(authorization, dict):
        raise RuntimeError("PDV provider outcome requires an authorization receipt")
    base, key = _boundary()
    payload = {
        "authorization_receipt_id": authorization.get("authorization_receipt_id"),
        "provider_request_id": authorization.get("provider_request_id"),
        "outcome": outcome,
        "duration_ms": max(0, int(duration_ms)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_microusd": cost_microusd,
    }
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            f"{base}/v1/integrations/odysseus/provider/outcome",
            headers={"X-PDV-Odysseus-Key": key},
            json=payload,
        )
    try:
        receipt = response.json()
    except ValueError as error:
        raise RuntimeError("PDV provider outcome returned malformed JSON") from error
    if (response.status_code != 201 or not isinstance(receipt, dict)
            or receipt.get("authorization_receipt_id") != payload["authorization_receipt_id"]
            or receipt.get("provider_request_id") != payload["provider_request_id"]
            or receipt.get("outcome") != outcome or not receipt.get("outcome_receipt_id")):
        raise RuntimeError("PDV provider outcome receipt validation failed")
    return receipt


def provider_usage(payload: object) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict):
        return None, None
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_value = usage.get("prompt_tokens", usage.get("input_tokens", payload.get("prompt_eval_count")))
    output_value = usage.get("completion_tokens", usage.get("output_tokens", payload.get("eval_count")))
    safe = lambda value: value if isinstance(value, int) and value >= 0 else None
    return safe(input_value), safe(output_value)


def required() -> bool:
    return os.environ.get("PDV_PROVIDER_GUARD_REQUIRED", "false").lower() == "true"


def _boundary() -> tuple[str, str]:
    base = os.environ.get("PDV_EXECUTION_OS_URL", "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        raise RuntimeError("PDV provider guard requires an explicit loopback Execution OS URL")
    key_path = Path(os.environ.get("ODYSSEUS_PDV_ADAPTER_KEY_FILE", "").strip())
    try:
        key = key_path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise RuntimeError("PDV provider guard credential reference is unavailable") from error
    if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
        raise RuntimeError("PDV provider guard credential format is invalid")
    return base, key


def _validate(response: httpx.Response, endpoint: str, model: str, provider_request_id: str) -> dict:
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError("PDV provider authorization returned malformed JSON") from error
    if response.status_code != 200 or not isinstance(payload, dict) or payload.get("allowed") is not True:
        reason = payload.get("reason_code") if isinstance(payload, dict) else "UNAVAILABLE"
        raise RuntimeError(f"PDV provider authorization denied route ({reason or 'UNAVAILABLE'})")
    if (payload.get("selected_model") != model or payload.get("selected_endpoint") != endpoint
            or payload.get("provider_request_id") != provider_request_id or not payload.get("authorization_receipt_id")):
        raise RuntimeError("PDV provider authorization correlation mismatch")
    return payload


def authorize_provider_sync(endpoint: str, model: str) -> dict | None:
    _last_authorization.set(None)
    if not required():
        return None
    base, key = _boundary()
    provider_request_id = str(uuid4())
    response = httpx.post(
        f"{base}/v1/integrations/odysseus/provider/authorize",
        headers={"X-PDV-Odysseus-Key": key},
        json={"endpoint": endpoint, "model": model, "provider_request_id": provider_request_id},
        timeout=3.0,
    )
    payload = _validate(response, endpoint, model, provider_request_id)
    _last_authorization.set(payload)
    return payload


async def authorize_provider(endpoint: str, model: str) -> dict | None:
    _last_authorization.set(None)
    if not required():
        return None
    base, key = _boundary()
    provider_request_id = str(uuid4())
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            f"{base}/v1/integrations/odysseus/provider/authorize",
            headers={"X-PDV-Odysseus-Key": key},
            json={"endpoint": endpoint, "model": model, "provider_request_id": provider_request_id},
        )
    payload = _validate(response, endpoint, model, provider_request_id)
    _last_authorization.set(payload)
    return payload
