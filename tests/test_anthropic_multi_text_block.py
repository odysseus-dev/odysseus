"""Regression: _parse_anthropic_response dropped all text after the first text block.

The Anthropic Messages API can return multiple text blocks in content —
text split around a tool_use block, or citation-segmented responses.
The original code returned on the first block; all subsequent text was silently
lost. Fix: join the text of every text block in order.
"""
import pytest
from src.llm_core import _parse_anthropic_response


# ---------------------------------------------------------------------------
# Basic cases
# ---------------------------------------------------------------------------

def test_single_text_block():
    data = {"content": [{"type": "text", "text": "Hello world"}]}
    assert _parse_anthropic_response(data) == "Hello world"


def test_empty_content():
    assert _parse_anthropic_response({"content": []}) == ""


def test_missing_content_key():
    assert _parse_anthropic_response({}) == ""


def test_no_text_blocks_only_tool_use():
    data = {
        "content": [
            {"type": "tool_use", "id": "t1", "name": "do_thing", "input": {}}
        ]
    }
    assert _parse_anthropic_response(data) == ""


# ---------------------------------------------------------------------------
# Multi-block: the core regression
# ---------------------------------------------------------------------------

def test_two_text_blocks_are_joined():
    data = {
        "content": [
            {"type": "text", "text": "First part. "},
            {"type": "text", "text": "Second part."},
        ]
    }
    assert _parse_anthropic_response(data) == "First part. Second part."


def test_text_blocks_interleaved_with_tool_use():
    """Text → tool_use → text: only the two text blocks should be joined."""
    data = {
        "content": [
            {"type": "text", "text": "Before tool. "},
            {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}},
            {"type": "text", "text": "After tool."},
        ]
    }
    assert _parse_anthropic_response(data) == "Before tool. After tool."


def test_thinking_block_is_skipped():
    """thinking blocks must be ignored (they are not user-visible text)."""
    data = {
        "content": [
            {"type": "thinking", "thinking": "Let me reason about this..."},
            {"type": "text", "text": "Here is my answer."},
        ]
    }
    assert _parse_anthropic_response(data) == "Here is my answer."


def test_three_text_blocks_all_joined():
    data = {
        "content": [
            {"type": "text", "text": "A"},
            {"type": "text", "text": "B"},
            {"type": "text", "text": "C"},
        ]
    }
    assert _parse_anthropic_response(data) == "ABC"


def test_text_block_with_empty_text():
    """Empty text fields contribute nothing but don't break joining."""
    data = {
        "content": [
            {"type": "text", "text": "Start "},
            {"type": "text", "text": ""},
            {"type": "text", "text": "End"},
        ]
    }
    assert _parse_anthropic_response(data) == "Start End"


def test_text_block_missing_text_key():
    """A text block with no 'text' key defaults to empty string."""
    data = {
        "content": [
            {"type": "text"},
            {"type": "text", "text": " present"},
        ]
    }
    assert _parse_anthropic_response(data) == " present"
