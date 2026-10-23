import asyncio
import json

import pytest

from src import agent_loop
from src.agent_loop import (
    _streamed_tool_markup_complete,
    _streamed_tool_markup_starts,
    _strip_incomplete_tool_markup_tail,
)
from src.tool_parsing import strip_tool_blocks


def test_dsml_tool_markup_is_complete_only_after_closing_tag():
    start = "<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"host_shell\">"
    assert not _streamed_tool_markup_complete(start)
    assert _streamed_tool_markup_complete(start + "</｜｜DSML｜｜invoke>")


def test_dsml_tool_markup_strips_to_empty_visible_text():
    markup = (
        "<｜｜DSML｜｜tool_calls>"
        "<｜｜DSML｜｜invoke name=\"host_shell\">"
        "<｜｜DSML｜｜parameter name=\"command\" string=\"true\">pwd"
        "</｜｜DSML｜｜parameter>"
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )
    assert strip_tool_blocks(markup, skip_fenced=True) == ""


def test_chunk_split_tool_call_prefix_is_buffered_and_not_persisted():
    assert _streamed_tool_markup_starts('I will search<tool_cal')
    assert _strip_incomplete_tool_markup_tail('I will search<tool_cal') == 'I will search'


def _stream_response(monkeypatch, deltas):
    """Drive the real SSE loop without model, tool execution, or owner services."""
    parsed_responses = []
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "_agent_route_tool_mode", lambda *args, **kwargs: (True, False, False))
    monkeypatch.setattr(agent_loop, "_build_system_prompt", lambda messages, *args, **kwargs: (list(messages), []))

    def resolve_tools(response, *args, **kwargs):
        parsed_responses.append(response)
        return [], False, []

    async def provider(*args, **kwargs):
        for delta in deltas:
            yield "data: " + json.dumps({"delta": delta}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "_resolve_tool_blocks", resolve_tools)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", provider)

    async def collect():
        return [
            json.loads(chunk[6:])
            async for chunk in agent_loop.stream_agent_loop(
                "https://model.test/v1", "test-model",
                [{"role": "user", "content": "Inspect the current state."}],
                owner="fixture", max_rounds=1, relevant_tools={"inspect_state"},
                forced_tools={"inspect_state"},
                external_tool_schemas=[{
                    "type": "function",
                    "function": {
                        "name": "inspect_state",
                        "description": "Return the example state.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }],
                fallbacks=[], fallback_on_empty=False, _is_teacher_run=True,
            )
            if chunk.startswith("data: {")
        ]

    events = asyncio.run(collect())
    visible = "".join(event["delta"] for event in events if "delta" in event and not event.get("thinking"))
    return visible, parsed_responses


@pytest.mark.parametrize("deltas", [
    ["if x", " <", " 2:", "\n    pass"],
    ["For x", " <", " 2,", " the bound holds."],
    ["Use ", "<", "div>", "content</div>"],
    ["Keep ", "<tool_cal", "culator>", " as text."],
])
def test_false_tool_prefix_releases_ordinary_stream_text(monkeypatch, deltas):
    visible, parsed = _stream_response(monkeypatch, deltas)

    assert visible == "".join(deltas)
    assert parsed == ["".join(deltas)]


@pytest.mark.parametrize("deltas", [
    ["The comparison ends with", " <"],
    ["The comparison ends with <", "  "],
])
def test_stream_end_flushes_bare_less_than_sign(monkeypatch, deltas):
    visible, parsed = _stream_response(monkeypatch, deltas)

    assert visible == "".join(deltas)
    assert parsed == ["".join(deltas)]
    assert agent_loop._visible_response_text(visible) == "".join(deltas).strip()


@pytest.mark.parametrize("deltas, expected", [
    (["Before ", "<tool_cal", 'l>{"name":"inspect_state"}', "</tool_call>", " after"], "Before  after"),
    (["Before ", "<", 'invoke name="inspect_state">', "{}</invoke>", " after"], "Before  after"),
    (["Before ", "<｜｜D", 'SML｜｜invoke name="inspect_state">', "{}</｜｜DSML｜｜invoke>", " after"], "Before  after"),
    (["Before <tool_cal"], "Before"),
    (["Before <invoke", ' name="inspect_state">{}'], "Before "),
])
def test_tool_markup_stays_hidden_while_raw_response_reaches_parser(monkeypatch, deltas, expected):
    visible, parsed = _stream_response(monkeypatch, deltas)

    assert visible == expected
    assert parsed == ["".join(deltas)]
