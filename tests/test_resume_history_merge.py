from core.models import ChatMessage
from routes.history.history_routes import _has_immediate_continue_marker


def test_short_resume_control_is_a_merge_marker():
    messages = [
        ChatMessage("assistant", "partial", {"stopped": True}),
        ChatMessage("user", "Continue from where you left off."),
        ChatMessage("assistant", "finished"),
    ]
    assert _has_immediate_continue_marker(messages, 0, 2)
