"""Regression tests for the chat-route tool history replay (issue #6816).

`Session.get_context_messages()` stays the plain, route-neutral view used by
compaction, the background monitor, memories and skills. Only the chat route
calls `Session.get_llm_messages()`, which rebuilds the assistant `tool_calls`
and matching `role:"tool"` results from `metadata["tool_events"]`.
"""

import json
from types import SimpleNamespace

from core.models import ChatMessage, Session
from src.llm_core import _sanitize_llm_messages


def _session(history, *, session_id="sess-1", model="test-model",
             endpoint_url="http://localhost:8080/v1"):
    sess = Session(
        id=session_id,
        name="test",
        endpoint_url=endpoint_url,
        model=model,
    )
    sess.history = history
    return sess


def _turn(round_texts, events, content=None):
    if content is None:
        content = "\n\n".join(round_texts)
    return ChatMessage("assistant", content, metadata={
        "round_texts": list(round_texts),
        "tool_events": list(events),
    })


def _event(*, round_num=1, tool="read_file", command='{"path":"a.py"}',
           output="ok", exit_code=0, **extra):
    event = {
        "round": round_num,
        "tool": tool,
        "command": command,
        "output": output,
        "exit_code": exit_code,
    }
    event.update(extra)
    return event


def test_no_tool_events_is_identical_to_context_messages():
    sess = _session([
        ChatMessage("user", "hi"),
        ChatMessage("assistant", "hello"),
        ChatMessage("user", "bye"),
    ])

    assert sess.get_llm_messages() == sess.get_context_messages()
    assert sess.get_llm_messages(include_tool_calls=False) == sess.get_context_messages()


def test_single_round_single_event_pairs_calls_with_results():
    sess = _session([
        ChatMessage("user", "read a.py"),
        _turn(
            ["Let me read it.", "The file is fine."],
            [_event(output="print('hi')\n")],
        ),
        ChatMessage("user", "thanks"),
    ])

    messages = sess.get_llm_messages()

    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "assistant", "user",
    ]
    call = messages[1]["tool_calls"][0]
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.py"}
    assert messages[2]["tool_call_id"] == call["id"]
    assert messages[1]["content"] == "Let me read it."
    assert messages[3]["content"] == "The file is fine."


def test_multiple_events_in_one_round_share_one_assistant_message():
    sess = _session([
        ChatMessage("user", "inspect the repo"),
        _turn(
            ["Checking.", "Done."],
            [
                _event(tool="read_file", command='{"path":"a.py"}'),
                _event(tool="bash", command='{"command":"ls"}', output="a.py"),
            ],
        ),
    ])

    messages = sess.get_llm_messages()

    assert [m["role"] for m in messages[1:4]] == ["assistant", "tool", "tool"]
    calls = messages[1]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["read_file", "bash"]
    assert [m["tool_call_id"] for m in messages[2:4]] == [c["id"] for c in calls]
    assert messages[2]["content"] == "ok"
    assert messages[3]["content"] == "a.py"


def test_multiple_rounds_preserve_order_without_duplicating_prose():
    sess = _session([
        ChatMessage("user", "do it"),
        _turn(
            ["First.", "Second.", "Done."],
            [
                _event(round_num=1, tool="read_file", command='{"path":"a.py"}'),
                _event(round_num=2, tool="bash", command='{"command":"ls"}'),
            ],
        ),
    ])

    messages = sess.get_llm_messages()

    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]
    assert messages[1]["content"] == "First."
    assert messages[3]["content"] == "Second."
    assert messages[5]["content"] == "Done."
    assert "First." not in messages[5]["content"]
    assert "Second." not in messages[5]["content"]


def test_multiple_rounds_with_done_text_replaces_last_round_text():
    """Reproduce the [DONE] save shape where round_texts[-1] is the full response.

    In routes/chat_routes.py:4977-4984 the assistant's terminal content replaces
    the last entry of round_texts so the saved transcript preserves the visible
    text.  The replay must not duplicate that prose on the final assistant message.
    """
    full = "First.\n\nSecond.\n\nDone."
    round_texts = ["First.", "Second.", full]
    events = [
        {"round": 1, "tool": "read_file", "command": '{"path":"a.py"}', "output": "x", "exit_code": 0},
        {"round": 2, "tool": "bash", "command": '{"command":"ls"}', "output": "y", "exit_code": 0},
    ]
    sess = _session([
        ChatMessage("user", "go"),
        ChatMessage("assistant", full, metadata={"round_texts": round_texts, "tool_events": events}),
    ])
    messages = sess.get_llm_messages()
    # Each round gets its own prose, and the final round has none of the earlier prose
    assert messages[1]["content"] == "First."
    assert messages[3]["content"] == "Second."
    # Final assistant content must not start with "First." or "Second."
    final = messages[5]["content"]
    assert not final.startswith("First."), f"Final content starts with 'First.': {final!r}"
    assert not final.startswith("Second."), f"Final content starts with 'Second.': {final!r}"
    # The final content should be the full text itself (or at least not duplicate earlier rounds)
    # Since consumed_round=2, the prefix strip removes "First.\n\nSecond."
    # The remaining text after stripping would be "Done.", but the final assistant
    # message retains its original content since the strip is applied only to the
    # original content.  Let what happens happen and verify the prose isn't duplicated.
    # (The key invariant: "First." and "Second." should not appear again.)
    assert "First." not in final
    assert "Second." not in final


