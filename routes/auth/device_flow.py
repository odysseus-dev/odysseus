"""Shared OAuth/device-flow route scaffolding for provider setup."""

from __future__ import annotations

import inspect
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

from fastapi import APIRouter, Form, HTTPException, Request

from core.middleware import require_admin


@dataclass(frozen=True)
class DeviceFlowStart:
    """Provider-specific start result consumed by the shared route wrapper."""

    pending: Mapping[str, Any]
    response: Mapping[str, Any]
    interval: int = 5
    expires_in: int = 900


@dataclass(frozen=True)
class DeviceFlowPoll:
    """Normalized provider poll outcome."""

    status: str
    endpoint: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None
    detail: Optional[str] = None
    interval: Optional[int] = None

    @classmethod
    def pending(cls, detail: Optional[str] = None) -> "DeviceFlowPoll":
        return cls(status="pending", detail=detail)

    @classmethod
    def slow_down(cls, interval: Optional[int] = None, detail: Optional[str] = None) -> "DeviceFlowPoll":
        return cls(status="slow_down", interval=interval, detail=detail)

    @classmethod
    def authorized(cls, endpoint: Mapping[str, Any]) -> "DeviceFlowPoll":
        return cls(status="authorized", endpoint=endpoint)

    @classmethod
    def failed(cls, error: str) -> "DeviceFlowPoll":
        return cls(status="failed", error=error)


class PendingDeviceFlowStore:
    """Thread-safe in-memory pending device-flow store.

    Device codes and provider-side secrets stay inside this process. Each entry
    stores provider payload separately from poll metadata so provider callbacks
    only receive the fields they created.
    """

    def __init__(self, *, time_func: Callable[[], float] = time.time):
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._time = time_func

    def _now(self) -> float:
        return float(self._time())

    def prune_expired(self) -> None:
        now = self._now()
        with self._lock:
            for key in [k for k, v in self._pending.items() if v.get("expires_at", 0) < now]:
                self._pending.pop(key, None)

    def add(self, payload: Mapping[str, Any], *, interval: int, expires_in: int) -> str:
        self.prune_expired()
        poll_id = uuid.uuid4().hex
        with self._lock:
            self._pending[poll_id] = {
                "payload": dict(payload),
                "interval": max(int(interval or 5), 1),
                "expires_at": self._now() + max(int(expires_in or 900), 1),
                "next_poll_at": 0.0,
            }
        return poll_id

    def get_payload(self, poll_id: str) -> Optional[dict[str, Any]]:
        self.prune_expired()
        with self._lock:
            entry = self._pending.get(poll_id)
            if entry is None:
                return None
            return dict(entry.get("payload") or {})

    def is_throttled(self, poll_id: str) -> bool:
        with self._lock:
            entry = self._pending.get(poll_id)
            return bool(entry and self._now() < float(entry.get("next_poll_at") or 0))

    def schedule_next(self, poll_id: str) -> None:
        now = self._now()
        with self._lock:
            entry = self._pending.get(poll_id)
            if entry is not None:
                entry["next_poll_at"] = now + int(entry.get("interval") or 5)

    def slow_down(self, poll_id: str, interval: Optional[int] = None) -> None:
        now = self._now()
        with self._lock:
            entry = self._pending.get(poll_id)
            if entry is not None:
                new_interval = int(interval or (int(entry.get("interval") or 5) + 5))
                entry["interval"] = max(new_interval, 1)
                entry["next_poll_at"] = now + entry["interval"]

    def pop(self, poll_id: str) -> None:
        with self._lock:
            self._pending.pop(poll_id, None)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _pending_response(detail: Optional[str] = None) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "pending"}
    if detail:
        response["detail"] = detail
    return response


def create_device_flow_router(
    *,
    prefix: str,
    tags: Iterable[str],
    store: PendingDeviceFlowStore,
    start_flow: Callable[[Request, Mapping[str, Any]], DeviceFlowStart],
    poll_flow: Callable[[Request, Mapping[str, Any]], DeviceFlowPoll],
) -> APIRouter:
    """Create standard `/device/start|poll|cancel` routes for a provider."""

    router = APIRouter(prefix=prefix, tags=list(tags))

    @router.post("/device/start")
    async def device_start(request: Request):
        require_admin(request)
        form = await request.form()
        start = await _maybe_await(start_flow(request, form))
        interval = int(start.interval or 5)
        expires_in = int(start.expires_in or 900)
        poll_id = store.add(start.pending, interval=interval, expires_in=expires_in)
        response = dict(start.response)
        response.update({"poll_id": poll_id, "interval": interval, "expires_in": expires_in})
        return response

    @router.post("/device/poll")
    async def device_poll(request: Request, poll_id: str = Form(...)):
        require_admin(request)
        payload = store.get_payload(poll_id)
        if payload is None:
            raise HTTPException(404, "Unknown or expired login session")
        if store.is_throttled(poll_id):
            return {"status": "pending"}

        try:
            outcome = await _maybe_await(poll_flow(request, payload))
        except Exception:
            store.pop(poll_id)
            raise

        if outcome.status == "authorized":
            store.pop(poll_id)
            return {"status": "authorized", "endpoint": dict(outcome.endpoint or {})}
        if outcome.status == "failed":
            store.pop(poll_id)
            return {"status": "failed", "error": outcome.error or "denied"}
        if outcome.status == "slow_down":
            store.slow_down(poll_id, outcome.interval)
            return _pending_response(outcome.detail)

        store.schedule_next(poll_id)
        return _pending_response(outcome.detail)

    @router.post("/device/cancel")
    def device_cancel(request: Request, poll_id: str = Form(...)):
        require_admin(request)
        store.pop(poll_id)
        return {"status": "cancelled"}

    return router
