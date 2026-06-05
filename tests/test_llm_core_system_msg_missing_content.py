"""Regression guard for #2350 — KeyError on missing 'content' key in system messages.

A system message dict that lacks a 'content' key (possible via malformed tool
results) previously raised KeyError in the hot path for llm_call,
llm_call_async, stream_llm, and _build_anthropic_payload. The fix is
m.get("content", "") in every spot that reads system message content.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from src.llm_core import _build_anthropic_payload


def _sys_msg_no_content():
    """A system message dict with no 'content' key — the crash trigger."""
    return {"role": "system"}


def _sys_msg_none_content():
    """A system message dict with content explicitly set to None."""
    return {"role": "system", "content": None}


def test_anthropic_payload_missing_content_key_does_not_crash():
    """_build_anthropic_payload must not KeyError on a contentless system message."""
    payload = _build_anthropic_payload(
        "claude-x",
        [_sys_msg_no_content(), {"role": "user", "content": "hello"}],
        0.7,
        100,
    )
    assert "messages" in payload


def test_anthropic_payload_none_content_does_not_crash():
    """content=None must also be handled gracefully (joined as empty string)."""
    payload = _build_anthropic_payload(
        "claude-x",
        [_sys_msg_none_content(), {"role": "user", "content": "hello"}],
        0.7,
        100,
    )
    assert "messages" in payload


def test_anthropic_payload_missing_content_produces_empty_system():
    """A missing 'content' should degrade to an empty string in the system block."""
    payload = _build_anthropic_payload(
        "claude-x",
        [_sys_msg_no_content(), {"role": "user", "content": "hello"}],
        0.7,
        100,
    )
    system_text = payload["system"][0]["text"]
    assert system_text == ""


def test_anthropic_payload_mixed_system_messages():
    """A mix of contentful and contentless system messages should join without crashing."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        _sys_msg_no_content(),
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "hi"},
    ]
    payload = _build_anthropic_payload("claude-x", messages, 0.7, 100)
    system_text = payload["system"][0]["text"]
    assert "You are helpful." in system_text
    assert "Be concise." in system_text
