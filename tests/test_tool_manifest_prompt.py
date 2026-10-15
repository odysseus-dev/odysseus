"""A tool-manifest prompt must never be parsed as an end-user tool request.

Regression cover for the whats2api Conversation Analyzer cron runs that
persisted only the evidence-gate disclosure instead of the model's answer.

What happened: the analyzer's scheduled-task prompt is an instruction
document -- it names ``manage_memory``, ``manage_calendar``, ``manage_notes``,
``manage_tasks`` to *instruct* the agent, and mentions "recent" conversations
and a ``memory:`` output row. ``_parse_explicit_memory_lookup_request`` keys on
exactly those ordinary words, so it returned ``manage_memory list``. The agent
loop then force-injected that call every round:

    round 2  model: "No new conversation intervals."      <- correct answer
             loop:   normalized explicit qwen request to manage_memory
             loop:   the round text is dropped and replaced by the tool call
    round 3  empty round -> nudging tool call
    round 4  empty round -> manage_memory again (identical result)
    round 5  primary provider stalled 988s, fell back
    round 6  tools_sent=0, forced final answer, empty
             loop:   break with no text -> evidence-gate disclosure (142 chars)

Because a round with text but no tool calls is the loop's natural exit
(``if not tool_blocks: break``), injecting any tool block both erased the
answer and kept the loop spinning until an upstream stalled -- which is what
pinned one TaskRun ``running`` for 51 minutes and blew past the E2E poll.
"""

from __future__ import annotations

import pytest

import src.agent_loop as al
from src.llm_core import _first_token_timeout


# The live analyzer prompt, verbatim from the extension's default. Kept in the
# test rather than fetched so the regression is hermetic.
ANALYZER_PROMPT = """# WhatsApp Conversation Analyzer — system prompt

## Role
You are the WhatsApp Conversation Analyzer: a silent, periodic reviewer of the
operator's recent WhatsApp conversations. You run on a schedule; the operator
reviews each run as a normal chat session in folder "Tasks".

## Scope (every run)
- **You analyze NEW conversation intervals only.** Your FIRST call must be
  `mcp__oxs_whats2api_bridge__get_analysis_queue` (once, no arguments).
- If `new_intervals` is empty, output exactly `No new conversation intervals.`
  and call **zero** write tools — doing nothing is the correct outcome.

## Tools to use (priority order)
| Signal in chat                                 | Persist via        |
|------------------------------------------------|--------------------|
| Promise with a stated deadline                 | `manage_tasks`     |
| Future event with an unambiguous date/time     | `manage_calendar`  |
| Decision / context / person fact worth keeping | `manage_notes` or `manage_memory` |
| New contact / phone / handle                   | `manage_memory`    |

## Hard rules
1. **NEVER call** `send_message`, `send_to_session`, `bash`, `python`.

## Output (always — short and structured)
  - note: changed delivery address — saved
  - memory: contact João (11 9 9999-0000) — saved
  - event: meeting Thu 15:00 — already exists, skipped
- no action needed

If the queue is empty, output exactly `No new conversation intervals.`
"""


def test_analyzer_prompt_is_recognized_as_a_tool_manifest():
    assert al._is_tool_manifest_prompt(ANALYZER_PROMPT) is True


def test_analyzer_prompt_would_otherwise_parse_as_memory_lookup():
    """Pin the trap: the parser the loop relies on does fire on this prompt.

    If this ever stops matching, the guard is no longer load-bearing and the
    test below would pass for the wrong reason.
    """
    block = al._parse_explicit_memory_lookup_request(ANALYZER_PROMPT)
    assert block is not None
    assert block.tool_type == "manage_memory"
    assert block.content == "list"


@pytest.mark.parametrize(
    "text",
    [
        "What do you remember about me?",
        "list my memories",
        "show memories",
        "open memories",
        "latest memories",
        "search memories for timezone",
        "What does computer memory mean?",
        "add it to my list",
        "schedule a task for friday",
        "add a note",
        "",
    ],
)
def test_plain_user_requests_are_not_manifests(text):
    assert al._is_tool_manifest_prompt(text) is False


