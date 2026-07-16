"""Qwen / Hermes <tool_call> bare-JSON tool-call parsing (#5187).

These models emit the OpenAI function-call object as JSON directly inside a
<tool_call> wrapper, not the <invoke>/<parameter> XML the wrapper parser
expected. In text mode (manually added Ollama endpoints, native_tools=False)
the block used to land as inert text and nothing ran.
"""
from src.agent_tools import parse_tool_blocks, strip_tool_blocks


def test_qwen_tool_call_json_parses_and_strips():
    raw = '<tool_call>\n{"name": "bash", "arguments": {"command": "mkdir -p agent-test"}}\n</tool_call>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "mkdir -p agent-test"
    # The wrapper markup must never reach the user.
    assert strip_tool_blocks(raw).strip() == ""


def test_qwen_multiple_tool_calls_all_parse():
    # Qwen routinely emits several <tool_call> blocks in one turn; a global
    # "stop after the first" guard would drop all but the first.
    raw = (
        '<tool_call>\n{"name": "bash", "arguments": {"command": "mkdir -p agent-test"}}\n</tool_call>\n'
        '<tool_call>\n{"name": "python", "arguments": {"code": "print(1)"}}\n</tool_call>'
    )

    blocks = parse_tool_blocks(raw)

    assert [(b.tool_type, b.content) for b in blocks] == [
        ("bash", "mkdir -p agent-test"),
        ("python", "print(1)"),
    ]


def test_qwen_tool_call_arguments_as_json_string():
    # Some Hermes finetunes emit "arguments" as a JSON-encoded string.
    raw = '<tool_call>{"name": "web_search", "arguments": "{\\"query\\": \\"hello world\\"}"}</tool_call>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "hello world"


def test_qwen_tool_call_normalizes_alias_name():
    # "shell" is an alias for the bash tool.
    raw = '<tool_call>{"name": "shell", "arguments": {"command": "ls -la"}}</tool_call>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "ls -la"


def test_qwen_tool_call_missing_closer_still_parses():
    # Streamed output sometimes drops the closing </tool_call>.
    raw = '<tool_call>\n{"name": "bash", "arguments": {"command": "pwd"}}'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "pwd"


def test_existing_invoke_xml_still_parses():
    # The bare-JSON path must not regress the <invoke>/<parameter> format.
    raw = '<tool_call><invoke name="bash"><parameter name="command">ls</parameter></invoke></tool_call>'

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "ls"


def test_bare_json_in_prose_is_not_executed():
    # Only JSON *inside a <tool_call> wrapper* is a call. A JSON object shown
    # as an example in prose must never execute.
    raw = 'For example the model might emit {"name": "bash", "arguments": {"command": "rm -rf /"}} as text.'

    assert parse_tool_blocks(raw) == []


def test_tool_call_non_object_json_is_ignored():
    # A JSON array/string inside the wrapper is not a function call.
    raw = '<tool_call>["not", "a", "call"]</tool_call>'

    assert parse_tool_blocks(raw) == []


def test_tool_call_without_name_is_ignored():
    raw = '<tool_call>{"arguments": {"command": "ls"}}</tool_call>'

    assert parse_tool_blocks(raw) == []


# ── #5199 review, P1: XML-like data inside JSON args must stay data ──────────

def test_xml_like_string_in_json_arg_stays_data_for_intended_tool():
    # A wrapped write_file whose content contains <bash>…</bash> must remain a
    # single write_file call — the JSON body is classified as JSON before any
    # direct-XML scan, so the embedded tag is never reinterpreted as a Bash call.
    raw = (
        '<tool_call>{"name": "write_file", "arguments": '
        '{"path": "note.md", "content": "<bash>echo unsafe</bash>"}}</tool_call>'
    )

    blocks = parse_tool_blocks(raw)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "write_file"
    assert "<bash>echo unsafe</bash>" in blocks[0].content
    # The embedded tag must NOT have produced a bash call.
    assert not any(b.tool_type == "bash" for b in blocks)


def test_json_body_that_cannot_convert_fails_closed_not_xml():
    # A JSON wrapper body for an unknown tool, whose args contain XML-like text,
    # must fail closed — it must NOT fall through to the direct-XML scan and
    # execute the embedded <bash> tag.
    raw = (
        '<tool_call>{"name": "not_a_real_tool", "arguments": '
        '{"note": "<bash>echo pwned</bash>"}}</tool_call>'
    )

    assert parse_tool_blocks(raw) == []


# ── #5199 review, P2: non-string scalar fields must be rejected ──────────────

def test_numeric_command_is_rejected():
    # command:1 would build a ToolBlock with int content; the agent loop later
    # calls .strip() on it and aborts. Reject it (fail closed) instead.
    raw = '<tool_call>{"name": "bash", "arguments": {"command": 1}}</tool_call>'

    assert parse_tool_blocks(raw) == []


def test_numeric_code_is_rejected():
    raw = '<tool_call>{"name": "python", "arguments": {"code": 3.14}}</tool_call>'

    assert parse_tool_blocks(raw) == []


def test_converted_block_content_is_always_a_string():
    # Whatever converts, ToolBlock.content is str — never a scalar that would
    # break a later .strip().
    for raw in (
        '<tool_call>{"name": "bash", "arguments": {"command": "ls -la"}}</tool_call>',
        '<tool_call>{"name": "python", "arguments": {"code": "print(1)"}}</tool_call>',
        '<tool_call>{"name": "web_search", "arguments": {"query": "hi"}}</tool_call>',
    ):
        for b in parse_tool_blocks(raw):
            assert isinstance(b.content, str)
