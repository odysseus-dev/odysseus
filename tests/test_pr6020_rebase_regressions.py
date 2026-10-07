"""Executable regression coverage for behavior lost in PR #6020's rebase."""

import asyncio
import inspect
import json

import src.agent_loop as agent_loop


ODY_QWEN = "odysseus-qwen3-4b"
NOTES_TOOLS = {
    "manage_notes",
    "manage_calendar",
    "manage_tasks",
    "ask_user",
    "update_plan",
}
GENERAL_COMPACT_TOOLS = {"ask_user", "web_search", "web_fetch"}
CALENDAR_COMPACT_TOOLS = {
    "ask_user", "bash", "get_workspace", "manage_calendar", "manage_notes",
    "python", "read_file", "web_fetch", "web_search",
}


def _collect(generator):
    async def _run():
        return [chunk async for chunk in generator]

    return asyncio.run(_run())


def _events(chunks):
    return [
        json.loads(chunk[6:])
        for chunk in chunks
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]")
    ]


def _install_route_probe(monkeypatch):
    prompt_calls = []
    stream_calls = []

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_agent_route_tool_mode",
        lambda *args, **kwargs: (True, False, False),
    )

    def fake_build(
        messages,
        model,
        _active_document,
        _mcp_mgr,
        disabled_tools=None,
        **kwargs,
    ):
        prompt_calls.append(
            {
                "model": model,
                "relevant_tools": set(kwargs.get("relevant_tools") or set()),
                "disabled_tools": set(disabled_tools or set()),
                "workspace": kwargs.get("workspace"),
            }
        )
        return (list(messages), [])

    async def fake_stream(_candidates, _messages, **kwargs):
        stream_calls.append(kwargs)
        yield 'data: {"delta": "ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "_build_system_prompt", fake_build)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    return prompt_calls, stream_calls


def _run_probe(messages, *, relevant_tools, **kwargs):
    return _collect(
        agent_loop.stream_agent_loop(
            "https://api.example/v1",
            kwargs.pop("model", ODY_QWEN),
            messages,
            max_rounds=1,
            relevant_tools=set(relevant_tools),
            _is_teacher_run=True,
            **kwargs,
        )
    )


def test_odysseus_notes_mode_clamps_without_overriding_caller_denials(monkeypatch):
    prompt_calls, _ = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Add buy milk to my notes."}],
        relevant_tools={"bash", "manage_notes", "manage_calendar", "manage_tasks"},
        disabled_tools={"manage_notes", "manage_calendar", "manage_tasks"},
    )

    route = prompt_calls[0]
    assert route["relevant_tools"] == NOTES_TOOLS
    assert {"manage_notes", "manage_calendar", "manage_tasks"} <= route["disabled_tools"]


def test_odysseus_router_uses_compact_core(monkeypatch):
    prompt_calls, _ = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Explain the CAP theorem with a concrete distributed database example."}],
        relevant_tools={"bash", "manage_notes", "ask_user"},
    )

    route = prompt_calls[0]
    assert route["relevant_tools"] == GENERAL_COMPACT_TOOLS


def test_odysseus_general_no_tool_mode_has_no_executable_surface(monkeypatch):
    from src.tool_policy import known_tool_names

    # The current merged profile takes the compact-router branch. Exercise
    # the legacy general mode itself so its execution denial stays covered.
    monkeypatch.setattr(agent_loop, "_is_qwen38_tool_router", lambda model: False)
    prompt_calls, stream_calls = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Explain the CAP theorem with a concrete distributed database example."}],
        relevant_tools={"bash", "manage_notes", "ask_user"},
    )

    assert stream_calls[0]["tools"] is None
    assert known_tool_names() <= prompt_calls[0]["disabled_tools"]


def test_odysseus_calendar_intent_uses_compact_calendar_route(monkeypatch):
    prompt_calls, _ = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Add lunch tomorrow to my calendar."}],
        relevant_tools={"manage_notes", "manage_calendar", "manage_tasks", "bash"},
    )

    assert prompt_calls[0]["relevant_tools"] == CALENDAR_COMPACT_TOOLS


def test_odysseus_calendar_followup_keeps_compact_calendar_route(monkeypatch):
    prompt_calls, _ = _install_route_probe(monkeypatch)
    messages = [
        {"role": "user", "content": "Add lunch tomorrow to my calendar."},
        {
            "role": "assistant",
            "content": "Done.",
            "metadata": {
                "tool_events": [
                    {
                        "tool": "manage_calendar",
                        "command": '{"action":"create_event","summary":"Lunch"}',
                        "output": "Created event evt-123 at noon.",
                    }
                ]
            },
        },
        {"role": "user", "content": "Move it to 3pm."},
    ]

    _run_probe(
        messages,
        relevant_tools={"manage_notes", "manage_calendar", "manage_tasks", "bash"},
    )

    assert prompt_calls[0]["relevant_tools"] == CALENDAR_COMPACT_TOOLS


def test_agent_route_passes_workspace_to_system_prompt(monkeypatch):
    prompt_calls, _ = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Fix the failing test in this project."}],
        model="gpt-4o",
        relevant_tools={"bash", "read_file", "apply_patch"},
        workspace="/tmp/example-repo",
    )

    assert prompt_calls[0]["workspace"] == "/tmp/example-repo"


