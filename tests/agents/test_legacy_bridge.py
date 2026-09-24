import asyncio
from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.dispatcher import AgentDispatcher
from services.agents.legacy_bridge import stream_governed_agent


class ScriptedClient:
    def __init__(self, events, polls=None):
        self.events = events
        self.polls = polls
        self._poll_i = 0
        self.calls = []
        self.confirmed = []

    def create_or_resume(self, **kwargs):
        self.calls.append(kwargs)
        cid = kwargs.get("conversation_id") or "conv-new"
        return type("R", (), {"conversation_id": cid, "execution_id": f"agent-server:{cid}"})()

    def conversation_events(self, conversation_id):
        if self.polls is not None:
            idx = min(self._poll_i, len(self.polls) - 1)
            self._poll_i += 1
            return list(self.polls[idx])
        return list(self.events)

    def get_execution(self, execution_id):
        return dict(getattr(self, "execution", {}))

    def respond_to_confirmation(self, conversation_id, *, accept, reason=""):
        self.confirmed.append(
            {"conversation_id": conversation_id, "accept": accept, "reason": reason}
        )
        return {"ok": True}


def _collect(**kwargs):
    return asyncio.run(_alist(kwargs))


async def _alist(kwargs):
    return [chunk async for chunk in stream_governed_agent(**kwargs)]


def test_second_turn_reuses_openhands_id_not_session_id():
    client = ScriptedClient([])
    dispatcher = AgentDispatcher(client=client)
    _collect(
        dispatcher=dispatcher,
        messages=[{"role": "user", "content": "hi"}],
        session_id="ody-session",
        conversation_id="conv-keep",
        turn_id="turn-2",
        agent_profile_id="odysseus",
        bound_agent_profile_id="odysseus",
        poll_timeout_s=0,
    )
    assert client.calls[0]["conversation_id"] == "conv-keep"
    assert client.calls[0]["request_id"] == "turn-2"


def test_stream_emits_execution_delta_and_done():
    client = ScriptedClient(
        [
            {
                "id": "m1",
                "kind": "MessageEvent",
                "source": "agent",
                "content": [{"type": "text", "text": "pong"}],
            },
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "finished"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "ping"}],
        turn_id="t1",
    )
    joined = "".join(chunks)
    assert '"type": "execution"' in joined
    assert '"delta": "pong"' in joined
    assert chunks[-1] == "data: [DONE]\n\n"


def test_profile_change_rebinds_new_conversation():
    client = ScriptedClient([])
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "hi"}],
        conversation_id="conv-old",
        agent_profile_id="opencode",
        bound_agent_profile_id="odysseus",
        turn_id="t3",
        poll_timeout_s=0,
    )
    assert client.calls[0]["conversation_id"] is None
    assert '"rebound": true' in "".join(chunks)


def test_empty_first_poll_then_message_still_yields_delta():
    client = ScriptedClient(
        [],
        polls=[
            [],
            [
                {
                    "id": "m1",
                    "kind": "MessageEvent",
                    "source": "agent",
                    "content": [{"type": "text", "text": "later"}],
                },
                {"id": "s1", "kind": "ConversationStateUpdate", "status": "finished"},
            ],
        ],
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "ping"}],
        turn_id="t-late",
    )
    joined = "".join(chunks)
    assert '"delta": "later"' in joined
    assert chunks[-1] == "data: [DONE]\n\n"


def test_pending_confirmation_emitted_once_per_event_id():
    action = {"id": "a1", "kind": "ActionEvent", "tool_name": "mail.send"}
    client = ScriptedClient(
        [],
        polls=[
            [action],
            [action],
            [action, {"id": "s1", "kind": "ConversationStateUpdate", "status": "paused"}],
        ],
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "send"}],
        turn_id="t-pend",
    )
    joined = "".join(chunks)
    assert joined.count('"type": "pending_confirmation"') == 1
    assert '"event_id": "a1"' in joined


