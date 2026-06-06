"""Tests for model_stream_quirks.py — universal thinking stall policy."""

import pytest

from src.model_stream_quirks import (
    DEFAULT_THINKING_ONLY_STALL_MS,
    MODEL_STREAM_QUIRKS,
    THINKING_ONLY_NUDGE_MS,
    THINKING_ONLY_TIMEOUT_MS,
    get_model_stream_quirk,
    match_model_stream_quirk,
    quirk_thinking_intent,
    resolve_thinking_stall_policy,
    thinking_tool_intent_in_text,
)


class TestResolveThinkingStallPolicy:
    def test_universal_default_for_any_model(self):
        policy = resolve_thinking_stall_policy("qwen3:14b")
        assert policy["nudge_ms"] == THINKING_ONLY_NUDGE_MS
        assert policy["timeout_ms"] == THINKING_ONLY_TIMEOUT_MS
        assert policy["auto_continue_on_thinking_only"] is True

    def test_unknown_model_gets_default(self):
        policy = resolve_thinking_stall_policy("llama3.2:3b")
        assert policy["nudge_ms"] == 12_000
        assert policy["timeout_ms"] == 25_000

    def test_legacy_stall_alias_maps_to_nudge(self, monkeypatch):
        monkeypatch.setitem(
            MODEL_STREAM_QUIRKS,
            "test:*",
            {"thinking_only_stall_ms": 9_000, "thinking_only_timeout_ms": 18_000},
        )
        policy = resolve_thinking_stall_policy("test:1b")
        assert policy["nudge_ms"] == 9_000
        assert policy["timeout_ms"] == 18_000


class TestMatchModelStreamQuirk:
    def test_no_override_by_default(self):
        assert match_model_stream_quirk("gemma4:e4b") is None
        assert match_model_stream_quirk("qwen3:8b") is None

    def test_override_when_registered(self, monkeypatch):
        monkeypatch.setitem(
            MODEL_STREAM_QUIRKS,
            "gemma4:*",
            {"thinking_only_nudge_ms": 8_000},
        )
        matched = match_model_stream_quirk("gemma4:7b")
        assert matched is not None
        assert matched[0] == "gemma4:*"


class TestThinkingToolIntent:
    def test_should_call_tool_name(self):
        assert thinking_tool_intent_in_text("I should call list_served_models next") == "list_served_models"

    def test_call_backtick_tool(self):
        assert thinking_tool_intent_in_text("I'll call `bash` to inspect") == "bash"

    def test_no_match_on_plain_text(self):
        assert thinking_tool_intent_in_text("Here is my answer to the user.") is None

    def test_thinking_intent_in_block_any_model(self):
        round_text = (
            "<think>I should call list_served_models to see what's installed."
            "</think>"
        )
        assert quirk_thinking_intent(round_text, "qwen3:14b") == "list_served_models"
        assert quirk_thinking_intent(round_text, "gemma4:e4b") == "list_served_models"


def test_default_nudge_alias():
    assert DEFAULT_THINKING_ONLY_STALL_MS == THINKING_ONLY_NUDGE_MS


def test_registry_empty_by_default():
    assert MODEL_STREAM_QUIRKS == {}
