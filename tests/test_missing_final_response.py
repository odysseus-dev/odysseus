import ast
import asyncio
import json
from pathlib import Path

import pytest


def _load_helpers():
    source = Path(__file__).parents[1].joinpath("src", "agent_loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"_provider_failure_message", "_agent_response_to_save", "_empty_response_fallback"}
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"json": json, "Optional": __import__("typing").Optional}
    exec(compile(module, "agent_loop_helpers", "exec"), namespace)
    return namespace


_HELPERS = _load_helpers()
_empty_response_fallback = _HELPERS["_empty_response_fallback"]
_agent_response_to_save = _HELPERS["_agent_response_to_save"]


def test_tool_calls_then_provider_error_preserve_events_and_expose_failure():
    events = [{"tool": "bash", "output": "exit_code=0"}]
    failure = {"status": 502, "text": "Request blocked."}

    response, chunk = _empty_response_fallback("", "", events, failure)

    assert "tools completed" in response.lower()
    assert "Request blocked." in response
    assert "Done." not in response
    assert json.loads(chunk.removeprefix("data: ").strip())["delta"] == response
    metrics = {"tool_events": events, "terminal_provider_error": failure, "synthesis_failed": True}
    assert _agent_response_to_save("", metrics) == response
    assert metrics["tool_events"] == events


def test_tool_calls_then_empty_synthesis_do_not_become_done():
    events = [{"tool": "read_file", "output": "ok"}]

    response, chunk = _empty_response_fallback("", "", events)

    assert response == "The tools completed, but the model did not provide a final response."
    assert "Done." not in response
    assert chunk is not None
    assert _agent_response_to_save("", {"tool_events": events}) == response


def test_successful_tool_turn_with_final_text_is_unchanged():
    events = [{"tool": "bash", "output": "ok"}]

    response, chunk = _empty_response_fallback("Tests passed.", "", events)

    assert response == "Tests passed."
    assert chunk is None
    assert _agent_response_to_save(response, {"tool_events": events}) == response


def test_failed_synthesis_is_not_classified_as_successful_done():
    failure = {"status": 502, "text": "Request blocked."}
    metrics = {"tool_events": [{"tool": "grep"}], "terminal_provider_error": failure, "synthesis_failed": True}

    saved = _agent_response_to_save("", metrics)

    assert saved
    assert saved != "Done."
    assert "provider request failed" in saved


def test_route_uses_failure_aware_response_selection():
    source = Path(__file__).parents[1].joinpath("routes", "chat_routes.py").read_text(encoding="utf-8")
    assert "_response_to_save = _agent_response_to_save(full_response, last_metrics)" in source
    assert '_response_to_save = full_response or "Done."' not in source


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


def _events(chunks):
    events = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            events.append(json.loads(chunk[6:]))
    return events


def _patch_loop_basics(monkeypatch, agent_loop):
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)


@pytest.mark.parametrize("with_tool", [False, True])
def test_stream_provider_error_is_terminal_and_reported(monkeypatch, with_tool):
    import src.agent_loop as agent_loop

    _patch_loop_basics(monkeypatch, agent_loop)
    calls = 0

    async def fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        if with_tool and calls == 1:
            call = {"id": "call_1", "name": "bash", "arguments": json.dumps({"command": "true"})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
            yield "data: [DONE]\n\n"
            return
        yield 'event: error\ndata: {"status": 502, "text": "Request blocked."}\n\n'

    async def fake_execute(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream, raising=False)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute, raising=False)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-test",
            [{"role": "user", "content": "Run the check and report."}],
            max_rounds=3,
            relevant_tools={"bash"},
            _is_teacher_run=True,
        )
    )
    events = _events(chunks)
    deltas = [event["delta"] for event in events if "delta" in event and not event.get("thinking")]
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")

    assert calls == (2 if with_tool else 1)
    assert metrics["synthesis_failed"] is True
    assert metrics["terminal_provider_error"]["status"] == 502
    assert "Request blocked." in "".join(deltas)
    assert "Done." not in "".join(deltas)
    if with_tool:
        assert len(metrics["tool_events"]) == 1
    else:
        assert "tool_events" not in metrics
