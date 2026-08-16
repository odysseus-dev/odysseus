"""Regression for idle UI polls that must not count as foreground activity."""

from src.interactive_gate import should_track_interactive_request


def test_email_unread_state_is_passive_like_urgency_state():
    assert should_track_interactive_request("/api/email/urgency-state") is False
    assert should_track_interactive_request("/api/email/unread-state") is False


def test_real_interactive_paths_still_tracked():
    assert should_track_interactive_request("/api/chat_stream") is True
    assert should_track_interactive_request("/api/email/messages") is True
    assert should_track_interactive_request("/api/tasks", method="POST") is True


def test_options_never_tracked():
    assert should_track_interactive_request("/api/email/unread-state", method="OPTIONS") is False
    assert should_track_interactive_request("/api/chat_stream", method="OPTIONS") is False
