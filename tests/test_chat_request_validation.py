import pytest
from pydantic import ValidationError

import src.chat_helpers as chat_helpers
from src.request_models import ChatRequest


class _HTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _SessionManager:
    def __init__(self, existing=None):
        self.existing = set(existing or {"s1"})
        self.seen = []

    def get_session(self, session_id):
        self.seen.append(session_id)
        if session_id not in self.existing:
            raise KeyError(session_id)
        return object()


def test_chat_request_trims_required_fields_and_attachments():
    req = ChatRequest(
        message="  hello  ",
        session="  s1  ",
        attachments=[" att-1 ", "att-2"],
        time_filter=" week ",
    )

    assert req.message == "hello"
    assert req.session == "s1"
    assert req.attachments == ["att-1", "att-2"]
    assert req.time_filter == "week"


def test_chat_request_rejects_whitespace_only_message():
    with pytest.raises(ValidationError):
        ChatRequest(message="   ", session="s1")


def test_chat_request_rejects_whitespace_only_session():
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", session="   ")


def test_chat_request_uses_independent_attachment_lists():
    first = ChatRequest(message="hello", session="s1")
    second = ChatRequest(message="hello", session="s2")

    first.attachments.append("att-1")

    assert second.attachments == []


def test_chat_request_keeps_invalid_time_filter_compatibility():
    req = ChatRequest(message="hello", session="s1", time_filter="decade")

    assert req.time_filter is None


def test_coerce_message_and_session_trims_session_before_lookup():
    session_manager = _SessionManager({"s1"})

    message, session = chat_helpers.coerce_message_and_session(
        None, " hello ", " s1 ", session_manager,
    )

    assert message == "hello"
    assert session == "s1"
    assert session_manager.seen == ["s1"]


def test_coerce_message_and_session_rejects_whitespace_session(monkeypatch):
    monkeypatch.setattr(chat_helpers, "HTTPException", _HTTPException)

    with pytest.raises(_HTTPException) as exc:
        chat_helpers.coerce_message_and_session(None, "hello", "   ", _SessionManager())

    assert exc.value.status_code == 400
    assert exc.value.detail["message"] == "Session ID is required"


def test_coerce_message_and_session_allows_attachment_only_empty_message():
    message, session = chat_helpers.coerce_message_and_session(
        None, "   ", " s1 ", _SessionManager({"s1"}), allow_empty=True,
    )

    assert message == ""
    assert session == "s1"
