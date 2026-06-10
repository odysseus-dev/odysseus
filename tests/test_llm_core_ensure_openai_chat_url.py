"""Regression: defensive URL normalization for OpenAI-compat chats (LANE-ODYSSEUS-OPENROUTER-CHATFIX-V1).

Before the fix, a session whose `endpoint_url` was the BASE URL
(e.g. ``https://openrouter.ai/api/v1``) — recorded by the form-based
session-create handler when the frontend didn't supply an ``endpoint_id`` —
would POST the chat to the provider's website HTML page. The HTTP
response was 200 with an HTML body, ``r.json()`` raised
``JSONDecodeError``, and the user saw a 500 with an empty body. The
``llm_call`` / ``llm_call_async`` / ``stream_llm`` paths now run the URL
through ``_ensure_openai_chat_url`` which appends ``/chat/completions``
when missing, and is a no-op when already present. These tests pin the
contract.
"""
import pytest

from src.llm_core import _ensure_openai_chat_url


@pytest.mark.parametrize("bad,good", [
    # The 6/10 incident: base URL persisted, no chat suffix.
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
    # Trailing slash on the base must not block the append.
    ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1/chat/completions"),
    # OpenAI base (no /v1/chat/completions yet).
    ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
    # Together.xyz style.
    ("https://api.together.xyz/v1", "https://api.together.xyz/v1/chat/completions"),
    # Local proxy.
    ("http://localhost:1234/v1", "http://localhost:1234/v1/chat/completions"),
])
def test_appends_chat_suffix_when_missing(bad, good):
    assert _ensure_openai_chat_url(bad) == good


@pytest.mark.parametrize("already", [
    "https://openrouter.ai/api/v1/chat/completions",
    "https://openrouter.ai/api/v1/chat/completions/",  # trailing slash
    "https://api.openai.com/v1/chat/completions",
    "https://api.deepseek.com/v1/chat/completions",
    # Legacy ``/completions`` suffix is also treated as a chat URL.
    "https://example.com/v1/completions",
    "https://example.com/v1/completions/",
])
def test_noop_when_chat_suffix_present(already):
    out = _ensure_openai_chat_url(already)
    # Trailing slashes are normalized away; the rest is preserved.
    assert out == already.rstrip("/")


def test_empty_and_none_passthrough():
    """Empty / None URLs should not blow up — the chat path will raise
    a clean HTTPException upstream if the URL is missing."""
    assert _ensure_openai_chat_url("") == ""
    assert _ensure_openai_chat_url(None) is None


def test_provider_branches_unaffected():
    """_ensure_openai_chat_url is for the OpenAI-compatible branch only.
    Anthropic's /v1/messages and Ollama's /api/chat are dispatched
    upstream by provider detection; if an anthropic URL slipped into the
    OpenAI-compat branch by accident, the helper would naively append
    /chat/completions. We pin the current behavior here so a future
    refactor that wants to handle cross-provider routing has a
    conversation starter."""
    out = _ensure_openai_chat_url("https://api.anthropic.com/v1/messages")
    # Currently: the helper sees no /chat/completions suffix and appends.
    # If you ever want it to recognize Anthropic natively, gate it on
    # _detect_provider(url) first.
    assert out.endswith("/chat/completions")
