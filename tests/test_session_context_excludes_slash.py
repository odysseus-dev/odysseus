"""Regression: slash-command / setup messages must not reach LLM context.

Slash replies (and the echoed `/setup ...` command) are persisted to history so
they render in the transcript, tagged ``metadata.source == "slash"``. They are
UI chatter the user never meant as conversation, so ``get_context_messages``
(the LLM-API view) must exclude them while the raw history keeps them for
display. See issue #2634.
"""

from core.models import Session, ChatMessage


def _session_with_slash():
    s = Session(id="s1", name="t", endpoint_url="http://x/v1", model="m")
    s.add_message(ChatMessage("user", "hi, give me a recipe"))
    s.add_message(ChatMessage("user", "/setup copilot", metadata={"source": "slash"}))
    s.add_message(ChatMessage("assistant", "Starting GitHub Copilot sign-in...", metadata={"source": "slash"}))
    s.add_message(ChatMessage("assistant", "Here is a recipe", metadata={"model": "m"}))
    return s


def test_context_excludes_slash_messages():
    ctx = _session_with_slash().get_context_messages()
    contents = [m["content"] for m in ctx]
    assert "hi, give me a recipe" in contents
    assert "Here is a recipe" in contents
    # Slash command + its status reply are filtered out of LLM context.
    assert "/setup copilot" not in contents
    assert all("sign-in" not in c for c in contents)
    assert len(ctx) == 2


def test_history_still_keeps_slash_messages_for_display():
    s = _session_with_slash()
    # Raw history (what the UI renders) is untouched.
    assert len(s.history) == 4
    assert any(m.content == "/setup copilot" for m in s.history)


def test_no_metadata_messages_are_kept():
    s = Session(id="s2", name="t", endpoint_url="http://x/v1", model="m")
    s.add_message(ChatMessage("user", "plain"))
    s.add_message(ChatMessage("assistant", "reply"))
    assert [m["content"] for m in s.get_context_messages()] == ["plain", "reply"]
