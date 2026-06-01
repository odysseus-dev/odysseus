"""Integration tests: concurrent chat sessions must not leak.

These tests verify that the async streaming chat path maintains session
isolation even under concurrent access patterns.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from core.models import Session, ChatMessage
from core.session_manager import SessionManager


@pytest.mark.asyncio
async def test_concurrent_sessions_have_independent_history():
    """Simulating concurrent message adds to different sessions."""
    sm = SessionManager()
    sm.sessions = {}  # Bypass DB load

    s1 = Session(id="sess-a", name="Chat A", endpoint_url="http://ep", model="model-a")
    s2 = Session(id="sess-b", name="Chat B", endpoint_url="http://ep", model="model-b")
    sm.sessions["sess-a"] = s1
    sm.sessions["sess-b"] = s2

    async def add_to_session(sid, msgs):
        sess = sm.sessions[sid]
        for role, content in msgs:
            sess.add_message(ChatMessage(role, content))

    # Simulate concurrent adds
    await asyncio.gather(
        add_to_session("sess-a", [("user", "hello from A"), ("assistant", "reply A")]),
        add_to_session("sess-b", [("user", "hello from B")]),
    )

    a = sm.sessions["sess-a"]
    b = sm.sessions["sess-b"]

    assert len(a.history) == 2, f"Session A has {len(a.history)} messages, expected 2"
    assert len(b.history) == 1, f"Session B has {len(b.history)} messages, expected 1"
    assert b.history[0].content == "hello from B"


@pytest.mark.asyncio
async def test_concurrent_add_message_does_not_cross_contaminate():
    """Concurrent add_message calls must not write to each other's sessions."""
    sm = SessionManager()
    sm.sessions = {}

    s1 = Session(id="a", name="A", endpoint_url="http://ep", model="m1")
    s2 = Session(id="b", name="B", endpoint_url="http://ep", model="m2")
    sm.sessions["a"] = s1
    sm.sessions["b"] = s2

    async def rapid_add(sid, count):
        sess = sm.sessions[sid]
        for i in range(count):
            sess.add_message(ChatMessage("user", f"msg_{i}_from_{sid}"))

    await asyncio.gather(
        rapid_add("a", 5),
        rapid_add("b", 5),
        rapid_add("a", 3),  # More adds to A
    )

    a = sm.sessions["a"]
    b = sm.sessions["b"]

    assert len(a.history) == 8, f"Session A has {len(a.history)} messages"
    assert len(b.history) == 5, f"Session B has {len(b.history)} messages"
    # Verify B's messages are purely from B
    for msg in b.history:
        assert msg.content.endswith("_from_b"), f"Session B has cross-contaminated: {msg.content}"


@pytest.mark.asyncio
async def test_concurrent_read_write_isolation():
    """Reading one session while writing to another must return correct data."""
    sm = SessionManager()
    sm.sessions = {}

    s1 = Session(id="reader", name="Reader", endpoint_url="http://ep", model="m")
    s2 = Session(id="writer", name="Writer", endpoint_url="http://ep", model="m")
    sm.sessions["reader"] = s1
    sm.sessions["writer"] = s2

    # Pre-populate reader
    s1.add_message(ChatMessage("user", "original"))

    async def read_and_check():
        for _ in range(20):
            sess = sm.sessions["reader"]
            hist = sess.get_context_messages()
            # Should never see writer's messages
            for msg in hist:
                assert "writer_data" not in msg.get("content", ""), "Reader saw writer data!"

    async def write_to_writer():
        for i in range(20):
            sm.sessions["writer"].add_message(ChatMessage("user", f"writer_data_{i}"))

    await asyncio.gather(read_and_check(), write_to_writer())

    # Final state check
    reader = sm.sessions["reader"]
    writer = sm.sessions["writer"]
    assert len(reader.history) == 1, "Reader history mutated!"
    assert len(writer.history) == 20, f"Writer has {len(writer.history)} messages"
