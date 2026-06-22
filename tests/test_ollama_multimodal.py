"""Regression tests for Ollama-native multimodal image routing (issue #4723).

Odysseus builds user messages in OpenAI style::

    {"role": "user", "content": [
        {"type": "text", "text": "..."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]}

Native Ollama ``/api/chat`` does **not** accept a list for ``content``. It
expects ``content`` to be a string and images carried separately on
``images`` (a list of raw base64 strings, no ``data:`` prefix). Without
this conversion the image block silently never reaches the vision model —
the model reports "I can't see the image" even though it is vision-capable
and the request succeeded.
"""
from src import llm_core


def _multimodal_msg():
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this picture?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
        ],
    }


def test_ollama_payload_converts_openai_image_blocks_to_native_images_array():
    payload = llm_core._build_ollama_payload(
        "gemma4:e4b", [_multimodal_msg()], temperature=0.0, max_tokens=0,
    )
    msg = payload["messages"][0]
    # Content must be a string, not a list — native Ollama rejects lists.
    assert isinstance(msg["content"], str)
    assert "What is in this picture?" in msg["content"]
    # Base64 data extracted into the native images array (no data: prefix).
    assert msg["images"] == ["AAAA", "BBBB"]


def test_ollama_payload_skips_http_image_url():
    """Non-data-URI image_url values are skipped with a warning because
    native Ollama images[] accepts base64 only."""
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Look"},
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        ],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    out = payload["messages"][0]
    assert out["content"] == "Look"
    # HTTP URL is NOT added to images — Ollama cannot fetch it.
    assert "images" not in out


def test_ollama_payload_preserves_native_images_array():
    """If the caller already used Ollama's native shape, leave it alone."""
    msg = {
        "role": "user",
        "content": "Describe",
        "images": ["XXXX"],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    out = payload["messages"][0]
    assert out["content"] == "Describe"
    assert out["images"] == ["XXXX"]


def test_ollama_payload_merges_native_and_openai_images():
    """A message that carries both native ``images`` and OpenAI ``image_url``
    blocks (e.g. assembled by different code paths) must produce one combined
    list rather than drop either half."""
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Hi"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,OPENAI"}},
        ],
        "images": ["NATIVE"],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    out = payload["messages"][0]
    assert out["content"] == "Hi"
    assert out["images"] == ["NATIVE", "OPENAI"]


def test_ollama_payload_text_only_message_untouched():
    msgs = [{"role": "user", "content": "hello"}]
    payload = llm_core._build_ollama_payload("gemma4:e4b", msgs, temperature=0.0, max_tokens=0)
    assert payload["messages"][0] == {"role": "user", "content": "hello"}


def test_ollama_payload_string_content_with_only_image_block():
    """A message whose content list has only image_url blocks (no text part)
    still yields a non-empty content string so native Ollama accepts it."""
    msg = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
        ],
    }
    payload = llm_core._build_ollama_payload("gemma4:e4b", [msg], temperature=0.0, max_tokens=0)
    out = payload["messages"][0]
    assert isinstance(out["content"], str)
    assert out["images"] == ["QQ=="]
