"""Regression test for the task-path endpoint-URL normalization fix.

Bug: the task executor passed ``task.endpoint_url`` verbatim to the model HTTP
call (unlike the chat path, which normalizes via ``build_chat_url``). A bare
OpenAI-compatible base such as ``http://host:11434/v1`` POSTed to a 404 and the
run silently reported "The model returned an empty response".

The fix routes every resolved task endpoint through ``_normalize_chat_endpoint``.
"""
from src.task_scheduler import _normalize_chat_endpoint


def test_bare_v1_base_gets_chat_completions_suffix():
    # The exact failure case: a bare /v1 base must become a full chat URL.
    assert (
        _normalize_chat_endpoint("http://localhost:11434/v1")
        == "http://localhost:11434/v1/chat/completions"
    )


def test_full_chat_url_is_unchanged_idempotent():
    full = "http://localhost:11434/v1/chat/completions"
    assert _normalize_chat_endpoint(full) == full
    # Idempotent under repeated application.
    assert _normalize_chat_endpoint(_normalize_chat_endpoint(full)) == full


def test_native_ollama_url_left_alone():
    # Native Ollama (/api...) has its own downstream normalizer — don't touch it.
    assert _normalize_chat_endpoint("http://localhost:11434/api") == "http://localhost:11434/api"
    assert _normalize_chat_endpoint("http://localhost:11434/api/chat") == "http://localhost:11434/api/chat"


def test_empty_and_none_are_passthrough():
    assert _normalize_chat_endpoint("") == ""
    assert _normalize_chat_endpoint(None) is None


def test_trailing_slash_base_normalized():
    assert (
        _normalize_chat_endpoint("http://localhost:11434/v1/")
        == "http://localhost:11434/v1/chat/completions"
    )
