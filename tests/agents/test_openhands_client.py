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
        if path.startswith("/api/conversations") and method == "POST" and path.endswith("/events"):
            return {"ok": True, "event_id": "evt-1"}
        if path == "/api/conversations" and method == "POST":
            return {"id": "conv-new"}
        if "/events" in path and method == "GET":
            return {"items": [{"id": "evt-1", "kind": "MessageEvent"}]}
        if method == "POST" and path.endswith("/stop"):
            return {"ok": True}
        if method == "GET" and "/conversations/" in path:
            return {"id": "conv-1", "status": "running"}
        raise AssertionError(f"unexpected {method} {path}")


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
