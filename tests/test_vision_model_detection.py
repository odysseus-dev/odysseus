"""Tests for is_vision_model (issue #124).

Local vision models served through Ollama/llama.cpp show up under many
names. If one isn't recognized as vision-capable, the image attachment is
stripped from the request before it reaches the model, so it silently never
sees the picture.
"""
from src.chat_helpers import is_vision_model


def test_recognizes_local_and_hosted_vision_models():
    for name in [
        # the ones #124 missed
        "moondream", "moondream:latest",
        "llama3.2-vision:11b", "granite3.2-vision",
        "qwen2.5-vl:7b", "qwen2.5vl", "internvl2.5", "cogvlm",
        # already worked, keep them working
        "llava", "llava:7b", "bakllava", "minicpm-v",
        "gpt-4o", "claude-sonnet-4", "gemini-2.0-flash", "pixtral-12b",
    ]:
        assert is_vision_model(name), f"{name!r} should be detected as vision-capable"


def test_text_only_models_not_flagged():
    for name in ["qwen2.5:3b", "mistral", "llama3.1:8b", "deepseek-r1", "phi3", "vicuna", ""]:
        assert not is_vision_model(name), f"{name!r} should not be flagged as vision"


def test_none_is_safe():
    assert is_vision_model(None) is False
