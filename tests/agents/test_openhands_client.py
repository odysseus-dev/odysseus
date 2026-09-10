from __future__ import annotations

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.openhands_client import (  # noqa: E402
    OpenHandsClient,
    OpenHandsFailure,
    Transport,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path, body or {}))
        if path == "/api/settings" and method == "GET":
            return {"agent_settings": {"agent_kind": "openhands", "agent": "CodeActAgent"}}
        if path.startswith("/api/conversations") and method == "POST" and path.endswith("/events"):
            return {"ok": True, "event_id": "evt-1"}
        if path == "/api/conversations" and method == "POST":
            return {"id": "conv-new"}
        if path.endswith("/events/search") and method == "GET":
            return {"items": [{"id": "evt-1", "kind": "MessageEvent"}]}
        if "/events" in path and method == "GET":
            return {"items": [{"id": "evt-1", "kind": "MessageEvent"}]}
        if method == "POST" and path.endswith("/interrupt"):
            return {"ok": True}
        if method == "GET" and "/conversations/" in path:
            return {"id": "conv-1", "status": "running"}
        raise AssertionError(f"unexpected {method} {path}")


def test_create_sends_workspace_and_initial_message():
    transport = RecordingTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    result = client.create_or_resume(
        conversation_id=None,
        message="hi",
        request_id="o1",
        profile_revision=1,
        archetype_version=1,
    )
    assert result.conversation_id == "conv-new"
    assert transport.calls[0][1] == "/api/settings"
    method, path, body = transport.calls[1]
    assert method == "POST"
    assert path == "/api/conversations"
    assert body["workspace"] == {"working_dir": "/workspace", "kind": "LocalWorkspace"}
    assert body["agent_settings"]["agent_kind"] == "openhands"
    assert body["initial_message"]["content"][0]["text"] == "hi"
    assert body["autotitle"] is False
    assert "credential_delivery_mode" not in body
    assert "request_id" not in body


def test_create_sends_opencode_profile_id():
    transport = RecordingTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    client.create_or_resume(
        conversation_id=None,
        message="hi",
        request_id="o1",
        profile_revision=1,
        archetype_version=1,
        agent_profile_id="opencode",
    )
    body = transport.calls[1][2]
    assert body["agent_profile_id"] == "opencode"
    assert body["autotitle"] is False


def test_create_or_resume_uses_agent_server_conversation_api():
    transport = RecordingTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    result = client.create_or_resume(
        conversation_id="conv-1",
        message="hi",
        request_id="o1",
        profile_revision=1,
        archetype_version=1,
        workspace_grants=("ws:1",),
        credential_delivery_mode="broker",
    )
    assert result.conversation_id == "conv-1"
    assert transport.calls
    assert all("/v1/" not in path for _, path, _ in transport.calls)
    assert any(path.startswith("/api/conversations/conv-1") for _, path, _ in transport.calls)
    method, path, body = transport.calls[0]
    assert path.endswith("/events")
    assert body["run"] is True
    assert body["content"][0]["text"] == "hi"


def test_cancel_interrupts_conversation():
    transport = RecordingTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    client.cancel_execution("agent-server:conv-1")
    assert transport.calls[0][1] == "/api/conversations/conv-1/interrupt"


def test_failures_preserve_source_owner():
    class Boom(Transport):
        def request(self, method: str, path: str, body: dict | None = None) -> dict:
            raise OpenHandsFailure("agent-server", "disconnected")

    client = OpenHandsClient(transport=Boom(), agent_server_base="http://agent-server")
    with pytest.raises(OpenHandsFailure) as excinfo:
        client.get_execution("conv-1")
    assert excinfo.value.source == "agent-server"


def test_client_talks_only_through_transport():
    transport = RecordingTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    client.conversation_events("conv-1")
    client.cancel_execution("conv-1")
    assert transport.calls