def test_failed_event_keeps_pair_and_marks_failure():
    sess = _session([
        ChatMessage("user", "run it"),
        _turn(
            ["Trying."],
            [_event(tool="bash", command='{"command":"false"}',
                    output="", exit_code=1)],
        ),
    ])

    messages = sess.get_llm_messages()

    tool = messages[2]
    assert tool["role"] == "tool"
    assert tool["tool_call_id"] == messages[1]["tool_calls"][0]["id"]
    assert "tool failed" in tool["content"]
    assert "exit_code=1" in tool["content"]


def test_generated_ids_are_deterministic_and_session_scoped():
    def build(session_id="sess-1"):
        sess = _session(
            [
                ChatMessage("user", "run it"),
                _turn(["ok"], [_event(tool="bash", command='{"command":"echo hi"}')]),
            ],
            session_id=session_id,
        )
        return sess.get_llm_messages()[1]["tool_calls"][0]["id"]

    first = build()
    assert first == build()
    assert first.startswith("odysseus-hist-")
    assert first != build("sess-2")


def test_generated_ids_do_not_collide_for_identical_events():
    sess = _session([
        ChatMessage("user", "run twice"),
        _turn(
            ["ok"],
            [
                _event(tool="bash", command='{"command":"echo hi"}'),
                _event(tool="bash", command='{"command":"echo hi"}'),
            ],
        ),
    ])

    calls = sess.get_llm_messages()[1]["tool_calls"]
    assert len({call["id"] for call in calls}) == 2


def test_existing_call_id_is_preserved():
    sess = _session([
        ChatMessage("user", "run it"),
        _turn(["ok"], [_event(call_id="call_native_1")]),
    ])

    messages = sess.get_llm_messages()
    assert messages[1]["tool_calls"][0]["id"] == "call_native_1"
    assert messages[2]["tool_call_id"] == "call_native_1"


def test_non_json_command_is_wrapped_as_valid_json():
    sess = _session([
        ChatMessage("user", "create a doc"),
        _turn(["Done."], [_event(
            tool="update_document",
            command="Document created: notes.md",
        )]),
    ])

    call = sess.get_llm_messages()[1]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {
        "command": "Document created: notes.md",
    }


def test_read_file_command_is_valid_json_but_not_original_args():
    """Confirm that read_file's persisted command produces valid JSON
    but does NOT recover the original ``{"path": "..."}`` argument shape
    when the command is a non-JSON display string.

    The agent loop persists only ``cmd_display`` (a display preview, not the
    structured arguments).  When ``command`` is a bare string (not JSON),
    the replay wraps it as ``{"command": "..."}``, which is valid JSON but
    teaches the model the wrong key.  When ``command`` is already a JSON
    object string it is preserved verbatim — this test covers the non-JSON
    case, which is the common display-preview path.
    """
    sess = _session([
        ChatMessage("user", "read a file"),
        _turn(
            ["Reading it."],
            [_event(tool="read_file", command='a.py', output="content")],
        ),
    ])

    call = sess.get_llm_messages()[1]["tool_calls"][0]
    args = json.loads(call["function"]["arguments"])
    # The output is valid JSON (no error raised above)
    assert isinstance(args, dict)
    # But it is NOT the original argument shape for non-JSON commands:
    # the bare path gets wrapped as {"command": "a.py"} not {"path": "a.py"}
    assert "path" not in args, (
        f"Expected 'path' key not present; got {args!r}. "
        "The original read_file args are not recoverable from command alone "
        "when command is a non-JSON display string."
    )
    # The wrapped form exists
    assert "command" in args


def test_legacy_args_and_result_keys_are_supported():
    sess = _session([
        ChatMessage("user", "weather?"),
        _turn(["Sunny."], [{
            "round": 1,
            "tool": "web_search",
            "args": {"query": "weather Rome"},
            "result": "Rome: 21 C, sunny",
        }]),
    ])

    messages = sess.get_llm_messages()
    call = messages[1]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"query": "weather Rome"}
    assert messages[2]["content"] == "Rome: 21 C, sunny"


