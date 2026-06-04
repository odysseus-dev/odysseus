"""Tests for _parse_anthropic_response (src/llm_core.py)."""

from src.llm_core import _parse_anthropic_response


def test_concatenates_multiple_text_blocks():
    # Regression: only the first text block was returned, dropping the rest.
    data = {"content": [
        {"type": "text", "text": "Part A "},
        {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
        {"type": "text", "text": "Part B"},
    ]}
    assert _parse_anthropic_response(data) == "Part A Part B"


def test_skips_non_text_blocks():
    data = {"content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "text", "text": "answer"},
    ]}
    assert _parse_anthropic_response(data) == "answer"


def test_single_block_and_empty():
    assert _parse_anthropic_response({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    assert _parse_anthropic_response({"content": []}) == ""
    assert _parse_anthropic_response({}) == ""
