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
