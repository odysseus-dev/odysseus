"""Unit tests for the Odysseus CLI pure-logic pieces (no model required)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from odysseus_cli.config import (  # noqa: E402
    APPROVAL_ASK, APPROVAL_AUTO, CliConfig, to_chat_completions_url,
)


# ── URL normalization ──────────────────────────────────────────────────────
@pytest.mark.parametrize("base,expected", [
    ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
    ("http://localhost:11434/", "http://localhost:11434/v1/chat/completions"),
    ("http://localhost:11434/v1", "http://localhost:11434/v1/chat/completions"),
    ("http://localhost:11434/v1/", "http://localhost:11434/v1/chat/completions"),
    ("http://h:8000/v1/chat/completions", "http://h:8000/v1/chat/completions"),
])
def test_to_chat_completions_url(base, expected):
    assert to_chat_completions_url(base) == expected


# ── Config immutability + overrides ────────────────────────────────────────
def test_config_with_overrides_is_immutable():
    base = CliConfig(model="original-model")
    updated = base.with_overrides(model="new-model")
    assert updated.model == "new-model"
    assert base.model == "original-model"  # original unchanged
    assert updated is not base


def test_config_with_overrides_ignores_none():
    base = CliConfig(model="keep-me")
    updated = base.with_overrides(model=None, approval=APPROVAL_AUTO)
    assert updated.model == "keep-me"
    assert updated.approval == APPROVAL_AUTO


# ── SSE parsing ────────────────────────────────────────────────────────────
def test_parse_sse_delta_and_done():
    from odysseus_cli.agent import _parse_sse
    events = list(_parse_sse('data: {"delta": "hi"}\n\ndata: [DONE]\n\n'))
    assert events[0] == {"delta": "hi"}
    assert events[-1] == "[DONE]"


def test_parse_sse_skips_garbage():
    from odysseus_cli.agent import _parse_sse
    events = list(_parse_sse("event: error\ndata: not-json\n\n"))
    assert events == []  # non-JSON data lines are skipped, not crashed on


# ── Approval gate ──────────────────────────────────────────────────────────
def test_denied_result_shape():
    from odysseus_cli.approval import _denied_result
    desc, result = _denied_result("bash")
    assert "denied" in desc
    assert "error" in result and result["exit_code"] == 1


def test_approval_state_always_grant():
    from odysseus_cli.approval import ApprovalState
    state = ApprovalState(APPROVAL_ASK)
    assert not state.always_allowed("bash")
    state.grant_always("bash")
    assert state.always_allowed("bash")


def test_mutating_tools_membership():
    """The set of gated tools must cover shell + file writes."""
    from odysseus_cli.config import MUTATING_TOOLS
    assert {"bash", "python", "write_file"} <= MUTATING_TOOLS
    assert "read_file" not in MUTATING_TOOLS  # read-only must never be gated


def test_build_project_context_mentions_root(tmp_path):
    from odysseus_cli.agent import build_project_context
    ctx = build_project_context(tmp_path)
    assert str(tmp_path) in ctx
    assert "bash" in ctx.lower()
