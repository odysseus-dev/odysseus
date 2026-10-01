from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from routes.agent_routes import AgentHttp  # noqa: E402
from services.agents.contracts import AutomationExecutionRef  # noqa: E402
from services.agents.projection import ProjectionReconciler, ProjectionStore, SourceEvent  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.resumed: list[str] = []
        self.confirmed: list[dict] = []
        self.events: list[dict] = []

    def create_or_resume(self, **kwargs):
        conversation_id = kwargs.get("conversation_id") or "conv-1"
        return SimpleNamespace(conversation_id=conversation_id, execution_id=f"agent-server:{conversation_id}")

    def cancel_execution(self, execution_id: str):
        self.cancelled.append(execution_id)
        return {"ok": True}

    def conversation_events(self, conversation_id: str):
        return list(self.events)

    def respond_to_confirmation(self, conversation_id: str, *, accept: bool, reason: str = ""):
        self.confirmed.append(
            {"conversation_id": conversation_id, "accept": accept, "reason": reason}
        )
        return {"ok": True}


class FakeDispatcher:
    def __init__(self) -> None:
        self.client = FakeClient()
        self._refs: dict[str, AutomationExecutionRef] = {}

    def dispatch(self, request=None, **kwargs):
        request_id = kwargs.get("request_id") or getattr(request, "request_id", "o1")
        conversation_id = kwargs.get("conversation_id") or getattr(request, "conversation_id", None) or "conv-1"
        if request_id in self._refs:
            return self._refs[request_id]
        resumed = self.client.create_or_resume(conversation_id=conversation_id, message="hi", request_id=request_id, profile_revision=1, archetype_version=1)
        ref = AutomationExecutionRef(resumed.execution_id, resumed.conversation_id)
        self._refs[request_id] = ref
        return ref


@pytest.fixture
def stack():
    dispatcher = FakeDispatcher()
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    api = AgentHttp(dispatcher=dispatcher, reconciler=reconciler, canvas_base="http://canvas.local")
    run = dispatcher.dispatch(request_id="o1", archetype="chat", payload={"text": "hi"}, conversation_id="conv-1")
    return api, run, dispatcher


def test_cancel_targets_execution_not_conversation(stack):
    client, run, dispatcher = stack
    response = client.post(f"/api/agents/executions/{run.automation_execution_id}/cancel")
    assert response.status_code == 202
    assert run.conversation_id not in response.get_json().get("cancelled_conversations", [])
    assert dispatcher.client.cancelled == [run.automation_execution_id]


def test_create_is_idempotent_on_request_id(stack):
    client, run, _ = stack
    first = client.post("/api/agents/executions", json={"request_id": "o1", "archetype": "chat", "payload": {"text": "hi"}})
    second = client.post("/api/agents/executions", json={"request_id": "o1", "archetype": "chat", "payload": {"text": "hi"}})
    assert first.status_code == 200
    assert first.get_json()["execution_id"] == second.get_json()["execution_id"]


def test_status_exposes_derived_projection_flags(stack):
    client, run, _ = stack
    client.reconciler.reconcile([
        SourceEvent(
            source_system="agent-server",
            source_event_id="mystery",
            kind="UnknownKind",
            conversation_id=run.conversation_id,
            execution_id=run.automation_execution_id,
            parent_id=None,
            source_order=1,
            payload={},
        )
    ])
    response = client.get(f"/api/agents/executions/{run.automation_execution_id}")
    body = response.get_json()
    assert body["degraded"] is True
    assert body["status"] in {"synchronizing", "unknown", "running", "degraded"}
    assert "canvas_url" in body
    assert run.conversation_id in body["canvas_url"]


def test_missing_owner_is_unauthorized(stack):
    client, run, _ = stack
    response = client.post(
        f"/api/agents/executions/{run.automation_execution_id}/cancel",
        owner=None,
    )
    assert response.status_code == 401


def test_approve_without_pending_confirmation_is_conflict(stack):
    client, run, dispatcher = stack
    response = client.post(f"/api/agents/executions/{run.automation_execution_id}/approve")
    assert response.status_code == 409
    assert "error" in response.get_json()
    assert dispatcher.client.confirmed == []


def test_approve_responds_to_pending_action_event(stack):
    client, run, dispatcher = stack
    dispatcher.client.events = [
        {
            "id": "action-1",
            "kind": "ActionEvent",
            "tool_name": "mail.send",
            "action": {"to": ["a@example.test"], "body": "x"},
        }
    ]
    response = client.post(f"/api/agents/executions/{run.automation_execution_id}/approve")
    assert response.status_code == 202
    body = response.get_json()
    assert body["approved"] is True
    assert body["event_id"] == "action-1"
    assert dispatcher.client.confirmed == [
        {"conversation_id": run.conversation_id, "accept": True, "reason": ""}
    ]
