"""Regression tests for #7113: automatic memory extraction cadence.

``run_post_response_tasks`` used to run memory extraction when the number of
saved history messages was a multiple of four. A normal turn saves two
messages, so that meant every second turn, but an approved tool action saves
three: the user message, the approval card, and the continuation's reply.
After one such exchange the count stayed odd, and with only ordinary turns
after it extraction never ran again in that chat. The cadence now counts the
user's turns instead.
"""
from types import SimpleNamespace

import pytest

import routes.chat_helpers as chat_helpers
from core.models import ChatMessage, Session
from src.prompt_security import untrusted_context_message


@pytest.fixture
def queued_jobs(monkeypatch):
    """Record the extraction jobs each post-response call queues."""
    queued = []
    monkeypatch.setattr(
        "services.memory.memory_extractor.extract_and_store",
        lambda *args, **kwargs: "memory-job",
    )
    monkeypatch.setattr(
        "src.task_endpoint.resolve_task_endpoint",
        lambda url, model, headers, owner=None: (url, model, headers),
    )
    monkeypatch.setattr(
        chat_helpers,
        "_run_extraction_jobs_sequentially",
        lambda session_id, jobs: queued.extend(name for name, _ in jobs),
    )
    monkeypatch.setattr(chat_helpers, "_spawn_bg", lambda job: None)
    monkeypatch.setattr(chat_helpers, "needs_auto_name", lambda name: False)
    return queued


def _new_session():
    return Session(
        id="sess-cadence",
        name="Cadence test",
        endpoint_url="http://localhost:1234/v1",
        model="test-model",
    )


def _finish_turn(sess, queued_jobs, **kwargs):
    """Run the post-response hook; return whether memory extraction was queued."""
    before = len(queued_jobs)
    chat_helpers.run_post_response_tasks(
        sess, SimpleNamespace(save_sessions=lambda: None), sess.id,
        "message", "reply", None, {"auto_memory": True},
        memory_manager=None, memory_vector=None, webhook_manager=None,
        **kwargs,
    )
    return "memory" in queued_jobs[before:]


def _chat_turn(sess, queued_jobs, text):
    sess.history.append(ChatMessage("user", text))
    sess.history.append(ChatMessage("assistant", f"Reply to {text}"))
    return _finish_turn(sess, queued_jobs)


def test_normal_chat_keeps_every_second_turn_cadence(queued_jobs):
    sess = _new_session()

    ran = [_chat_turn(sess, queued_jobs, f"turn {n}") for n in range(1, 9)]

    # Unchanged for ordinary chats: two saved messages per turn, so the old
    # "history length is a multiple of four" rule also fired on even turns.
    assert ran == [False, True, False, True, False, True, False, True]


def test_extraction_keeps_running_after_an_approved_tool_action(queued_jobs):
    sess = _new_session()
    assert _chat_turn(sess, queued_jobs, "turn 1") is False

    # Turn 2 stops at an approval card. Approving it resumes the run as a
    # continuation that saves another assistant message but no user message.
    sess.history.append(ChatMessage("user", "turn 2"))
    sess.history.append(ChatMessage("assistant", "", metadata={
        "tool_events": [{"ask_user": {"kind": "tool_approval", "approval_id": "approval-1"}}],
    }))
    assert _finish_turn(sess, queued_jobs) is True
    sess.history.append(ChatMessage("assistant", "Done."))
    assert _finish_turn(sess, queued_jobs, allow_background_extraction=False) is False
    assert len(sess.history) == 5

    ran = [_chat_turn(sess, queued_jobs, f"turn {n}") for n in range(3, 9)]

    assert ran == [False, True, False, True, False, True]


def test_injected_context_is_not_a_user_turn(queued_jobs):
    sess = _new_session()
    assert _chat_turn(sess, queued_jobs, "turn 1") is False

    # /api/inject_context stores untrusted source data with role "user".
    msg = untrusted_context_message("injected research context", "Research Context: notes")
    sess.history.append(ChatMessage(msg["role"], msg["content"], metadata=msg.get("metadata")))

    assert _chat_turn(sess, queued_jobs, "turn 2") is True
    assert _chat_turn(sess, queued_jobs, "turn 3") is False
    assert _chat_turn(sess, queued_jobs, "turn 4") is True
