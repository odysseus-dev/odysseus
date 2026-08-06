"""Fail-closed PDV provider authorization preflight for integrated deployments."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from threading import Lock

import httpx


_last_authorization: ContextVar[dict | None] = ContextVar("pdv_last_provider_authorization", default=None)
_last_routing: ContextVar[dict | None] = ContextVar("pdv_last_provider_routing", default=None)
_last_outcome: ContextVar[dict | None] = ContextVar("pdv_last_provider_outcome", default=None)
_runtime_lock = Lock()
_runtime_observation: dict = {}


def _observe_runtime(**values) -> None:
    with _runtime_lock:
        _runtime_observation.update(values)


def get_provider_runtime_observation() -> dict:
    with _runtime_lock:
        return dict(_runtime_observation)


def get_last_authorization_receipt() -> dict | None:
    receipt = _last_authorization.get()
    return dict(receipt) if receipt is not None else None


def get_last_routing_receipt() -> dict | None:
    receipt = _last_routing.get()
    return dict(receipt) if receipt is not None else None


def get_last_outcome_receipt() -> dict | None:
    receipt = _last_outcome.get()
    return dict(receipt) if receipt is not None else None


def _dispatch_id() -> str | None:
    value = os.environ.get("PDV_EXECUTION_OS_DISPATCH_ID", "").strip()
    return value if value.startswith("ody_dispatch_") and len(value) == 49 else None


def _dispatch_transition_sync(base: str, key: str, state: str, authorization: dict, final_receipt_id: str | None = None) -> None:
    dispatch_id = _dispatch_id()
    if not dispatch_id:
        return
    payload = {"state": state, "provider_request_id": authorization.get("provider_request_id"), "final_receipt_id": final_receipt_id}
    response = httpx.post(f"{base}/v1/integrations/odysseus/dispatch/{dispatch_id}/events", headers={"X-PDV-Odysseus-Key": key}, json=payload, timeout=3.0)
    if response.status_code != 200:
        raise RuntimeError("PDV dispatch correlation transition failed")


async def _dispatch_transition(base: str, key: str, state: str, authorization: dict, final_receipt_id: str | None = None) -> None:
    dispatch_id = _dispatch_id()
    if not dispatch_id:
        return
    payload = {"state": state, "provider_request_id": authorization.get("provider_request_id"), "final_receipt_id": final_receipt_id}
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(f"{base}/v1/integrations/odysseus/dispatch/{dispatch_id}/events", headers={"X-PDV-Odysseus-Key": key}, json=payload)
    if response.status_code != 200:
        raise RuntimeError("PDV dispatch correlation transition failed")


def get_ranked_route_policy(endpoint: str, model: str) -> dict | None:
    receipt = _last_routing.get()
    if not isinstance(receipt, dict):
        return None
    for item in receipt.get("ordered_candidates", []):
        if isinstance(item, dict) and item.get("endpoint") == endpoint and item.get("model") == model:
            return dict(item)
    return None


def record_provider_outcome_sync(
    authorization: dict,
    outcome: str,
    duration_ms: int,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_microusd: int | None = None,
) -> dict | None:
    _last_outcome.set(None)
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
    _last_outcome.set(receipt)
    _dispatch_transition_sync(base, key, outcome, authorization, receipt["outcome_receipt_id"])
    _observe_runtime(currentRunStatus="SUCCEEDED" if outcome == "completed" else "BLOCKED" if outcome == "cancelled" else "FAILED", failureMessage=None if outcome == "completed" else outcome)
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
    _last_outcome.set(None)
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
    _last_outcome.set(receipt)
    await _dispatch_transition(base, key, outcome, authorization, receipt["outcome_receipt_id"])
    _observe_runtime(currentRunStatus="SUCCEEDED" if outcome == "completed" else "BLOCKED" if outcome == "cancelled" else "FAILED", failureMessage=None if outcome == "completed" else outcome)
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


def _validate(response: httpx.Response, endpoint: str, model: str, provider_request_id: str, routing_receipt_id: str, candidate_index: int) -> dict:
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError("PDV provider authorization returned malformed JSON") from error
    if response.status_code != 200 or not isinstance(payload, dict) or payload.get("allowed") is not True:
        reason = payload.get("reason_code") if isinstance(payload, dict) else "UNAVAILABLE"
        _observe_runtime(model=model, currentRunStatus="BLOCKED", taskCorrelationId=provider_request_id, failureMessage=str(reason or "UNAVAILABLE"))
        raise RuntimeError(f"PDV provider authorization denied route ({reason or 'UNAVAILABLE'})")
    if (payload.get("selected_model") != model or payload.get("selected_endpoint") != endpoint
            or payload.get("provider_request_id") != provider_request_id or payload.get("routing_receipt_id") != routing_receipt_id
            or payload.get("candidate_index") != candidate_index or not payload.get("authorization_receipt_id")):
        raise RuntimeError("PDV provider authorization correlation mismatch")
    return payload


def _provider_target(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path += "/chat/completions"
    return parsed._replace(path=path).geturl()


def _routing_candidate(receipt: dict | None, endpoint: str, model: str) -> dict | None:
    for item in receipt.get("ordered_candidates", []) if isinstance(receipt, dict) else []:
        if isinstance(item, dict) and _provider_target(str(item.get("endpoint", ""))) == _provider_target(endpoint) and item.get("model") == model:
            return item
    return None


def _validate_ranking(response: httpx.Response, candidates: list, task_class: str) -> tuple[list, dict]:
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError("PDV provider ranking returned malformed JSON") from error
    ordered = payload.get("ordered_candidates") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or response.status_code != 200 or payload.get("allowed") is not True
            or payload.get("task_class") != task_class or not payload.get("routing_receipt_id")
            or not isinstance(ordered, list) or not ordered or len(ordered) > len(candidates)):
        reason = payload.get("reason_code") if isinstance(payload, dict) else "UNAVAILABLE"
        raise RuntimeError(f"PDV provider ranking denied candidates ({reason or 'UNAVAILABLE'})")
    seen = set()
    ranked = []
    for item in ordered:
        index = item.get("candidate_index") if isinstance(item, dict) else None
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(candidates) or index in seen:
            raise RuntimeError("PDV provider ranking correlation mismatch")
        candidate = candidates[index]
        if item.get("endpoint") != candidate[0] or item.get("model") != candidate[1]:
            raise RuntimeError("PDV provider ranking correlation mismatch")
        timeout_ms = item.get("timeout_ms")
        retry_limit = item.get("retry_limit")
        if (not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms < 1
                or not isinstance(retry_limit, int) or isinstance(retry_limit, bool) or retry_limit < 0):
            raise RuntimeError("PDV provider ranking policy is invalid")
        seen.add(index)
        ranked.append(candidate)
    if payload.get("selected_endpoint") != ranked[0][0] or payload.get("selected_model") != ranked[0][1]:
        raise RuntimeError("PDV provider ranking correlation mismatch")
    return ranked, payload


def rank_provider_candidates_sync(candidates: list, task_class: str = "chat") -> list:
    _last_routing.set(None)
    if not required():
        return list(candidates)
    base, key = _boundary()
    response = httpx.post(
        f"{base}/v1/integrations/odysseus/provider/rank",
        headers={"X-PDV-Odysseus-Key": key},
        json={"task_class": task_class, "candidates": [{"endpoint": item[0], "model": item[1]} for item in candidates]},
        timeout=3.0,
    )
    ranked, receipt = _validate_ranking(response, candidates, task_class)
    _last_routing.set(receipt)
    _observe_runtime(provider=receipt.get("selected_provider"), model=receipt.get("selected_model"), currentRunStatus="IDLE", taskCorrelationId=None, failureMessage=None)
    return ranked


async def rank_provider_candidates(candidates: list, task_class: str = "chat") -> list:
    _last_routing.set(None)
    if not required():
        return list(candidates)
    base, key = _boundary()
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            f"{base}/v1/integrations/odysseus/provider/rank",
            headers={"X-PDV-Odysseus-Key": key},
            json={"task_class": task_class, "candidates": [{"endpoint": item[0], "model": item[1]} for item in candidates]},
        )
    ranked, receipt = _validate_ranking(response, candidates, task_class)
    _last_routing.set(receipt)
    _observe_runtime(provider=receipt.get("selected_provider"), model=receipt.get("selected_model"), currentRunStatus="IDLE", taskCorrelationId=None, failureMessage=None)
    return ranked


def authorize_provider_sync(endpoint: str, model: str) -> dict | None:
    _last_authorization.set(None)
    if not required():
        return None
    base, key = _boundary()
    routing = _last_routing.get()
    candidate = _routing_candidate(routing, endpoint, model)
    if candidate is None:
        rank_provider_candidates_sync([(endpoint, model, {})])
        routing = _last_routing.get()
        candidate = _routing_candidate(routing, endpoint, model)
    if candidate is None:
        raise RuntimeError("PDV provider authorization lacks ranked candidate correlation")
    provider_request_id = str(uuid4())
    response = httpx.post(
        f"{base}/v1/integrations/odysseus/provider/authorize",
        headers={"X-PDV-Odysseus-Key": key},
        json={"endpoint": endpoint, "model": model, "provider_request_id": provider_request_id, "routing_receipt_id": routing.get("routing_receipt_id"), "candidate_index": candidate.get("candidate_index")},
        timeout=3.0,
    )
    payload = _validate(response, endpoint, model, provider_request_id, routing.get("routing_receipt_id"), candidate.get("candidate_index"))
    _dispatch_transition_sync(base, key, "running", payload)
    _last_authorization.set(payload)
    _observe_runtime(provider=payload.get("selected_provider"), model=model, currentRunStatus="RUNNING", taskCorrelationId=provider_request_id, failureMessage=None)
    return payload


async def authorize_provider(endpoint: str, model: str) -> dict | None:
    _last_authorization.set(None)
    if not required():
        return None
    base, key = _boundary()
    routing = _last_routing.get()
    candidate = _routing_candidate(routing, endpoint, model)
    if candidate is None:
        await rank_provider_candidates([(endpoint, model, {})])
        routing = _last_routing.get()
        candidate = _routing_candidate(routing, endpoint, model)
    if candidate is None:
        raise RuntimeError("PDV provider authorization lacks ranked candidate correlation")
    provider_request_id = str(uuid4())
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            f"{base}/v1/integrations/odysseus/provider/authorize",
            headers={"X-PDV-Odysseus-Key": key},
            json={"endpoint": endpoint, "model": model, "provider_request_id": provider_request_id, "routing_receipt_id": routing.get("routing_receipt_id"), "candidate_index": candidate.get("candidate_index")},
        )
    payload = _validate(response, endpoint, model, provider_request_id, routing.get("routing_receipt_id"), candidate.get("candidate_index"))
    await _dispatch_transition(base, key, "running", payload)
    _last_authorization.set(payload)
    _observe_runtime(provider=payload.get("selected_provider"), model=model, currentRunStatus="RUNNING", taskCorrelationId=provider_request_id, failureMessage=None)
    return payload
