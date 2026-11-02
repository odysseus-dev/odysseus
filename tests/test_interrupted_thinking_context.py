from core.models import ChatMessage, Session


def _session(history):
    return Session(
        id="resume-thinking",
        name="",
        endpoint_url="http://example.test/v1",
        model="thinking-model",
        history=history,
    )


def test_latest_interrupted_thinking_is_replayed_for_resume():
    session = _session([
        ChatMessage("user", "Solve this"),
        ChatMessage(
            "assistant",
            "",
            metadata={"stopped": True, "thinking": "partial chain"},
        ),
        ChatMessage("user", "Continue from where you left off."),
    ])

    messages = session.get_context_messages()
    assert messages[1]["reasoning_content"] == "partial chain"


def test_old_interrupted_thinking_is_not_replayed_past_newer_assistant_turn():
    session = _session([
        ChatMessage("assistant", "", metadata={"stopped": True, "thinking": "old chain"}),
        ChatMessage("user", "Try something else"),
        ChatMessage("assistant", "New answer"),
        ChatMessage("user", "Continue"),
    ])

    messages = session.get_context_messages()
    assert "reasoning_content" not in messages[0]
