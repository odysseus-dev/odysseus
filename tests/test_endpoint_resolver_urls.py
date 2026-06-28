"""Tests for endpoint_resolver — URL normalization and URL construction."""
import pytest

from src.endpoint_resolver import (
    normalize_base,
    build_chat_url,
    build_models_url,
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
