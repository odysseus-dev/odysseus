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


def test_bare_less_than_and_comparison_operators_false_alarm_recognition():
    # A bare "<" starts buffering because it could be a tool-call tag prefix like "<tool_call>"
    assert _streamed_tool_markup_starts(' <')
    assert _streamed_tool_markup_starts('x <')
    assert _streamed_tool_markup_starts('<tool_call')
    assert _streamed_tool_markup_starts('<invoke')

    # Once subsequent characters arrive and cannot become a tool-call tag, false alarm is detected
    assert not _streamed_tool_markup_starts(' < 2')
    assert not _streamed_tool_markup_starts('x < 2')
    assert not _streamed_tool_markup_starts('len(items) < 2')
    assert not _streamed_tool_markup_starts(' < 2:')
    assert not _streamed_tool_markup_starts('x <= y')
    assert not _streamed_tool_markup_starts('a < b')


async def test_stream_agent_loop_does_not_truncate_at_less_than_operator(monkeypatch):
    import json
    import src.agent_loop as al

    async def fake_stream(*args, **kwargs):
        for d in ["def is_short(items):\n", "    return len(items)", " <", " 2\n"]:
            yield "data: " + json.dumps({"delta": d}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "get_setting", lambda k, d=None: d, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream, raising=False)

    deltas = []
    async for chunk in al.stream_agent_loop(
        "http://local.test/v1",
        "qwen3.6-35b-a3b-heretic",
        [{"role": "user", "content": "Write python function"}],
        max_rounds=1,
        owner="test_user",
    ):
        for line in chunk.split("\n"):
            line = line.strip()
            if line.startswith("data: ") and line[6:] != "[DONE]":
                try:
                    payload = json.loads(line[6:])
                    if "delta" in payload:
                        deltas.append(payload["delta"])
                except Exception:
                    pass

    full_text = "".join(deltas)
    assert full_text == "def is_short(items):\n    return len(items) < 2\n"


async def test_stream_agent_loop_still_hides_real_tool_markup(monkeypatch):
    import json
    import src.agent_loop as al

    async def fake_stream(*args, **kwargs):
        chunks = [
            "Sure.",
            " <",
            "tool",
            "_call",
            '>{"name":"host_shell","arguments":{"command":"pwd"}}',
            "</",
            "tool",
            "_call",
            ">",
            " done",
        ]
        for c in chunks:
            yield "data: " + json.dumps({"delta": c}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "get_setting", lambda k, d=None: d, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream, raising=False)

    deltas = []
    async for chunk in al.stream_agent_loop(
        "http://local.test/v1",
        "qwen3.6-35b-a3b-heretic",
        [{"role": "user", "content": "run command"}],
        max_rounds=1,
        owner="test_user",
    ):
        for line in chunk.split("\n"):
            line = line.strip()
            if line.startswith("data: ") and line[6:] != "[DONE]":
                try:
                    payload = json.loads(line[6:])
                    if "delta" in payload:
                        deltas.append(payload["delta"])
                except Exception:
                    pass

    assert "".join(deltas) == "Sure. done"
