"""Tests for src/agent/inbox.py"""
from __future__ import annotations
import pytest
from src.agent.inbox import Inbox, InboxMessage


def test_inbox_message_creation():
    msg = InboxMessage(sender_id="explore-1", receiver_id="main", content="Found 5 files", type="actor_notification")
    assert msg.sender_id == "explore-1"
    assert msg.receiver_id == "main"
    assert msg.type == "actor_notification"


def test_inbox_send_and_receive():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="msg1", type="text"))
    messages = inbox.receive("b")
    assert len(messages) == 1
    assert messages[0].content == "msg1"


def test_inbox_receive_clears():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="msg1", type="text"))
    inbox.receive("b")
    messages = inbox.receive("b")
    assert len(messages) == 0


def test_inbox_receive_filtered():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="msg1", type="text"))
    inbox.send(InboxMessage(sender_id="c", receiver_id="b", content="msg2", type="actor_notification"))
    messages = inbox.receive("b", type_filter="actor_notification")
    assert len(messages) == 1
    assert messages[0].content == "msg2"


def test_inbox_send_notification():
    inbox = Inbox()
    inbox.send_notification(sender_id="explore-1", receiver_id="main", status="success", summary="Found all files")
    messages = inbox.receive("main")
    assert len(messages) == 1
    assert "success" in messages[0].content.lower()


def test_inbox_multiple_receivers():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="to b", type="text"))
    inbox.send(InboxMessage(sender_id="a", receiver_id="c", content="to c", type="text"))
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="to b again", type="text"))
    b_msgs = inbox.receive("b")
    c_msgs = inbox.receive("c")
    assert len(b_msgs) == 2
    assert len(c_msgs) == 1


def test_inbox_is_empty():
    inbox = Inbox()
    assert inbox.is_empty("main") is True
    inbox.send(InboxMessage(sender_id="a", receiver_id="main", content="msg", type="text"))
    assert inbox.is_empty("main") is False


def test_inbox_pending_count():
    inbox = Inbox()
    assert inbox.pending_count("main") == 0
    inbox.send(InboxMessage(sender_id="a", receiver_id="main", content="msg1", type="text"))
    inbox.send(InboxMessage(sender_id="a", receiver_id="main", content="msg2", type="text"))
    assert inbox.pending_count("main") == 2
