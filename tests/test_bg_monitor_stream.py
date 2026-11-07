import asyncio
import json
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


def test_background_job_output_is_wrapped_and_arms_gate(monkeypatch):
    monkeypatch.setattr(bg_monitor.bg_jobs, "result_text", lambda rec: "injected output")

    message = bg_monitor._background_result_message({"id": "job-1"})

    assert message["metadata"]["trusted"] is False
    assert message["metadata"]["tool_gate_untrusted"] is True
    assert "injected output" in message["content"]


def test_background_drain_preserves_exact_approval_card(monkeypatch):
    approval = {
        "kind": "tool_approval",
        "approval_id": "opaque-id",
        "question": "Allow this exact action once?",
        "options": [{"label": "Allow once"}, {"label": "Deny"}],
    }

    async def fake_stream_agent_loop(*args, **kwargs):
        yield "data: " + json.dumps({
            "type": "tool_output",
            "tool": "bash",
            "command": "echo ok",
            "output": "Waiting for an exact user approval.",
            "exit_code": None,
            "ask_user": approval,
        })
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
        owner="owner",
    )

    _, events = asyncio.run(bg_monitor._drain_agent(sess, []))

    assert events[0]["ask_user"] == approval
