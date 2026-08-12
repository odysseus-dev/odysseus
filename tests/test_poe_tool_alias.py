"""Regression tests for Poe tool-name aliasing.

Validates that:
1. Tool declarations are aliased on the initial request.
2. Message history (assistant tool_calls + tool results) is re-aliased on
   follow-up rounds, without mutating the caller-owned messages.
"""

import copy
import pytest

from src.llm_core import _alias_poe_tools, _alias_poe_messages, _POE_TOOL_ALIASES


POE_URL = "https://api.poe.com/v1/chat/completions"
NON_POE_URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_tools():
    """Minimal tool declarations including conflicting names."""
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web",
                "parameters": {},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Run Python code",
                "parameters": {},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {},
            },
        },
    ]


@pytest.fixture
def sample_history():
    """Message history after one tool-using round (local names)."""
    return [
        {"role": "user", "content": "Search for the latest news"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_001",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "latest news"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_001",
            "name": "web_search",
            "content": "Here are the results...",
        },
        {"role": "user", "content": "Now run some python to parse that"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_002",
                    "type": "function",
                    "function": {"name": "python", "arguments": '{"code": "print(1)"}'},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_002",
            "name": "python",
            "content": "1",
        },
        {"role": "user", "content": "Thanks, summarize everything"},
    ]


# ---------------------------------------------------------------------------
# Tests: Tool declaration aliasing (round 1)
# ---------------------------------------------------------------------------


class TestToolDeclarationAliasing:
    def test_poe_declarations_are_aliased(self, sample_tools):
        aliased = _alias_poe_tools(sample_tools, POE_URL)
        names = [t["function"]["name"] for t in aliased]

        assert "odysseus_web_search" in names
        assert "odysseus_python" in names
        assert "web_search" not in names
        assert "python" not in names
        # Non-conflicting tools are untouched
        assert "read_file" in names

    def test_non_poe_declarations_unchanged(self, sample_tools):
        result = _alias_poe_tools(sample_tools, NON_POE_URL)
        names = [t["function"]["name"] for t in result]

        assert "web_search" in names
        assert "python" in names

    def test_declaration_aliasing_does_not_mutate_input(self, sample_tools):
        original = copy.deepcopy(sample_tools)
        _alias_poe_tools(sample_tools, POE_URL)

        assert sample_tools == original


# ---------------------------------------------------------------------------
# Tests: Message history aliasing (follow-up rounds)
# ---------------------------------------------------------------------------


class TestMessageHistoryAliasing:
    def test_poe_history_tool_calls_are_aliased(self, sample_history):
        aliased = _alias_poe_messages(sample_history, POE_URL)

        # Find assistant messages with tool_calls
        assistant_msgs = [m for m in aliased if m.get("tool_calls")]
        assert len(assistant_msgs) == 2

        assert (
            assistant_msgs[0]["tool_calls"][0]["function"]["name"]
            == "odysseus_web_search"
        )
        assert (
            assistant_msgs[1]["tool_calls"][0]["function"]["name"] == "odysseus_python"
        )

    def test_poe_history_tool_results_are_aliased(self, sample_history):
        aliased = _alias_poe_messages(sample_history, POE_URL)

        tool_msgs = [m for m in aliased if m.get("role") == "tool"]
        assert len(tool_msgs) == 2

        assert tool_msgs[0]["name"] == "odysseus_web_search"
        assert tool_msgs[1]["name"] == "odysseus_python"

    def test_poe_history_preserves_non_aliased_fields(self, sample_history):
        aliased = _alias_poe_messages(sample_history, POE_URL)

        tool_msgs = [m for m in aliased if m.get("role") == "tool"]
        assert tool_msgs[0]["tool_call_id"] == "call_001"
        assert tool_msgs[0]["content"] == "Here are the results..."
        assert tool_msgs[1]["tool_call_id"] == "call_002"
        assert tool_msgs[1]["content"] == "1"

    def test_poe_history_user_messages_unchanged(self, sample_history):
        aliased = _alias_poe_messages(sample_history, POE_URL)

        user_msgs = [m for m in aliased if m.get("role") == "user"]
        assert len(user_msgs) == 3
        assert user_msgs[0]["content"] == "Search for the latest news"

    def test_non_poe_history_unchanged(self, sample_history):
        result = _alias_poe_messages(sample_history, NON_POE_URL)
        assert result == sample_history

    def test_history_aliasing_does_not_mutate_input(self, sample_history):
        original = copy.deepcopy(sample_history)
        _alias_poe_messages(sample_history, POE_URL)

        assert sample_history == original, (
            "_alias_poe_messages must not mutate caller-owned messages"
        )


# ---------------------------------------------------------------------------
# Integration: Full round-trip scenario
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """Simulates building payloads for two consecutive Poe requests."""

    def test_second_round_payload_is_consistent(self, sample_tools, sample_history):
        """Declarations and history must use the same aliased names."""
        # Build payload as the real code would
        aliased_tools = _alias_poe_tools(sample_tools, POE_URL)
        aliased_messages = _alias_poe_messages(sample_history, POE_URL)

        declared_names = {t["function"]["name"] for t in aliased_tools}

        # Every tool name referenced in history must exist in declarations
        for msg in aliased_messages:
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    assert (
                        fn_name in declared_names or fn_name not in _POE_TOOL_ALIASES
                    ), (
                        f"Tool call '{fn_name}' in history not found in declarations {declared_names}"
                    )
            if msg.get("role") == "tool":
                tool_name = msg.get("name")
                assert (
                    tool_name in declared_names or tool_name not in _POE_TOOL_ALIASES
                ), (
                    f"Tool result name '{tool_name}' in history not found in declarations {declared_names}"
                )
