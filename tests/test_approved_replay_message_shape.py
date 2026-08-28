"""Regression coverage for the assistant turn an approved-action replay appends.

Anthropic's Messages API rejects a non-final assistant message whose content is
empty, so the resumed turn after a tool approval used to fail before the model
ever saw the sealed result. The replay injects its result with no assistant
prose for that round, which is the only path that produced such a turn.
"""

import src.llm_core as llm_core
from src.agent_loop import _append_tool_results

_RESULT = "bash: ok\nhello"
_RECORD = {
    "tool_name": "bash",
    "content": "printf hello",
    "result": {"output": "hello", "exit_code": 0},
    "text": _RESULT,
}


def _replay_messages(round_response="", round_reasoning=""):
    """Mirror the approved-action injection in stream_agent_loop."""
    messages = [
        {"role": "system", "content": "system preface"},
        {"role": "user", "content": "run the command and summarise it"},
    ]
    _append_tool_results(
        messages,
        round_response,
        [],
        [_RESULT],
        [_RESULT],
        False,
        0,
        round_reasoning=round_reasoning,
        tool_result_records=[_RECORD],
    )
    return messages


def _empty_assistant_turns(messages):
    return [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant"
        and not str(message.get("content") or "").strip()
        and not message.get("tool_calls")
    ]


def test_replay_appends_no_empty_assistant_turn():
    assert _empty_assistant_turns(_replay_messages()) == []


def test_replay_payload_is_a_single_non_empty_user_turn_for_anthropic():
    # Asserting the whole sequence rather than scanning a slice: once the empty
    # spacer is gone the payload is one message, so a "no offenders in
    # chat[:-1]" check would pass without inspecting anything.
    sanitized = llm_core._sanitize_llm_messages(_replay_messages())
    payload = llm_core._build_anthropic_payload(
        "claude-sonnet-5", sanitized, 0.2, 512
    )
    chat = payload["messages"]
    assert [message["role"] for message in chat] == ["user"]
    assert all(str(message.get("content") or "").strip() for message in chat)


def test_replay_merges_tool_output_after_the_request_with_its_fence_intact():
    # Dropping the empty assistant turn leaves two adjacent user messages, which
    # the sanitizer merges. Pin that shape: the tool output must still sit
    # behind its untrusted fence and must not precede the operator's request.
    sanitized = llm_core._sanitize_llm_messages(_replay_messages())
    user_turns = [m for m in sanitized if m.get("role") == "user"]
    assert len(user_turns) == 1
    merged = user_turns[0]["content"]
    assert merged.index("run the command") < merged.index("UNTRUSTED SOURCE DATA")
    assert merged.index("UNTRUSTED SOURCE DATA") < merged.index("hello")


def test_a_round_with_prose_still_appends_its_assistant_turn():
    messages = _replay_messages(round_response="running that now")
    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert [m["content"] for m in assistants] == ["running that now"]


def test_reasoning_only_round_keeps_its_carrier_and_its_known_empty_content():
    """Characterises the one empty-content turn this change deliberately leaves.

    A round with reasoning and no prose still appends its carrier, and that
    carrier's content is still "", which Anthropic would still reject. Dropping
    it would lose the reasoning DeepSeek thinking mode requires on the next
    request, and the approval replay never takes this path because it passes no
    reasoning. Left alone on purpose; this test makes the gap visible instead of
    silent, and should be updated by whoever closes it.
    """
    messages = _replay_messages(round_reasoning="thinking about it")
    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].get("reasoning_content") == "thinking about it"
    assert assistants[0]["content"] == ""
