"""Tests for endpoint_resolver — pure functions tested directly."""
import json

import pytest

from src.endpoint_resolver import (
    _first_chat_model,
    _endpoint_hidden_models,
    _endpoint_enabled_models,
    normalize_base,
    build_chat_url,
    build_models_url,
    build_headers,
)


class TestNormalizeBase:
    def test_strips_models(self):
        assert normalize_base("https://api.openai.com/v1/models") == "https://api.openai.com/v1"

    def test_strips_chat_completions(self):
        assert normalize_base("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"

    def test_strips_completions(self):
        assert normalize_base("https://api.openai.com/v1/completions") == "https://api.openai.com/v1"

    def test_strips_v1_messages(self):
        assert normalize_base("https://api.anthropic.com/v1/messages") == "https://api.anthropic.com"

    def test_strips_ollama_native_chat(self):
        assert normalize_base("https://ollama.com/api/chat") == "https://ollama.com/api"

    def test_trailing_slash(self):
        assert normalize_base("https://api.openai.com/v1/") == "https://api.openai.com/v1"

    def test_clean_url_unchanged(self):
        assert normalize_base("https://api.openai.com/v1") == "https://api.openai.com/v1"

    def test_empty_string(self):
        assert normalize_base("") == ""

    def test_none_safe(self):
        assert normalize_base(None) == ""


class TestBuildChatUrl:
    def test_openai_style(self):
        assert build_chat_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"

    def test_pathless_openai_style_adds_v1(self):
        assert build_chat_url("https://api.openai.com") == "https://api.openai.com/v1/chat/completions"

    def test_anthropic_style(self):
        assert build_chat_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"

    def test_anthropic_v1_base_does_not_double_v1(self):
        assert build_chat_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"

    def test_local_endpoint(self):
        assert build_chat_url("http://localhost:8000/v1") == "http://localhost:8000/v1/chat/completions"

    def test_ollama_cloud_native_api(self):
        assert build_chat_url("https://ollama.com/api") == "https://ollama.com/api/chat"

    def test_ollama_cloud_root_adds_api(self):
        assert build_chat_url("https://ollama.com") == "https://ollama.com/api/chat"

    def test_ollama_bare_url_adds_api(self):
        assert build_chat_url("http://nas:11434") == "http://nas:11434/api/chat"

    def test_ollama_v1_preserves_openai_compat(self):
        assert build_chat_url("http://nas:11434/v1") == "http://nas:11434/v1/chat/completions"

    @pytest.mark.parametrize("bad_base", [
        "https://api.example.com/v1?token=abc",
        "https://api.example.com/v1#fragment",
        "http://localhost:1234?",
    ])
    def test_rejects_query_or_fragment_base(self, bad_base):
        with pytest.raises(ValueError, match="query or fragment"):
            build_chat_url(bad_base)


class TestBuildModelsUrl:
    def test_openai_models(self):
        assert build_models_url("https://api.openai.com/v1") == "https://api.openai.com/v1/models"

    def test_pathless_openai_models_adds_v1(self):
        assert build_models_url("https://api.openai.com") == "https://api.openai.com/v1/models"

    def test_ollama_tags(self):
        assert build_models_url("https://ollama.com/api") == "https://ollama.com/api/tags"

    @pytest.mark.parametrize("bad_base", [
        "https://api.example.com/v1?token=abc",
        "https://api.example.com/v1#fragment",
        "http://localhost:1234?",
    ])
    def test_rejects_query_or_fragment_base(self, bad_base):
        with pytest.raises(ValueError, match="query or fragment"):
            build_models_url(bad_base)


class TestBuildHeaders:
    def test_no_key(self):
        assert build_headers(None, "https://api.openai.com/v1") == {}

    def test_openai_bearer(self):
        assert build_headers("sk-abc", "https://api.openai.com/v1") == {"Authorization": "Bearer sk-abc"}

    def test_anthropic_headers(self):
        assert build_headers("sk-ant-abc", "https://api.anthropic.com") == {"x-api-key": "sk-ant-abc", "anthropic-version": "2023-06-01"}

    def test_empty_key(self):
        assert build_headers("", "https://api.openai.com/v1") == {}


class _Ep:
    """Minimal ModelEndpoint stand-in for the model-picking helpers."""
    def __init__(self, cached=None, hidden=None):
        self.cached_models = json.dumps(cached) if cached is not None else None
        self.hidden_models = json.dumps(hidden) if hidden is not None else None


class TestFirstChatModel:
    def test_skips_embedding_and_tts(self):
        models = ["text-embedding-ada-002", "whisper-large-v3", "gpt-4o"]
        assert _first_chat_model(models) == "gpt-4o"

    def test_falls_back_to_first_when_all_non_chat(self):
        assert _first_chat_model(["whisper-large-v3"]) == "whisper-large-v3"

    def test_empty(self):
        assert _first_chat_model([]) is None


class TestEnabledModels:
    def test_excludes_hidden(self):
        # The Groq repro: 16 models, only gpt-oss-120b enabled.
        cached = [
            "openai/gpt-oss-safeguard-20b", "canopylabs/orpheus-arabic-saudi",
            "whisper-large-v3", "openai/gpt-oss-120b",
        ]
        hidden = [
            "openai/gpt-oss-safeguard-20b", "canopylabs/orpheus-arabic-saudi",
            "whisper-large-v3",
        ]
        ep = _Ep(cached=cached, hidden=hidden)
        assert _endpoint_enabled_models(ep) == ["openai/gpt-oss-120b"]

    def test_no_hidden_returns_all(self):
        ep = _Ep(cached=["a", "b"], hidden=None)
        assert _endpoint_enabled_models(ep) == ["a", "b"]

    def test_picker_never_selects_disabled_model(self):
        # Regression: a disabled model listed first must not be auto-picked.
        cached = ["canopylabs/orpheus-arabic-saudi", "openai/gpt-oss-120b"]
        hidden = ["canopylabs/orpheus-arabic-saudi"]
        ep = _Ep(cached=cached, hidden=hidden)
        assert _first_chat_model(_endpoint_enabled_models(ep)) == "openai/gpt-oss-120b"

    def test_stale_configured_model_is_discarded(self):
        # A configured model that's been disabled is dropped, falling through
        # to the first enabled chat model.
        ep = _Ep(
            cached=["canopylabs/orpheus-arabic-saudi", "openai/gpt-oss-120b"],
            hidden=["canopylabs/orpheus-arabic-saudi"],
        )
        configured = "canopylabs/orpheus-arabic-saudi"
        if configured in _endpoint_hidden_models(ep):
            configured = ""
        if not configured:
            configured = _first_chat_model(_endpoint_enabled_models(ep))
        assert configured == "openai/gpt-oss-120b"
