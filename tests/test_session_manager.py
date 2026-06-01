"""Tests for SessionManager — session isolation and data integrity.

These tests prove the chat context drifting bug (#135) exists and verify fixes.
Uses mocked DB to test in-memory session management logic in isolation.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch

# Mock SessionLocal INSIDE session_manager before any test code runs.
# session_manager.py does `from .database import SessionLocal` at module level,
# so we must patch the imported name where it's used, not the source module.
import core.session_manager as _csm
_csm.SessionLocal = MagicMock()

# Also mock the DB model classes
_csm.DbSession = MagicMock()
_csm.DbChatMessage = MagicMock()
_csm.DbDocument = MagicMock()

from core.session_manager import SessionManager
from core.models import Session, ChatMessage


@pytest.fixture
def sm():
    """SessionManager with a fresh in-memory store, no DB load."""
    manager = SessionManager()
    # Bypass DB load for unit tests — start with clean slate
    manager.sessions = {}
    return manager


class TestSessionIsolation:
    """PROVING THE BUG: Shared mutable history leaks between sessions."""

    def test_history_is_not_shared_between_sessions(self, sm):
        """Two sessions must have independent history lists."""
        s1 = sm.create_session("s1", "Chat A", "http://ep", "model-a")
        s2 = sm.create_session("s2", "Chat B", "http://ep", "model-b")

        s1.add_message(ChatMessage("user", "hello from A"))
        s2.add_message(ChatMessage("user", "hello from B"))

        assert len(s1.history) == 1, f"Session A has {len(s1.history)} messages"
        assert len(s2.history) == 1, f"Session B has {len(s2.history)} messages"
        assert s1.history[0].content == "hello from A"
        assert s2.history[0].content == "hello from B"

    def test_mutating_one_session_history_does_not_affect_another(self, sm):
        """Appending to one session must not add messages to another."""
        sm.create_session("s1", "Chat A", "http://ep", "model-a")
        sm.create_session("s2", "Chat B", "http://ep", "model-b")

        s1 = sm.sessions["s1"]
        s1.add_message(ChatMessage("user", "msg1"))
        s1.add_message(ChatMessage("assistant", "resp1"))

        # THIS FAILS ON CURRENT CODE — s2 sees s1's messages
        s2 = sm.sessions["s2"]
        assert len(s2.history) == 0, (
            f"Session B has {len(s2.history)} messages leaked from Session A"
        )

    def test_history_reference_immutable_after_add_message(self, sm):
        """Pre-existing references to .history must not see new messages after add_message."""
        sm.create_session("s1", "Test", "http://ep", "model")
        s1 = sm.sessions["s1"]
        s1.add_message(ChatMessage("user", "hi"))

        # Hold a reference to the current history list
        old_history_ref = s1.history

        s1.add_message(ChatMessage("user", "second message"))

        # old_history_ref should still have 1 item (immutable snapshot)
        assert len(old_history_ref) == 1, (
            f"Old history ref has {len(old_history_ref)} items, expected 1"
        )
        # Current history should have 2
        assert len(s1.history) == 2

    def test_get_session_returns_same_object_with_updated_state(self, sm):
        """get_session returns the cached object — state mutations via add_message are visible."""
        sm.create_session("s1", "Test", "http://ep", "model")
        s1 = sm.sessions["s1"]
        s1.add_message(ChatMessage("user", "hi"))

        retrieved = sm.get_session("s1")
        assert len(retrieved.history) == 1
        assert retrieved.history[0].content == "hi"


class TestSessionManagerEdgeCases:
    """Edge cases and latent bugs."""

    def test_persist_message_nonexistent_session_does_not_crash(self, sm):
        """_persist_message with non-existent session_id must not crash.
        
        Catches the `{}.history` AttributeError bug at line 207.
        """
        msg = ChatMessage("user", "orphan")
        try:
            sm._persist_message("nonexistent", msg)
        except AttributeError as e:
            pytest.fail(f"_persist_message crashed with AttributeError: {e}")
        except Exception:
            pass  # Other errors (DB-related) are acceptable without real DB

    def test_create_session_then_delete_then_get_raises(self, sm):
        """Deleting a session must remove it from the cache."""
        sm.create_session("s1", "ToDelete", "http://ep", "model")
        sm.delete_session("s1")
        assert "s1" not in sm.sessions

    def test_empty_session_isolation(self, sm):
        """Session created but no messages added must not pollute others."""
        sm.create_session("empty", "Empty", "http://ep", "model")
        sm.create_session("active", "Active", "http://ep", "model")

        active = sm.sessions["active"]
        active.add_message(ChatMessage("user", "first"))

        empty = sm.sessions["empty"]
        assert len(empty.history) == 0, (
            f"Empty session has {len(empty.history)} messages from active session"
        )

    def test_add_message_updates_message_count(self, sm):
        """add_message must correctly increment message_count."""
        s = sm.create_session("s1", "Test", "http://ep", "model")
        assert s.message_count == 0
        s.add_message(ChatMessage("user", "first"))
        assert s.message_count == 1
        s.add_message(ChatMessage("assistant", "reply"))
        assert s.message_count == 2

    def test_history_order_preserved(self, sm):
        """Messages must maintain insertion order."""
        s = sm.create_session("s1", "Test", "http://ep", "model")
        msgs = [
            ChatMessage("user", "q1"),
            ChatMessage("assistant", "a1"),
            ChatMessage("user", "q2"),
            ChatMessage("assistant", "a2"),
        ]
        for m in msgs:
            s.add_message(m)
        for i, expected in enumerate(msgs):
            assert s.history[i].role == expected.role
            assert s.history[i].content == expected.content

    def test_multiple_sessions_independent_counts(self, sm):
        """Multiple sessions must each track their own message counts."""
        s1 = sm.create_session("s1", "A", "http://ep", "m1")
        s2 = sm.create_session("s2", "B", "http://ep", "m2")
        s3 = sm.create_session("s3", "C", "http://ep", "m3")

        s1.add_message(ChatMessage("user", "a1"))
        s1.add_message(ChatMessage("user", "a2"))
        s2.add_message(ChatMessage("user", "b1"))

        assert s1.message_count == 2
        assert s2.message_count == 1
        assert s3.message_count == 0

    def test_get_context_messages_returns_copies(self, sm):
        """get_context_messages must not expose internal list for mutation."""
        s = sm.create_session("s1", "Test", "http://ep", "model")
        s.add_message(ChatMessage("user", "original"))

        ctx = s.get_context_messages()
        ctx.append({"role": "user", "content": "injected"})

        ctx2 = s.get_context_messages()
        assert len(ctx2) == 1, (
            f"get_context_messages leaked: {len(ctx2)} messages"
        )