def test_stream_reads_agent_server_message_and_execution_status():
    client = ScriptedClient(
        [
            {
                "id": "m1",
                "kind": "MessageEvent",
                "source": "agent",
                "llm_message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "pong"}],
                },
            },
            {
                "id": "s1",
                "kind": "ConversationStateUpdateEvent",
                "key": "execution_status",
                "value": "finished",
            },
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "ping"}],
        turn_id="t-as145",
        poll_timeout_s=0,
    )
    joined = "".join(chunks)
    assert '"delta": "pong"' in joined
    assert chunks[-1] == "data: [DONE]\n\n"


def test_client_idle_uses_execution_status():
    client = ScriptedClient([], polls=[[]] * 50)
    client.execution = {"execution_status": "finished"}
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "ping"}],
        turn_id="t-exec-idle",
        poll_timeout_s=5,
    )
    assert client._poll_i <= 2
    assert chunks[-1] == "data: [DONE]\n\n"


def test_client_idle_treats_error_as_terminal():
    """9router 401 sets execution_status=error; do not hang on Processing request."""
    client = ScriptedClient([], polls=[[]] * 50)
    client.execution = {"execution_status": "error"}
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "ping"}],
        turn_id="t-exec-error",
        poll_timeout_s=5,
    )
    assert client._poll_i <= 2
    assert chunks[-1] == "data: [DONE]\n\n"
    assert "Agent run failed before completion." in "".join(chunks)


def test_chat_turn_sends_empty_workspace_grants():
    client = ScriptedClient([])
    _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "hi"}],
        turn_id="t-chat-ws",
        user_requested_agent=False,
        workspace_grants=("/Users/me/proj",),
        poll_timeout_s=0,
    )
    assert client.calls[0]["workspace_grants"] == ()


def test_agent_turn_keeps_workspace_grants():
    client = ScriptedClient([])
    _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "hi"}],
        turn_id="t-agent-ws",
        user_requested_agent=True,
        workspace_grants=("/Users/me/proj",),
        poll_timeout_s=0,
    )
    assert client.calls[0]["workspace_grants"] == ("/Users/me/proj",)


def test_chat_does_not_confirm_human_workspace_mutating_actions():
    action = {"id": "a1", "kind": "ActionEvent", "tool_name": "mail.send"}
    client = ScriptedClient(
        [
            action,
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "paused"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "send"}],
        turn_id="t-chat-deny",
        user_requested_agent=False,
        poll_timeout_s=0,
    )
    assert '"type": "pending_confirmation"' not in "".join(chunks)
    assert len(client.confirmed) == 1
    assert client.confirmed[0]["accept"] is False
    assert client.confirmed[0]["conversation_id"] == "conv-new"


def test_chat_rejects_agent_server_action_event_shape():
    action = {
        "id": "a-as145",
        "kind": "ActionEvent",
        "llm_response_id": "lr1",
        "parent_id": "p1",
        "action": {
            "kind": "MCPToolAction",
            "name": "mail.send",
            "arguments": {"to": ["a@example.test"]},
        },
    }
    client = ScriptedClient(
        [
            action,
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "paused"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "send"}],
        turn_id="t-chat-as145",
        user_requested_agent=False,
        poll_timeout_s=0,
    )
    assert '"type": "pending_confirmation"' not in "".join(chunks)
    assert len(client.confirmed) == 1
    assert client.confirmed[0]["accept"] is False


def test_chat_still_confirms_sandbox_actions():
    action = {"id": "a1", "kind": "ActionEvent", "tool_name": "terminal"}
    client = ScriptedClient(
        [
            action,
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "paused"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "run"}],
        turn_id="t-chat-term",
        user_requested_agent=False,
        poll_timeout_s=0,
    )
    assert '"type": "pending_confirmation"' in "".join(chunks)
    assert not getattr(client, "confirmed", [])
