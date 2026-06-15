"""Regression tests for Ollama multimodal content conversion (#4249)."""

from src.llm_core import _ollama_normalize_tool_messages


class TestOllamaMultimodalContent:
    """Verify that OpenAI-style multimodal content arrays are converted
    to Ollama's expected format (string content + images list)."""

    def test_single_image_with_text(self):
        msg = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            ],
        }]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["content"] == "What is in this image?"
        assert result[0]["images"] == ["iVBORw0KGgo="]

    def test_multiple_images(self):
        msg = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Compare these"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,def456"}},
            ],
        }]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["content"] == "Compare these"
        assert result[0]["images"] == ["abc123", "def456"]

    def test_image_only_no_text(self):
        msg = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["content"] == ""
        assert result[0]["images"] == ["abc"]

    def test_plain_text_unchanged(self):
        msg = [{"role": "user", "content": "Hello"}]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["content"] == "Hello"
        assert "images" not in result[0]

    def test_non_data_uri_images_skipped(self):
        """External HTTP image URLs are not base64 data -- skip them."""
        msg = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Look"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
        }]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["content"] == "Look"
        assert "images" not in result[0]

    def test_tool_calls_still_normalized(self):
        msg = [{
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "test", "arguments": '{"key": "value"}'}},
            ],
        }]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["tool_calls"][0]["function"]["arguments"] == {"key": "value"}

    def test_mixed_content_and_tool_calls(self):
        """A message with both multimodal content and tool_calls."""
        msg = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Analyzing..."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
            ],
            "tool_calls": [
                {"function": {"name": "analyze", "arguments": "{}"}},
            ],
        }]
        result = _ollama_normalize_tool_messages(msg)
        assert result[0]["content"] == "Analyzing..."
        assert result[0]["images"] == ["xyz"]
        assert result[0]["tool_calls"][0]["function"]["arguments"] == {}

    def test_non_dict_messages_pass_through(self):
        result = _ollama_normalize_tool_messages(["text", 42, None])
        assert result == ["text", 42, None]
