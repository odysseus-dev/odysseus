from src.model_capabilities import (
    is_openai_responses_required_model,
    requires_openai_responses_api,
)


def test_detects_openai_responses_required_models():
    assert is_openai_responses_required_model("gpt-5.5-pro")
    assert is_openai_responses_required_model("openai/gpt-5-codex-max-2026-01-31")
    assert is_openai_responses_required_model("o3-pro")


def test_does_not_flag_regular_chat_or_non_generation_models():
    assert not is_openai_responses_required_model("gpt-5.5")
    assert not is_openai_responses_required_model("text-embedding-3-large")
    assert not is_openai_responses_required_model("")


def test_requires_responses_api_only_for_official_openai_hosts():
    assert requires_openai_responses_api(
        "https://api.openai.com/v1/chat/completions",
        "gpt-5.5-pro",
    )
    assert not requires_openai_responses_api(
        "https://example.test/v1/chat/completions",
        "gpt-5.5-pro",
    )
