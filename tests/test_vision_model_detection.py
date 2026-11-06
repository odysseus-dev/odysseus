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
        "Qwen3.5-9B", "qwen3_5-9b", "qwen35-9b-base-native",
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


def test_recognizes_multimodal_families_without_vision_in_name():
    # issue #1274: these are vision-capable but their names don't contain
    # "vision"/"vl", so they were dropped and the model never saw the image.
    for name in [
        "gemma3:4b", "gemma3", "gemma-3-27b-it",
        "llama4:scout", "llama4", "llama-4-maverick",
        "mistral-small3.1", "mistral-small-3.2",
        "phi-4-multimodal", "phi4-multimodal",
    ]:
        assert is_vision_model(name), f"{name!r} should be detected as vision-capable"


def test_new_keywords_do_not_overmatch_text_models():
    # The added families must not flag their text-only siblings.
    for name in ["gemma2:9b", "gemma:7b", "llama3.3", "mistral-small", "phi-3-mini"]:
        assert not is_vision_model(name), f"{name!r} should not be flagged as vision"


def test_recognizes_gpt5_and_later_generations():
    # The hosted keyword list stopped at gpt-4.5, so every GPT-5 tag fell through
    # and the attachment was swapped for a caption before it reached the model.
    # Matched by generation number so future releases don't need a code change.
    for name in [
        "gpt-5.6-sol", "gpt-5", "gpt-5.2", "gpt-5.2-pro", "gpt-5.2-codex",
        "gpt-5-mini", "gpt-6", "gpt-10.1",
    ]:
        assert is_vision_model(name), f"{name!r} should be detected as vision-capable"


def test_gpt_generation_match_does_not_catch_older_text_models():
    # Vision-capable GPT-4 tags are listed literally; the bare/older ones are not
    # multimodal and must stay unflagged.
    for name in ["gpt-4", "gpt-3.5-turbo", "gpt-3.5", "gpt2"]:
        assert not is_vision_model(name), f"{name!r} should not be flagged as vision"
