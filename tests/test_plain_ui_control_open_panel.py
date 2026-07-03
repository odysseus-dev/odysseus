import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def test_plain_ui_control_open_panel_is_rescued_even_when_fences_skipped():
    blocks = parse_tool_blocks("ui_control open_panel notes", skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "ui_control"
    assert blocks[0].content == "open_panel notes"


def test_plain_ui_control_open_panel_rescues_backticked_line():
    blocks = parse_tool_blocks("``ui_control open_panel cookbook```", skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "ui_control"
    assert blocks[0].content == "open_panel cookbook"


def test_plain_ui_control_open_panel_strips_executed_line_only():
    text = "I'll open it now.\nui_control open_panel notes"

    assert strip_tool_blocks(text, skip_fenced=True) == "I'll open it now."


def test_plain_ui_control_rescue_does_not_run_other_commands():
    assert parse_tool_blocks("ui_control switch_model gemma4:31b", skip_fenced=True) == []
    assert parse_tool_blocks("bash ls", skip_fenced=True) == []
