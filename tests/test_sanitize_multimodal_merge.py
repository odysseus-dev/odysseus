"""Regression: merging consecutive user messages must not str() multimodal content."""

from src.llm_core import _sanitize_llm_messages


def test_multimodal_user_message_keeps_image_block_when_merged():
    image_msg = {"role": "user", "content": [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}
    tool_result = {"role": "user", "content": "Tool result: 42"}
    out = _sanitize_llm_messages([image_msg, tool_result])

    # The two consecutive user messages collapse into one...
    assert len(out) == 1
    content = out[0]["content"]
    # ...and the image block survives (it used to be str()-ed into a repr).
    assert isinstance(content, list)
    assert any(b.get("type") == "image_url" for b in content)
    assert content[-1] == {"type": "text", "text": "Tool result: 42"}


def test_string_only_user_merge_unchanged():
    a = {"role": "user", "content": "first"}
    b = {"role": "user", "content": "second"}
    out = _sanitize_llm_messages([a, b])
    assert len(out) == 1
    assert out[0]["content"] == "first\n\nsecond"
