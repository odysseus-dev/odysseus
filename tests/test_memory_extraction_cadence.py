"""Regression tests for #7113: automatic memory extraction cadence across odd message counts."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.models import ChatMessage, Session
from routes import chat_helpers


def _make_session(roles: list[str]) -> Session:
    sess = Session(
        id="sess-7113",
        name="Test Session",
        endpoint_url="http://localhost:11434/v1",
        model="test-model",
    )
    sess.history = [ChatMessage(role=r, content=f"msg-{i}") for i, r in enumerate(roles)]
    return sess


@pytest.mark.parametrize(
    ("roles", "expected_triggered"),
    [
        # Turn 1 (normal 2 messages): 1 user turn -> do not extract yet
        (["user", "assistant"], False),
        # Turn 2 (normal 4 messages): 2 user turns -> extract
        (["user", "assistant", "user", "assistant"], True),
        # Turn 1 saved 3 messages (e.g. tool approval card + user + assistant): 1 user turn -> not yet
        (["user", "assistant", "assistant"], False),
        # Turn 2 after an odd-message turn (5 messages total): 2 user turns -> MUST extract (#7113)
        (["user", "assistant", "assistant", "user", "assistant"], True),
        # Turn 3 after an odd-message turn (7 messages total): 3 user turns -> skip
        (["user", "assistant", "assistant", "user", "assistant", "user", "assistant"], False),
        # Turn 4 after an odd-message turn (9 messages total): 4 user turns -> MUST extract (#7113)
        (
            [
                "user", "assistant", "assistant",
                "user", "assistant",
                "user", "assistant",
                "user", "assistant",
            ],
            True,
        ),
    ],
)
def test_memory_extraction_triggers_every_two_user_turns_even_with_odd_history(
    monkeypatch, roles, expected_triggered
):
    sess = _make_session(roles)
    queued: list[object] = []

    def fake_extract_and_store(*args, **kwargs):
        return "mock-extract-coro"

    def fake_spawn_bg(coro):
        queued.append(coro)
        coro.close()

    monkeypatch.setattr("services.memory.memory_extractor.extract_and_store", fake_extract_and_store)
    monkeypatch.setattr(
        "src.task_endpoint.resolve_task_endpoint",
        lambda url, model, headers, owner=None: (url, model, headers or {}),
    )
    monkeypatch.setattr(chat_helpers, "_spawn_bg", fake_spawn_bg)

    chat_helpers.run_post_response_tasks(
        sess,
        MagicMock(),
        sess.id,
        "hello",
        "world",
        None,
        {"auto_memory": True, "auto_skills": False},
        MagicMock(),
        MagicMock(),
        None,
        extract_skills=False,
    )

    assert bool(queued) is expected_triggered
