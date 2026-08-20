"""Unit and regression tests for the Thinking Mode / No-Think Mode toggle feature."""

import pytest
import re
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from routes.chat_helpers import (
    _clean_no_think_text,
    _clean_no_think_content,
    build_chat_context,
)
from src.request_models import ChatRequest
from src.text_helpers import strip_think

_REPO = Path(__file__).resolve().parent.parent


def test_clean_no_think_text():
    assert _clean_no_think_text("Hello /no_think") == "Hello"
    assert _clean_no_think_text("Hello\n/no_think") == "Hello"
    assert _clean_no_think_text("/no_think") == ""
    assert _clean_no_think_text("What is 2+2? /no_think") == "What is 2+2?"
    assert _clean_no_think_text("Normal message without flag") == "Normal message without flag"
    assert _clean_no_think_text(None) is None


def test_clean_no_think_content_multimodal():
    # String content
    assert _clean_no_think_content("Test /no_think") == "Test"
    
    # List of blocks
    blocks = [
        {"type": "text", "text": "Describe this image /no_think"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    ]
    cleaned = _clean_no_think_content(blocks)
    assert cleaned[0]["text"] == "Describe this image"
    assert cleaned[1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_build_chat_context_no_think_mode_appends_to_model_messages_and_cleans_history():
    # Mock session
    sess = MagicMock()
    sess.owner = "testuser"
    sess.model = "qwen3-7b"
    sess.endpoint_url = "http://localhost:11434/v1"
    sess.headers = {}
    sess.metadata = {}
    added_messages = []
    sess.add_message = lambda msg: added_messages.append(msg)
    sess.get_context_messages = lambda: [
        {"role": "user", "content": added_messages[-1].content if added_messages else ""}
    ]

    request = MagicMock()
    request.state = MagicMock()
    request.state.user = "testuser"
    request.cookies = {"user": "testuser"}
    request.headers = {}

    chat_handler = MagicMock()
    chat_handler.get_preset = lambda pid: None
    chat_handler.validate_and_extract_preset = lambda pid: (0.7, None, None, None)
    chat_handler.update_session_name_if_needed = lambda s, text: None
    chat_handler.preprocess_message = AsyncMock(return_value=(
        "Tell me a joke /no_think",
        "Tell me a joke /no_think",
        "Tell me a joke /no_think",
        [],
        None,
    ))

    chat_processor = MagicMock()
    chat_processor.build_context_preface = MagicMock(return_value=(
        [{"role": "system", "content": "You are a helpful assistant."}],
        [],
        [],
    ))

    ctx = await build_chat_context(
        sess=sess,
        request=request,
        chat_handler=chat_handler,
        chat_processor=chat_processor,
        message="Tell me a joke /no_think",
        session_id="session-123",
        thinking=False,
    )

    # 1. Saved session message in DB must be clean (no /no_think)
    assert len(added_messages) == 1
    assert added_messages[0].content == "Tell me a joke"
    assert "/no_think" not in added_messages[0].content

    # 2. Outgoing messages for LLM must have /no_think appended to the user turn
    user_msgs = [m for m in ctx.messages if m.get("role") == "user"]
    assert len(user_msgs) >= 1
    assert user_msgs[-1]["content"].endswith("/no_think")


@pytest.mark.asyncio
async def test_build_chat_context_thinking_on_preserves_clean_messages():
    sess = MagicMock()
    sess.owner = "testuser"
    sess.model = "qwen3-7b"
    sess.endpoint_url = "http://localhost:11434/v1"
    sess.headers = {}
    sess.metadata = {}
    added_messages = []
    sess.add_message = lambda msg: added_messages.append(msg)
    sess.get_context_messages = lambda: [
        {"role": "user", "content": added_messages[-1].content if added_messages else ""}
    ]

    request = MagicMock()
    request.state = MagicMock()
    request.state.user = "testuser"
    request.cookies = {"user": "testuser"}
    request.headers = {}

    chat_handler = MagicMock()
    chat_handler.get_preset = lambda pid: None
    chat_handler.validate_and_extract_preset = lambda pid: (0.7, None, None, None)
    chat_handler.update_session_name_if_needed = lambda s, text: None
    chat_handler.preprocess_message = AsyncMock(return_value=(
        "Explain gravity",
        "Explain gravity",
        "Explain gravity",
        [],
        None,
    ))

    chat_processor = MagicMock()
    chat_processor.build_context_preface = MagicMock(return_value=(
        [{"role": "system", "content": "You are a helpful assistant."}],
        [],
        [],
    ))

    ctx = await build_chat_context(
        sess=sess,
        request=request,
        chat_handler=chat_handler,
        chat_processor=chat_processor,
        message="Explain gravity",
        session_id="session-123",
        thinking=True,
    )

    # Saved message is clean
    assert added_messages[0].content == "Explain gravity"

    # Outgoing LLM message does NOT have /no_think
    user_msgs = [m for m in ctx.messages if m.get("role") == "user"]
    assert len(user_msgs) >= 1
    assert "/no_think" not in user_msgs[-1]["content"]


def test_strip_think_empty_tags():
    assert strip_think("<think></think>Clean answer.") == "Clean answer."
    assert strip_think("<think>\n</think>Clean answer.") == "Clean answer."
    assert strip_think("<think>   </think>Clean answer.") == "Clean answer."
    assert strip_think("<thought></thought>Clean answer.") == "Clean answer."
    assert strip_think("<thought>\n\n</thought>Clean answer.") == "Clean answer."


def test_chat_request_model_fields():
    req = ChatRequest(message="Hello", session="s1", thinking=False, no_think=True)
    assert req.thinking is False
    assert req.no_think is True

    req_default = ChatRequest(message="Hello", session="s1")
    assert req_default.thinking is None
    assert req_default.no_think is False


def test_ui_markup_contains_think_toggle():
    index_html = (_REPO / "static/index.html").read_text(encoding="utf-8")
    assert 'id="think-toggle-btn"' in index_html
    assert 'id="think-toggle"' in index_html
    assert 'title="Thinking mode"' in index_html


def test_app_js_contains_think_toggle_wiring():
    app_js = (_REPO / "static/app.js").read_text(encoding="utf-8")
    assert "initThinkToggle" in app_js
    assert "syncThinkToggle" in app_js
    assert "setThinkMode" in app_js
    assert "think-toggle-btn" in app_js


def test_chat_js_submits_thinking_state():
    chat_js = (_REPO / "static/js/chat.js").read_text(encoding="utf-8")
    assert "think-toggle" in chat_js
    assert "/no_think" in chat_js
    assert "fd.append('thinking'" in chat_js
