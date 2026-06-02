"""Tests for context_compactor.py — constants and prompt templates.
Uses mock imports to avoid loading the full app stack."""

import sys
from unittest.mock import MagicMock

# Mock heavy dependencies before importing
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'core.models', 'core.database',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

from src.context_compactor import (
    AUTO_BUDGET_FLOOR,
    AUTO_BUDGET_MAX,
    COMPACT_THRESHOLD,
    SELF_SUMMARY_SYSTEM_PROMPT,
    SUMMARY_MAX_TOKENS,
    resolve_input_budget,
    trim_for_context,
)


class TestCompactThreshold:
    def test_value(self):
        assert COMPACT_THRESHOLD == 0.85

    def test_summary_max_tokens(self):
        assert SUMMARY_MAX_TOKENS == 1024


class TestResolveInputBudget:
    """`agent_input_token_budget` semantics: <0 auto, 0 unlimited (handled by
    the caller), >0 explicit hard cap. Regression cover for #1170 — a flat
    default must not silently cap long-context models."""

    def test_explicit_cap_is_honored(self):
        # An explicit positive budget is a hard cap regardless of window size.
        assert resolve_input_budget(6000, context_length=200000) == 6000

    def test_explicit_cap_bounded_by_small_window(self):
        # A cap larger than the model window collapses to the window.
        assert resolve_input_budget(50000, context_length=8000) == 8000

    def test_explicit_cap_with_unknown_window(self):
        # Unknown window (0) leaves the explicit cap untouched.
        assert resolve_input_budget(6000, context_length=0) == 6000

    def test_auto_scales_with_window_below_ceiling(self):
        # The bug in #1170: a model used to be trimmed to 6000. Below the auto
        # ceiling, auto mode scales to a fraction of the window instead.
        budget = resolve_input_budget(-1, context_length=32000)
        assert budget == int(32000 * 0.75)
        assert budget > AUTO_BUDGET_FLOOR

    def test_auto_bounded_by_ceiling_on_large_window(self):
        # A 200k window scaled to 150k would blow up per-turn cost; the auto
        # ceiling caps it. This is the safeguard requested in review of #1189.
        assert resolve_input_budget(-1, context_length=200000) == AUTO_BUDGET_MAX

    def test_auto_ceiling_caps_huge_window(self):
        # Even a 1M-token window stays at the ceiling, not ~750k.
        assert resolve_input_budget(-1, context_length=1_000_000) == AUTO_BUDGET_MAX

    def test_auto_ceiling_disabled_with_zero(self):
        # auto_max <= 0 removes the ceiling — opt-in to full scaling.
        budget = resolve_input_budget(-1, context_length=1_000_000, auto_max=0)
        assert budget == int(1_000_000 * 0.75)

    def test_auto_custom_ceiling_is_respected(self):
        assert resolve_input_budget(-1, context_length=200000, auto_max=16000) == 16000

    def test_explicit_cap_ignores_auto_ceiling(self):
        # auto_max only governs auto mode; an explicit cap is honored verbatim.
        assert resolve_input_budget(50000, context_length=200000, auto_max=16000) == 50000

    # --- auto mode must never exceed the known window (review of #1189) ---

    def test_auto_never_exceeds_known_window(self):
        # The regression the maintainer caught: a small window must not be
        # handed a target larger than it can hold.
        for cl in (2048, 4096, 6000, 7000, 8192, 12000):
            budget = resolve_input_budget(-1, context_length=cl)
            assert budget <= cl, f"ctx={cl}: budget {budget} exceeds window"

    def test_auto_tiny_window_collapses_to_window(self):
        # Below the floor, auto matches the legacy min(context_length, floor).
        assert resolve_input_budget(-1, context_length=4096) == 4096
        assert resolve_input_budget(-1, context_length=2048) == 2048

    def test_auto_floor_applies_only_when_it_fits(self):
        # 6000-token window: 0.75*6000=4500 < floor, and floor fits → floor.
        assert resolve_input_budget(-1, context_length=6000) == AUTO_BUDGET_FLOOR
        # 7000-token window: scaled 5250 < floor 6000, floor fits → 6000.
        assert resolve_input_budget(-1, context_length=7000) == AUTO_BUDGET_FLOOR

    def test_auto_unknown_window_falls_back_to_floor(self):
        assert resolve_input_budget(-1, context_length=0) == AUTO_BUDGET_FLOOR

    def test_auto_leaves_response_headroom(self):
        # With a comfortable window the target stays a fraction below it, and
        # trim_for_context reserves reserve_tokens from there downstream.
        budget = resolve_input_budget(-1, context_length=8192, reserve_tokens=2048)
        assert budget < 8192
        assert budget == int(8192 * 0.75)
        assert budget < 8192


class TestSelfSummaryPrompt:
    def test_contains_goal_section(self):
        assert "### User Goal" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_what_was_done_section(self):
        assert "### What Was Done" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_current_state_section(self):
        assert "### Current State" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_pending_section(self):
        assert "### Pending / Next Steps" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_contains_key_context_section(self):
        assert "### Key Context" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_count_placeholder(self):
        assert "{count}" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_n_placeholder(self):
        assert "{n}" in SELF_SUMMARY_SYSTEM_PROMPT

    def test_mentions_compactions(self):
        assert "Compactions so far" in SELF_SUMMARY_SYSTEM_PROMPT


class TestTrimForContext:
    def test_keeps_current_large_user_message_by_truncating(self):
        huge = "A" * 20000
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": huge},
        ]

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        user_msgs = [m for m in trimmed if m.get("role") == "user"]
        assert len(user_msgs) == 1
        content = user_msgs[0]["content"]
        assert "pasted message was too large" in content
        assert content.startswith("A")
        assert len(content) < len(huge)

    def test_drops_older_messages_before_latest_user_paste(self):
        huge = "B" * 12000
        messages = [{"role": "system", "content": "You are helpful."}]
        messages.extend({"role": "user", "content": f"old-{i} " + ("x" * 1000)} for i in range(8))
        messages.append({"role": "user", "content": huge})

        trimmed = trim_for_context(messages, context_length=2048, reserve_tokens=512)

        assert trimmed[-1]["role"] == "user"
        assert "pasted message was too large" in trimmed[-1]["content"]
        assert "old-0" not in "\n".join(str(m.get("content", "")) for m in trimmed)
