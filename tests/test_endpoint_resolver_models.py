"""Tests for endpoint_resolver — endpoint/model selection and enabled-model filtering."""
import json

from src.endpoint_resolver import (
    _first_chat_model,
    _endpoint_hidden_models,
    _endpoint_enabled_models,
)


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
