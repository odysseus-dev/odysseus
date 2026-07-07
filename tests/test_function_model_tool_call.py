import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def test_function_model_wrapper_runs_web_search_and_strips_markup():
    raw = """Sure, let me check what's making headlines in Sweden today.

<function_model>
<function_call>web_search</function_call>
<parameters>{"query": "Sweden news today July 2026"}</parameters>
</function_model>"""

    blocks = parse_tool_blocks(raw, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "Sweden news today July 2026"
    assert strip_tool_blocks(raw, skip_fenced=True) == "Sure, let me check what's making headlines in Sweden today."


def test_function_model_wrapper_with_unknown_tool_is_stripped_but_not_executed():
    raw = """Nope.
<function_model>
<function_call>launch_missiles</function_call>
<parameters>{"target": "moon"}</parameters>
</function_model>"""

    assert parse_tool_blocks(raw, skip_fenced=True) == []
    assert strip_tool_blocks(raw, skip_fenced=True) == "Nope."
