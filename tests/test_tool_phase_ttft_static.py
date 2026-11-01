from pathlib import Path


CHAT_JS = Path(__file__).parents[1] / "static" / "js" / "chat.js"


def test_tool_start_stops_initial_ttft_before_tool_execution():
    source = CHAT_JS.read_text()
    branch = source.index("} else if (json.type === 'tool_start') {")
    mark_output = source.index("markFirstVisibleOutput();", branch)
    close_thinking = source.index("_closeOpenThinkingMarkup(_isBg);", branch)

    assert branch < mark_output < close_thinking
