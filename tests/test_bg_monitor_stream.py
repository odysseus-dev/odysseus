import asyncio
import sys
import types
from types import SimpleNamespace

from src import bg_monitor


def test_drain_agent_ignores_non_string_deltas(monkeypatch):
    async def fake_stream_agent_loop(*args, **kwargs):
        yield 'data: {"delta": null}'
        yield 'data: {"delta": ["bad"]}'
        yield 'data: {"delta": "ok"}'
        yield 'data: {"type": "agent_step", "round": 2}'
        yield 'data: {"type": "tool_output", "tool": "shell", "output": "done"}'
        yield "data: [DONE]"

    agent_loop = types.ModuleType("src.agent_loop")
    agent_loop.stream_agent_loop = fake_stream_agent_loop
    monkeypatch.setitem(sys.modules, "src.agent_loop", agent_loop)

    sess = SimpleNamespace(
        endpoint_url="http://example.test",
        model="model",
        headers=None,
        context_length=0,
        id="s1",
    )

    full, events = asyncio.run(bg_monitor._drain_agent(sess, []))

    assert full == "ok"
    assert events == [{
        "round": 2,
        "tool": "shell",
        "command": None,
        "output": "done",
        "exit_code": None,
    }]
