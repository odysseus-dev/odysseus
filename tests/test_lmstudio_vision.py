"""Tests for LM Studio vision-capability passthrough: reading capabilities.vision
from the native /api/v1/models endpoint, with no probing of cloud providers."""
import pytest

from src import chat_helpers


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


# ════════════════════════════════════════════════════════════
# lmstudio_supports_vision — reads capabilities.vision
# ════════════════════════════════════════════════════════════

class TestLmStudioSupportsVision:
    # A vision finetune whose NAME has no vision keyword — the case the
    # name-based heuristic gets wrong (the issue this fixes).
    PAYLOAD = {"models": [
        {"key": "qwen3.6-27b-custom-finetune", "architecture": "qwen35",
         "capabilities": {"vision": True, "trained_for_tool_use": True}},
        {"key": "text-only-llm", "architecture": "qwen35",
         "capabilities": {"vision": False}},
        {"key": "no-caps-model", "architecture": "qwen35"},
    ]}
    URL = "http://localhost:1234/v1/chat/completions"

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        chat_helpers._lmstudio_models_cache.clear()
        yield
        chat_helpers._lmstudio_models_cache.clear()

    def _serve(self, monkeypatch, payload):
        monkeypatch.setattr(chat_helpers.httpx, "get",
                            lambda url, timeout=None: _FakeResponse(payload))

    def test_vision_true_from_capabilities(self, monkeypatch):
        self._serve(monkeypatch, self.PAYLOAD)
        assert chat_helpers.lmstudio_supports_vision(self.URL, "qwen3.6-27b-custom-finetune") is True

    def test_vision_false_from_capabilities(self, monkeypatch):
        self._serve(monkeypatch, self.PAYLOAD)
        assert chat_helpers.lmstudio_supports_vision(self.URL, "text-only-llm") is False

    def test_model_without_capabilities_returns_none(self, monkeypatch):
        self._serve(monkeypatch, self.PAYLOAD)
        assert chat_helpers.lmstudio_supports_vision(self.URL, "no-caps-model") is None

    def test_unknown_model_returns_none(self, monkeypatch):
        self._serve(monkeypatch, self.PAYLOAD)
        assert chat_helpers.lmstudio_supports_vision(self.URL, "not-listed") is None

    def test_non_lmstudio_endpoint_returns_none(self, monkeypatch):
        self._serve(monkeypatch, {"data": [{"id": "gpt-4o"}]})
        assert chat_helpers.lmstudio_supports_vision(self.URL, "gpt-4o") is None

    def test_empty_model_returns_none(self, monkeypatch):
        self._serve(monkeypatch, self.PAYLOAD)
        assert chat_helpers.lmstudio_supports_vision(self.URL, "") is None

    def test_remote_endpoint_never_probed(self, monkeypatch):
        calls = {"n": 0}

        def tracking_get(url, timeout=None):
            calls["n"] += 1
            return _FakeResponse(self.PAYLOAD)

        monkeypatch.setattr(chat_helpers.httpx, "get", tracking_get)
        # A cloud provider host must short-circuit to None with no network probe.
        assert chat_helpers.lmstudio_supports_vision(
            "https://api.openai.com/v1/chat/completions", "gpt-4o") is None
        assert calls["n"] == 0


# ════════════════════════════════════════════════════════════
# model_supports_vision — endpoint capability wins, name is fallback
# ════════════════════════════════════════════════════════════

class TestModelSupportsVision:
    """Endpoint-aware vision check: API capability wins, name heuristic is the fallback."""

    def test_api_capability_overrides_name_heuristic(self, monkeypatch):
        # Name has no vision keyword, but the endpoint advertises vision=True.
        monkeypatch.setattr(chat_helpers, "is_vision_model", lambda n: False)
        monkeypatch.setattr(chat_helpers, "lmstudio_supports_vision", lambda url, m: True)
        assert chat_helpers.model_supports_vision("qwen3.6-27b-finetune",
                                                  "http://localhost:1234/v1/chat/completions") is True

    def test_falls_back_to_name_when_no_endpoint(self):
        # No endpoint URL → pure name heuristic.
        assert chat_helpers.model_supports_vision("llava-1.6", "") is True
        assert chat_helpers.model_supports_vision("mistral-7b", "") is False

    def test_falls_back_to_name_when_endpoint_unknown(self, monkeypatch):
        # Endpoint doesn't advertise (None) → name heuristic decides.
        monkeypatch.setattr(chat_helpers, "lmstudio_supports_vision", lambda url, m: None)
        monkeypatch.setattr(chat_helpers, "ollama_supports_vision", lambda url, m: None)
        assert chat_helpers.model_supports_vision("qwen2-vl-7b", "http://host/v1") is True
        assert chat_helpers.model_supports_vision("plain-llm", "http://host/v1") is False

    def test_ollama_capability_overrides_name_heuristic(self, monkeypatch):
        # devstral-small-2 has no vision keyword, but Ollama reports vision.
        monkeypatch.setattr(chat_helpers, "lmstudio_supports_vision", lambda url, m: None)
        monkeypatch.setattr(chat_helpers, "ollama_supports_vision", lambda url, m: True)
        assert chat_helpers.model_supports_vision("devstral-small-2:latest",
                                                  "http://localhost:11434/v1") is True


# ════════════════════════════════════════════════════════════
# ollama_supports_vision — reads /api/show capabilities
# ════════════════════════════════════════════════════════════

class TestOllamaSupportsVision:
    URL = "http://localhost:11434/v1"

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        chat_helpers._ollama_vision_cache.clear()
        yield
        chat_helpers._ollama_vision_cache.clear()

    def _serve(self, monkeypatch, payload, ok=True):
        calls = []

        def fake_post(url, json=None, timeout=None):
            calls.append((url, json))
            return _FakeResponse(payload, ok=ok)

        monkeypatch.setattr(chat_helpers.httpx, "post", fake_post)
        return calls

    def test_vision_capability_returns_true(self, monkeypatch):
        calls = self._serve(monkeypatch, {"capabilities": ["completion", "vision", "tools"]})
        assert chat_helpers.ollama_supports_vision(self.URL, "devstral-small-2:latest") is True
        assert calls == [("http://localhost:11434/api/show", {"model": "devstral-small-2:latest"})]

    def test_missing_vision_returns_none_not_false(self, monkeypatch):
        # A partial list (e.g. llama.cpp's /api/show) must not veto vision.
        self._serve(monkeypatch, {"capabilities": ["completion"]})
        assert chat_helpers.ollama_supports_vision(self.URL, "some-model") is None

    def test_non_ollama_endpoint_returns_none(self, monkeypatch):
        self._serve(monkeypatch, {"error": "not found"}, ok=False)
        assert chat_helpers.ollama_supports_vision(self.URL, "some-model") is None

    def test_result_is_cached(self, monkeypatch):
        calls = self._serve(monkeypatch, {"capabilities": ["vision"]})
        chat_helpers.ollama_supports_vision(self.URL, "gemma4:e4b")
        chat_helpers.ollama_supports_vision(self.URL, "gemma4:e4b")
        assert len(calls) == 1

    def test_remote_endpoint_never_probed(self, monkeypatch):
        calls = self._serve(monkeypatch, {"capabilities": ["vision"]})
        assert chat_helpers.ollama_supports_vision(
            "https://api.openai.com/v1/chat/completions", "gpt-4o") is None
        assert calls == []
