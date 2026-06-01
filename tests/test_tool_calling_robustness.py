"""
Robustness of local-model tool calling.

Covers three fixes that keep file/doc tools and local-model tool-call formats
working when the happy path degrades:

  U6 — write_file / create_document / edit_document / update_document survive a
       tool-index (RAG) miss because they are in ALWAYS_AVAILABLE.
  U7 — Hermes / qwen2.5 ``<tool_call>{"name":...,"arguments":{...}}</tool_call>``
       JSON blocks parse into the standard ToolBlock structure.
  U8 — a JSONDecodeError on MCP tool args is LOGGED (with the raw content)
       before degrading to ``{}``, instead of silently running with no args.
"""

import json
import logging
import sys
from unittest.mock import MagicMock

import pytest

# Other test modules (e.g. test_agent_loop.py) stub `src.agent_tools` — and
# heavy deps it pulls in — as bare MagicMocks in sys.modules to avoid loading
# the full app stack. Under a full-suite run (`pytest tests/`) those stubs
# linger and would make `from src.agent_tools import ToolBlock` return a Mock,
# `parse_tool_blocks` find no real TOOL_TAGS, and `tool_schemas` reject every
# call. Evict any Mock-backed entries for the modules this file load-bears so
# we re-import the REAL implementations regardless of collection order. This
# keeps the file passing both in isolation AND in the full suite.
for _name in (
    "src.agent_tools",
    "src.tool_parsing",
    "src.tool_schemas",
    "src.tool_index",
    "src.tool_execution",
):
    _existing = sys.modules.get(_name)
    if isinstance(_existing, MagicMock):
        del sys.modules[_name]

from src.tool_index import ALWAYS_AVAILABLE
# Import agent_tools first so the agent_tools<->tool_parsing module pair finishes
# initializing in the app's normal order (importing tool_parsing first triggers a
# pre-existing partial-init circular import).
import src.agent_tools  # noqa: F401,E402
from src.tool_parsing import parse_tool_blocks


# ── U6: file/doc tools always available ──────────────────────────────────────

def test_always_available_includes_file_and_doc_tools():
    # On a tool-index timeout the agent falls back to ALWAYS_AVAILABLE; without
    # these it would silently lose every way to write a file or doc.
    for tool in ("write_file", "create_document", "edit_document", "update_document"):
        assert tool in ALWAYS_AVAILABLE, f"{tool} missing from ALWAYS_AVAILABLE"


# ── U7: Hermes-style <tool_call> JSON blocks ─────────────────────────────────

def test_parse_hermes_tool_call_block():
    text = (
        "Sure, let me check.\n"
        '<tool_call>{"name": "bash", "arguments": {"command": "ls -la"}}</tool_call>'
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "ls -la"


def test_parse_hermes_tool_call_with_double_encoded_args():
    # Some models double-encode arguments as a JSON string.
    text = '<tool_call>{"name": "web_search", "arguments": "{\\"query\\": \\"weather\\"}"}</tool_call>'
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "weather"


def test_hermes_block_not_shadowed_by_xml_matcher():
    # A JSON-bodied <tool_call> must not be silently dropped by the generic
    # <tool_call> XML matcher (which looks for nested <invoke> and finds none).
    text = '<tool_call>{"name": "read_file", "arguments": {"path": "/etc/hosts"}}</tool_call>'
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "/etc/hosts"


def test_invoke_xml_tool_call_still_parses():
    # Regression guard: the pre-existing <invoke> XML form is untouched.
    text = (
        '<tool_call><invoke name="bash">'
        '<parameter name="command">echo hi</parameter>'
        "</invoke></tool_call>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "echo hi"


# ── U8: malformed MCP args are logged, not silently swallowed ─────────────────

class _FakeMcp:
    """Minimal MCP manager that records the args it was called with."""

    def __init__(self):
        self.called_with = None

    async def call_tool(self, tool, args):
        self.called_with = args
        return {"output": "ok", "exit_code": 0}


def test_malformed_mcp_args_are_logged_before_degrading(monkeypatch, caplog):
    import asyncio

    from src import tool_execution
    from src.agent_tools import ToolBlock

    fake = _FakeMcp()
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: fake)
    # mcp__ tools are public-blocked for non-admins; treat the test owner as
    # admin so execution reaches the args-parsing branch under test.
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    # Looks like a JSON object (starts with '{') but is malformed, so json.loads
    # raises and the code degrades to {} — which must be logged, not silent.
    block = ToolBlock("mcp__server__tool", '{"bad": ')

    with caplog.at_level(logging.WARNING, logger="src.tool_execution"):
        desc, result = asyncio.run(tool_execution.execute_tool_block(block))

    # Degraded to empty args...
    assert fake.called_with == {}
    # ...but the malformed payload was logged (with the raw content) so it is
    # diagnosable rather than executed silently with no parameters.
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Malformed JSON args" in r.getMessage() for r in records)
    assert any("mcp__server__tool" in r.getMessage() for r in records)
    assert any('{"bad":' in r.getMessage() for r in records)