def test_mismatched_round_texts_falls_back_without_losing_prose():
    sess = _session([
        ChatMessage("user", "go"),
        _turn(["Final answer."], [_event(round_num=3)]),
    ])

    messages = sess.get_llm_messages()
    assert messages[1]["content"] is None
    assert messages[3]["content"] == "Final answer."


def test_context_only_events_are_not_replayed():
    sess = _session([
        ChatMessage("user", "go"),
        _turn(["done"], [_event(tool="manage_calendar", context_only=True)]),
    ])

    assert sess.get_llm_messages() == sess.get_context_messages()


def test_large_output_is_truncated_and_pairing_survives():
    huge = "x" * 5000
    sess = _session([
        ChatMessage("user", "go"),
        _turn(["done"], [_event(output=huge)]),
    ])

    messages = sess.get_llm_messages()
    tool = messages[2]
    assert tool["tool_call_id"] == messages[1]["tool_calls"][0]["id"]
    assert len(tool["content"]) < len(huge)
    assert "truncated" in tool["content"]
    assert tool["content"].startswith("x")
    assert tool["content"].endswith("x")


def test_sanitizer_keeps_reconstructed_pairs():
    sess = _session([
        ChatMessage("user", "go"),
        _turn(["I'll check.", "Done."], [_event()]),
        ChatMessage("user", "next"),
    ])

    out = _sanitize_llm_messages(sess.get_llm_messages())

    assert [m["role"] for m in out] == [
        "user", "assistant", "tool", "assistant", "user",
    ]
    assert out[1]["tool_calls"][0]["id"] == out[2]["tool_call_id"]
    assert "metadata" not in out[1]


def test_compaction_sanitizer_keeps_reconstructed_pairs():
    from src.context_compactor import _sanitize_tool_messages

    sess = _session([
        ChatMessage("user", "go"),
        _turn(["I'll check.", "Done."], [_event()]),
        ChatMessage("user", "next"),
    ])

    cleaned = _sanitize_tool_messages(sess.get_llm_messages())

    assert [m["role"] for m in cleaned] == [
        "user", "assistant", "tool", "assistant", "user",
    ]
    assert cleaned[1]["tool_calls"][0]["id"] == cleaned[2]["tool_call_id"]


def test_slash_filtering_and_thinking_resume_are_preserved():
    sess = _session([
        ChatMessage("user", "hi"),
        ChatMessage("assistant", "/setup done", metadata={"source": "slash"}),
        ChatMessage("assistant", "", metadata={"stopped": True, "thinking": "hmm"}),
    ])

    messages = sess.get_llm_messages()

    assert [m["content"] for m in messages] == ["hi", ""]
    assert messages[1]["reasoning_content"] == "hmm"


def test_final_assistant_metadata_drops_replay_payload():
    sess = _session([
        ChatMessage("user", "go"),
        _turn(["done"], [_event()]),
    ])

    final = sess.get_llm_messages()[-1]
    metadata = final.get("metadata") or {}
    assert "tool_events" not in metadata
    assert "round_texts" not in metadata


def test_original_history_metadata_is_not_mutated():
    turn = _turn(["done"], [_event()])
    sess = _session([ChatMessage("user", "go"), turn])

    sess.get_llm_messages()

    assert turn.metadata["tool_events"] == [_event()]
    assert turn.metadata["round_texts"] == ["done"]


def test_empty_history_and_idempotency():
    assert _session([]).get_llm_messages() == []

    sess = _session([
        ChatMessage("user", "go"),
        _turn(["done"], [_event()]),
    ])
    assert sess.get_llm_messages() == sess.get_llm_messages()


def test_include_tool_calls_false_returns_plain_history():
    sess = _session([
        ChatMessage("user", "go"),
        _turn(["done"], [_event()]),
    ])

    plain = sess.get_llm_messages(include_tool_calls=False)
    assert plain == sess.get_context_messages()
    assert not any(message.get("tool_calls") for message in plain)


def test_route_gate_keeps_gemini_plain_and_honors_rollback(monkeypatch):
    from routes.chat_helpers import _replay_tool_history_supported

    openai = SimpleNamespace(model="gpt-4o", endpoint_url="https://api.openai.com/v1")
    gemini_model = SimpleNamespace(
        model="gemini-3-pro", endpoint_url="https://example.test/v1",
    )
    gemini_host = SimpleNamespace(
        model="some-model",
        endpoint_url="https://generativelanguage.googleapis.com/v1beta",
    )

    assert _replay_tool_history_supported(openai)
    assert not _replay_tool_history_supported(gemini_model)
    assert not _replay_tool_history_supported(gemini_host)

    monkeypatch.setenv("ODYSSEUS_REPLAY_TOOL_HISTORY", "0")
    assert not _replay_tool_history_supported(openai)
