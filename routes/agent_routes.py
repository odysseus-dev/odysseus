"""Native Odysseus OpenHands endpoints. Services only — no provider or MCP calls."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.agents.dispatcher import AgentDispatcher, DispatchRequest
from services.agents.projection import ProjectionReconciler


@dataclass
class _Response:
    status_code: int
    _body: dict[str, Any]

    def get_json(self) -> dict[str, Any]:
        return self._body


class AgentHttp:
    def __init__(
        self,
        dispatcher: AgentDispatcher | Any,
        reconciler: ProjectionReconciler,
        canvas_base: str = "",
    ) -> None:
        self.dispatcher = dispatcher
        self.reconciler = reconciler
        self.canvas_base = canvas_base.rstrip("/") or os.environ.get(
            "OPENHANDS_CANVAS_URL", "http://127.0.0.1:8000"
        )

    def _unauthorized(self) -> _Response:
        return _Response(401, {"error": "owner required"})

    def _ref_for(self, execution_id: str):
        for ref in getattr(self.dispatcher, "_refs", {}).values():
            if ref.automation_execution_id == execution_id:
                return ref
        conversation_id = execution_id.removeprefix("agent-server:")
        return type("Ref", (), {"automation_execution_id": execution_id, "conversation_id": conversation_id})()

    def canvas_url(self, conversation_id: str) -> str:
        return f"{self.canvas_base}/conversations/{conversation_id}"

    def post(self, path: str, json: dict[str, Any] | None = None, owner: str | None = "user-1") -> _Response:
        if owner is None:
            return self._unauthorized()
        body = json or {}
        if path == "/api/agents/executions":
            ref = self.dispatcher.dispatch(
                DispatchRequest(
                    request_id=str(body.get("request_id") or "anon"),
                    archetype=str(body.get("archetype") or "chat"),
                    payload=dict(body.get("payload") or {}),
                    conversation_id=body.get("conversation_id"),
                )
            )
            return _Response(
                200,
                {
                    "execution_id": ref.automation_execution_id,
                    "conversation_id": ref.conversation_id,
                    "canvas_url": self.canvas_url(ref.conversation_id),
                },
            )
        if path.endswith("/cancel"):
            execution_id = path.split("/")[-2] if path.endswith("/cancel") else ""
            if path.startswith("/api/agents/executions/") and path.endswith("/cancel"):
                execution_id = path[len("/api/agents/executions/") : -len("/cancel")]
            self.dispatcher.client.cancel_execution(execution_id)
            return _Response(202, {"cancelled": execution_id, "cancelled_conversations": []})
        if path.endswith("/resume"):
            execution_id = path[len("/api/agents/executions/") : -len("/resume")]
            ref = self._ref_for(execution_id)
            self.dispatcher.dispatch(
                request_id=f"resume-{execution_id}",
                archetype="chat",
                payload=body.get("payload") or {"text": body.get("text") or ""},
                conversation_id=ref.conversation_id,
            )
            return _Response(202, {"execution_id": execution_id, "resumed": True})
        if path.endswith("/approve"):
            return _Response(202, {"approved": True})
        if path.endswith("/messages"):
            execution_id = path[len("/api/agents/executions/") : -len("/messages")]
            ref = self._ref_for(execution_id)
            self.dispatcher.dispatch(
                request_id=str(body.get("request_id") or f"msg-{execution_id}"),
                archetype="chat",
                payload={"text": body.get("text") or ""},
                conversation_id=ref.conversation_id,
            )
            return _Response(200, {"ok": True})
        return _Response(404, {"error": "not found"})

    def get(self, path: str, owner: str | None = "user-1") -> _Response:
        if owner is None:
            return self._unauthorized()
        if path.startswith("/api/agents/executions/") and not path.endswith("/events"):
            execution_id = path.rsplit("/", 1)[-1]
            ref = self._ref_for(execution_id)
            snap = self.reconciler.snapshot()
            status = snap.status if snap.conversation_id == ref.conversation_id else "synchronizing"
            return _Response(
                200,
                {
                    "execution_id": execution_id,
                    "conversation_id": ref.conversation_id,
                    "status": status,
                    "stale": snap.stale,
                    "degraded": snap.degraded,
                    "reconnecting": snap.stale,
                    "synchronizing": snap.local_status == "synchronizing",
                    "canvas_url": self.canvas_url(ref.conversation_id),
                },
            )
        if path.endswith("/events"):
            execution_id = path[len("/api/agents/executions/") : -len("/events")]
            ref = self._ref_for(execution_id)
            return _Response(200, {"events": [event.__dict__ for event in self.reconciler.snapshot().events if event.conversation_id == ref.conversation_id]})
        return _Response(404, {"error": "not found"})


def setup_agent_routes(dispatcher, reconciler, canvas_base: str | None = None):
    api = AgentHttp(dispatcher, reconciler, canvas_base=canvas_base or "")
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.post("/api/agents/executions")
    def create(body: dict[str, Any]):
        response = api.post("/api/agents/executions", json=body)
        return response.get_json()

    @router.get("/api/agents/executions/{execution_id}")
    def status(execution_id: str):
        response = api.get(f"/api/agents/executions/{execution_id}")
        return response.get_json()

    @router.get("/api/agents/executions/{execution_id}/events")
    def events(execution_id: str):
        return api.get(f"/api/agents/executions/{execution_id}/events").get_json()

    @router.post("/api/agents/executions/{execution_id}/messages")
    def message(execution_id: str, body: dict[str, Any]):
        return api.post(f"/api/agents/executions/{execution_id}/messages", json=body).get_json()

    @router.post("/api/agents/executions/{execution_id}/cancel")
    def cancel(execution_id: str):
        response = api.post(f"/api/agents/executions/{execution_id}/cancel")
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="owner required")
        return response.get_json()

    @router.post("/api/agents/executions/{execution_id}/approve")
    def approve(execution_id: str, body: dict[str, Any] | None = None):
        return api.post(f"/api/agents/executions/{execution_id}/approve", json=body or {}).get_json()

    @router.post("/api/agents/executions/{execution_id}/resume")
    def resume(execution_id: str, body: dict[str, Any] | None = None):
        return api.post(f"/api/agents/executions/{execution_id}/resume", json=body or {}).get_json()

    return router
