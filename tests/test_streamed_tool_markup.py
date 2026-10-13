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