def test_plain_memory_requests_still_reach_the_intent_normalizer():
    """The guard must not disable legitimate end-user intent recovery."""
    block = al._parse_explicit_memory_lookup_request("What do you remember about me?")
    assert block is not None
    assert block.tool_type == "manage_memory"
    assert block.content == "list"


def test_single_tool_named_in_prose_is_not_a_manifest():
    # One tool reference can be an ordinary request ("remind me to call
    # manage_tasks later"); two or more is a manifest.
    assert al._is_tool_manifest_prompt("ping manage_tasks when the build lands") is False


def test_mcp_qualified_names_count_as_manifest_tokens():
    assert (
        al._is_tool_manifest_prompt(
            "Call mcp__oxs_whats2api_bridge__list_messages then "
            "mcp__oxs_whats2api_bridge__get_message."
        )
        is True
    )


def test_tool_prefixes_do_not_match_as_standalone_tokens():
    # `manage_memory_extra` must not register as `manage_memory`.
    assert al._is_tool_manifest_prompt("manage_memory_extra manage_notes_v2") is False


class TestFirstEventWatchdog:
    """A silent-but-accepted upstream must not pin a round open indefinitely."""

    def test_hosted_provider_gets_a_first_event_budget(self):
        assert _first_token_timeout("https://api.minimax.io/v1/chat/completions", 300) > 0

    def test_fallback_provider_gets_a_first_event_budget(self):
        assert _first_token_timeout("https://opencode.ai/zen/go/v1/chat/completions", 300) > 0

    def test_budget_is_clamped_by_the_stream_timeout(self):
        assert _first_token_timeout("https://api.minimax.io/v1/chat/completions", 30) <= 30

    def test_local_endpoints_keep_their_tighter_budget(self):
        assert _first_token_timeout("http://127.0.0.1:1234/v1/chat/completions", 300) == 60.0


class TestManifestIsNotAContinuation:
    """A manifest prompt carries its own contract; it continues nothing.

    The analyzer prompt matched ``_ACTION_CONTINUATION_RE`` on its own rule
    text — "Only read/write data belonging to **this** operator" parses as
    ``read`` ... ``this`` — so the turn was classified as a follow-up and the
    retrieval query was replaced by truncated recent context instead of the
    prompt that actually describes the job.
    """

    @staticmethod
    def _scheduler_messages(prompt: str) -> list[dict]:
        # Shape task_scheduler builds: system, datetime context, task prompt.
        return [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "The current date is Monday, October 12, 2026."},
            {"role": "user", "content": prompt},
        ]

    def test_analyzer_prompt_is_not_a_continuation(self):
        intent = al._classify_agent_request(self._scheduler_messages(ANALYZER_PROMPT), ANALYZER_PROMPT)
        assert intent["continuation"] is False

    def test_retrieval_query_is_the_whole_prompt(self):
        intent = al._classify_agent_request(self._scheduler_messages(ANALYZER_PROMPT), ANALYZER_PROMPT)
        assert intent["retrieval_query"] == ANALYZER_PROMPT.strip()

    def test_real_followups_are_still_continuations(self):
        history = [
            {"role": "user", "content": "add a meeting"},
            {"role": "assistant", "content": "Sure, add it for"},
            {"role": "user", "content": "do it"},
        ]
        assert al._classify_agent_request(history, "do it")["continuation"] is True

    def test_real_pronoun_followups_are_still_continuations(self):
        history = [
            {"role": "user", "content": "book the flight"},
            {"role": "assistant", "content": "Booked. Want to move it?"},
            {"role": "user", "content": "move it to friday"},
        ]
        assert al._classify_agent_request(history, "move it to friday")["continuation"] is True