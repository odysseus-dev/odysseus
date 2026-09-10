import asyncio
from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.dispatcher import AgentDispatcher
from services.agents.legacy_bridge import stream_governed_agent


class ScriptedClient:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def create_or_resume(self, **kwargs):
        self.calls.append(kwargs)
        cid = kwargs.get("conversation_id") or "conv-new"
        return type("R", (), {"conversation_id": cid, "execution_id": f"agent-server:{cid}"})()

    def conversation_events(self, conversation_id):
        return list(self.events)


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
    )
    assert client.calls[0]["conversation_id"] is None
    assert '"rebound": true' in "".join(chunks)
