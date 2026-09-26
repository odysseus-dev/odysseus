"""Qwen3-Coder text-mode tool calls: <function=NAME> / <parameter=KEY> XML.

Qwen-architecture local models (here ornith-1.5:9b, `architecture qwen35`)
emit their native Qwen3-Coder call format when Odysseus runs them in text
mode, which is what every Ollama endpoint gets unless
ModelEndpoint.supports_tools is True:

    <tool_call>
    <function=bash>
    <parameter=command>
    echo hello
    </parameter>
    </function>
    </tool_call>

parse_tool_blocks returned zero blocks for this, so the call rendered as
plain chat text and no tool ever ran. The dialect differs from the
already-supported <invoke name="x"><parameter name="y"> form only in where
the names sit: in the tag itself rather than in a name attribute.

Parsing delegates to function_call_to_tool_block, the same converter the
<invoke> path uses, so the whole tool set and the per-tool content format
stay handled in one place.
"""
import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks

# Verbatim assistant message recovered from a real chat (data/app.db). Note the
# stray empty <function></function> pair the model emits after the opening tag:
# it looks like the cause of the failure and is not — the canonical form below
# parsed to zero blocks too.
REAL_MODEL_OUTPUT = (
    "<tool_call>\n"
    "<function=bash>\n"
    "<function>\n"
    "</function>\n"
    "<parameter=command>\n"
    'echo "bash works on $(uname -s)"\n'
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)

CANONICAL = (
    "<tool_call>\n"
    "<function=bash>\n"
    "<parameter=command>\n"
    "echo hello\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)


def test_canonical_qwen_function_xml_parses():
    blocks = parse_tool_blocks(CANONICAL)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "echo hello"


def test_real_model_output_with_stray_function_tag_parses():
    blocks = parse_tool_blocks(REAL_MODEL_OUTPUT)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == 'echo "bash works on $(uname -s)"'


def test_multiple_parameters_become_json_content():
    text = (
        "<tool_call>\n"
        "<function=read_file>\n"
        "<parameter=path>\n"
        "/etc/hosts\n"
        "</parameter>\n"
        "<parameter=limit>\n"
        "10\n"
        "</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert '"path": "/etc/hosts"' in blocks[0].content
    assert '"limit": "10"' in blocks[0].content


def test_tool_name_is_mapped_like_other_dialects():
    # "shell" maps to bash via _TOOL_NAME_MAP, same as the other XML paths.
    text = (
        "<tool_call>\n"
        "<function=shell>\n"
        "<parameter=command>ls -la</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "ls -la"


def test_multiple_sequential_calls():
    text = (
        "<tool_call>\n<function=bash>\n<parameter=command>ls</parameter>\n"
        "</function>\n</tool_call>\n"
        "Then:\n"
        "<tool_call>\n<function=bash>\n<parameter=command>pwd</parameter>\n"
        "</function>\n</tool_call>"
    )
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls"), ("bash", "pwd")]


def test_surrounding_prose_is_kept_and_block_is_stripped():
    text = "Let me check that for you.\n\n" + CANONICAL + "\n\nStandby."
    cleaned = strip_tool_blocks(text)
    assert "Let me check that for you." in cleaned
    assert "Standby." in cleaned
    assert "<function=bash>" not in cleaned
    assert "echo hello" not in cleaned


def test_unknown_tool_name_is_not_invented():
    text = (
        "<tool_call>\n"
        "<function=definitely_not_a_tool>\n"
        "<parameter=command>rm -rf /</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    assert parse_tool_blocks(text) == []
