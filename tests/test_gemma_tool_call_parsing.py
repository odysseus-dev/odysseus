from src.agent_tools import parse_tool_blocks, strip_tool_blocks


def test_gemma_tool_call_json_args_parse_and_strip():
    raw = '<|tool_call|>call:web_search{"query":"hello world"}<|tool_call|>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "hello world"
    assert strip_tool_blocks(raw).strip() == ""


def test_gemma_tool_call_unquoted_args_parse():
    raw = '<|tool_call|>call:web_search{query: "hello world"}<|tool_call|>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "hello world"


def test_gemma_tool_call_normalizes_dash_tool_name():
    raw = '<|tool_call|>call:read-file{"path":"README.md"}<|tool_call|>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "README.md"


def test_gemma_parser_does_not_strip_non_tool_fenced_metadata():
    raw = '```python id="abc"\nprint("hello")\n```'

    assert parse_tool_blocks(raw) == []
    assert strip_tool_blocks(raw) == raw
