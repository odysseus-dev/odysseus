from src.research_utils import strip_thinking


def test_strip_thinking_none_passthrough():
    # strip_thinking is documented to return None when the LLM call fails.
    # Callers that chain .strip() directly would crash with AttributeError.
    assert strip_thinking(None) is None


def test_strip_thinking_or_empty_is_safe():
    # The fix: (strip_thinking(response) or "").strip() must not raise.
    result = (strip_thinking(None) or "").strip()
    assert result == ""


def test_strip_thinking_normal_string():
    assert strip_thinking("hello world") == "hello world"


def test_strip_thinking_with_think_block():
    raw = "<think>reasoning</think>final answer"
    assert "final answer" in strip_thinking(raw)
