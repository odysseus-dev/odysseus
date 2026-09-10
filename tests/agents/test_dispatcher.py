from __future__ import annotations

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.contracts import AutomationExecutionRef  # noqa: E402
from services.agents.dispatcher import AgentDispatcher, DispatchRequest  # noqa: E402
from services.agents.openhands_client import OpenHandsClient  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.agent_server_calls: list[dict] = []
        self.automation_calls: list[dict] = []

    def create_or_resume(self, **kwargs):
        self.agent_server_calls.append(kwargs)
        conversation_id = kwargs.get("conversation_id") or "conv-created"
        return type("Resume", (), {"conversation_id": conversation_id, "execution_id": f"agent-server:{conversation_id}"})()

    def cancel_execution(self, execution_id: str):
        self.agent_server_calls.append({"cancel": execution_id})

    def get_execution(self, execution_id: str):
        return {"id": execution_id}

    def conversation_events(self, conversation_id: str):
        return []


def test_dispatch_is_idempotent():
    dispatcher = AgentDispatcher(client=FakeClient())
    first = dispatcher.dispatch(request_id="o1", archetype="chat", payload={"text": "hi"})
    second = dispatcher.dispatch(request_id="o1", archetype="chat", payload={"text": "hi"})
    assert second == first
    assert isinstance(first, AutomationExecutionRef)
    assert dispatcher.client.agent_server_calls  # type: ignore[attr-defined]
    assert dispatcher.client.agent_server_calls[0]["request_id"] == "o1"  # type: ignore[index]


def test_interactive_chat_uses_agent_server_continued_run():
    client = FakeClient()
    dispatcher = AgentDispatcher(client=client)
    ref = dispatcher.dispatch(
        DispatchRequest(request_id="o2", archetype="chat", payload={"text": "hi"}, conversation_id="conv-keep")
    )
    assert ref.conversation_id == "conv-keep"
    assert client.automation_calls == []
    assert client.agent_server_calls[0]["credential_delivery_mode"] == "broker"
    assert client.agent_server_calls[0]["profile_revision"] == 1
    assert client.agent_server_calls[0]["archetype_version"] == 1


def test_dispatcher_has_no_raw_http():
    import services.agents.dispatcher as module

    source = open(module.__file__, encoding="utf-8").read()
    assert "urllib" not in source
    assert "requests" not in source
    assert "httpx" not in source


def test_dispatch_normalizes_hermes_to_odysseus():
    client = FakeClient()
    dispatcher = AgentDispatcher(client=client)
    dispatcher.dispatch(
        request_id="turn-hermes",
        archetype="chat",
        payload={"text": "hi"},
        agent_profile_id="hermes",
    )
    assert client.agent_server_calls[0]["agent_profile_id"] == "odysseus"

    client.agent_server_calls.clear()
    dispatcher.dispatch(
        DispatchRequest(
            request_id="turn-hermes-2",
            archetype="chat",
            payload={"text": "hi"},
            agent_profile_id="hermes",
        )
    )
    assert client.agent_server_calls[0]["agent_profile_id"] == "odysseus"


class _CreateBodyTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path, body or {}))
        if path == "/api/settings" and method == "GET":
            return {"agent_settings": {"agent_kind": "openhands", "agent": "CodeActAgent"}}
        if path == "/api/conversations" and method == "POST":
            return {"id": "conv-new"}
        raise AssertionError(f"unexpected {method} {path}")


def test_dispatch_hermes_not_sent_in_openhands_create_body():
    transport = _CreateBodyTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    dispatcher = AgentDispatcher(client=client)
    dispatcher.dispatch(
        request_id="turn-hermes-body",
        archetype="chat",
        payload={"text": "hi"},
        agent_profile_id="hermes",
    )
    body = transport.calls[1][2]
    assert "agent_profile_id" not in body


def test_dispatch_forwards_agent_profile_id():
    client = FakeClient()
    dispatcher = AgentDispatcher(client=client)
    dispatcher.dispatch(
        DispatchRequest(
            request_id="turn-2",
            archetype="chat",
            payload={"text": "hi"},
            conversation_id="conv-keep",
            agent_profile_id="opencode",
        )
    )
    assert client.agent_server_calls[0]["agent_profile_id"] == "opencode"


def test_dispatch_injects_workspace_grants_and_request_id():
    client = FakeClient()
    dispatcher = AgentDispatcher(client=client)
    dispatcher.dispatch(
        request_id="o3",
        archetype="deep-research",
        payload={"query": "q"},
        workspace_grants=("research",),
    )
    call = client.agent_server_calls[0]
    assert call["workspace_grants"] == ("research",)
    assert call["request_id"] == "o3"