def test_odysseus_compact_primary_uses_deterministic_temperature(monkeypatch):
    _, stream_calls = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Add buy milk to my notes."}],
        relevant_tools={"manage_notes"},
        temperature=1.2,
    )

    assert stream_calls[0]["temperature"] == 0.0


def test_qwen_fallback_candidate_gets_capped_temperature(monkeypatch):
    """A non-qwen primary must not leak its temperature into a qwen fallback."""

    _, stream_calls = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Explain the CAP theorem."}],
        model="gpt-4o",
        relevant_tools={"bash"},
        temperature=1.2,
        fallbacks=[("https://qwen.example/v1", ODY_QWEN, {})],
    )

    assert stream_calls[0]["temperature"] == 1.2
    factory = stream_calls[0]["candidate_request_factory"]
    request = factory(1, "https://qwen.example/v1", ODY_QWEN, {})
    if inspect.isawaitable(request):
        request = asyncio.run(request)
    assert request["kwargs"]["temperature"] == 0.2


def test_non_qwen_fallback_keeps_requested_temperature(monkeypatch):
    """A qwen primary's 0.2 cap must not leak into a non-qwen fallback."""

    _, stream_calls = _install_route_probe(monkeypatch)

    _run_probe(
        [{"role": "user", "content": "Add buy milk to my notes."}],
        relevant_tools={"manage_notes"},
        temperature=1.2,
        fallbacks=[("https://backup.example/v1", "gpt-4o", {})],
    )

    assert stream_calls[0]["temperature"] == 0.0
    factory = stream_calls[0]["candidate_request_factory"]
    request = asyncio.run(factory(1, "https://backup.example/v1", "gpt-4o", {}))
    assert request["kwargs"]["temperature"] == 1.2


def test_qwen_notes_fallback_reenables_personal_managers(monkeypatch):
    """Model/route fallback cannot bypass caller-disabled tools; denials persist."""

    _install_route_probe(monkeypatch)
    stream_round = 0
    resolve_round = 0
    seen_exec = {}

    async def fake_stream(_candidates, _messages, **kwargs):
        nonlocal stream_round
        stream_round += 1
        if stream_round == 1:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "fallback",
                        "answered_by": ODY_QWEN,
                        "candidate_index": 1,
                    }
                )
                + "\n\n"
            )
            yield 'data: {"delta": "Adding the note."}\n\n'
        else:
            yield 'data: {"delta": "Done."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_resolve(*args, **kwargs):
        nonlocal resolve_round
        resolve_round += 1
        if resolve_round == 1:
            return ([agent_loop.ToolBlock("manage_notes", "{}")], False, [])
        return ([], False, [])

    async def fake_execute(block, *args, **kwargs):
        # Execution is the consumer daybreak's probe showed rejecting the
        # managers: it receives the shared disabled_tools set, not the
        # answering route's own tool state.
        seen_exec["disabled_tools"] = set(kwargs.get("disabled_tools") or [])
        return ("manage_notes: saved", {"output": "noted", "exit_code": 0})

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "_resolve_tool_blocks", fake_resolve)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    _collect(
        agent_loop.stream_agent_loop(
            "https://api.example/v1",
            "gpt-4o",
            [{"role": "user", "content": "Add buy milk to my notes."}],
            max_rounds=2,
            relevant_tools={"manage_notes", "manage_calendar", "manage_tasks", "bash"},
            disabled_tools={"manage_notes", "manage_calendar", "manage_tasks"},
            fallbacks=[("https://qwen.example/v1", ODY_QWEN, {})],
            _is_teacher_run=True,
        )
    )

    # Caller hard denials are absolute: route fallback cannot re-enable disabled tools.
    assert {"manage_notes", "manage_calendar", "manage_tasks"}.issubset(
        seen_exec["disabled_tools"]
    )


def test_persisted_mcp_tool_event_keeps_description_and_resolved_name(monkeypatch):
    _install_route_probe(monkeypatch)
    stream_round = 0
    resolve_round = 0

    async def fake_stream(_candidates, _messages, **kwargs):
        nonlocal stream_round
        stream_round += 1
        if stream_round == 1:
            yield 'data: {"delta": "Calling calendar."}\n\n'
        else:
            yield 'data: {"delta": "Finished."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_resolve(*args, **kwargs):
        nonlocal resolve_round
        resolve_round += 1
        if resolve_round == 1:
            return ([agent_loop.ToolBlock("mcp", "{}")], False, [])
        return ([], False, [])

    async def fake_execute(block, *args, **kwargs):
        assert block.tool_type == "mcp"
        return (
            "mcp__calendar__create_event: created team sync",
            {"output": "Created event evt-456.", "exit_code": 0},
        )

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "_resolve_tool_blocks", fake_resolve)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    chunks = _collect(
        agent_loop.stream_agent_loop(
            "https://api.example/v1",
            "gpt-4o",
            [{"role": "user", "content": "Create the team sync event."}],
            max_rounds=2,
            relevant_tools={"mcp"},
            _is_teacher_run=True,
        )
    )
    metrics = next(
        event["data"] for event in _events(chunks) if event.get("type") == "metrics"
    )
    persisted = metrics["tool_events"][0]

    assert persisted["tool"] == "mcp__calendar__create_event"
    assert persisted["desc"] == "mcp__calendar__create_event: created team sync"
