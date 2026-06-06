"""Tests for model_stream_quirks.py — pattern matching and thinking intent."""

import pytest

from src.model_stream_quirks import (
    DEFAULT_THINKING_ONLY_STALL_MS,
    MODEL_STREAM_QUIRKS,
    get_model_stream_quirk,
    match_model_stream_quirk,
    quirk_thinking_intent,
    thinking_tool_intent_in_text,
)


class TestMatchModelStreamQuirk:
    def test_exact_gemma4_e4b(self):
        matched = match_model_stream_quirk("gemma4:e4b")
        assert matched is not None
        assert matched[0] == "gemma4:e4b"

    def test_gemma4_wildcard(self):
        matched = match_model_stream_quirk("gemma4:7b")
        assert matched is not None
        assert matched[0] == "gemma4:*"

    def test_prefers_longer_pattern(self):
        matched = match_model_stream_quirk("gemma4:e4b")
        assert matched[0] == "gemma4:e4b"

    def test_unknown_model(self):
        assert match_model_stream_quirk("qwen3:8b") is None

    def test_case_insensitive(self):
        assert match_model_stream_quirk("Gemma4:E4B") is not None

    def test_quirk_fields(self):
        quirk = get_model_stream_quirk("gemma4:e4b")
        assert quirk["thinking_only_stall_ms"] == DEFAULT_THINKING_ONLY_STALL_MS
        assert quirk["auto_continue_on_thinking_only"] is True


class TestThinkingToolIntent:
    def test_should_call_tool_name(self):
        assert thinking_tool_intent_in_text("I should call list_served_models next") == "list_served_models"

    def test_call_backtick_tool(self):
        assert thinking_tool_intent_in_text("I'll call `bash` to inspect") == "bash"

    def test_no_match_on_plain_text(self):
        assert thinking_tool_intent_in_text("Here is my answer to the user.") is None

    def test_quirk_thinking_intent_in_block(self):
        round_text = (
            "<think>I should call list_served_models to see what's installed."
            "</think>"
        )
        assert quirk_thinking_intent(round_text, "gemma4:e4b") == "list_served_models"

    def test_quirk_skipped_for_non_quirk_model(self):
        round_text = "<think>I should call list_served_models</think>"
        assert quirk_thinking_intent(round_text, "qwen3:8b") is None


def test_registry_not_empty():
    assert "gemma4:e4b" in MODEL_STREAM_QUIRKS
