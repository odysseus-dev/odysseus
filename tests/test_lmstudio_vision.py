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
        assert chat_helpers.model_supports_vision("deepseek-flash", "") is True
        assert chat_helpers.model_supports_vision("deepseek-v4-pro", "") is False
        assert chat_helpers.model_supports_vision("mistral-7b", "") is False

    def test_falls_back_to_name_when_endpoint_unknown(self, monkeypatch):
        # Endpoint doesn't advertise (None) → name heuristic decides.
        monkeypatch.setattr(chat_helpers, "lmstudio_supports_vision", lambda url, m: None)
        assert chat_helpers.model_supports_vision("qwen2-vl-7b", "http://host/v1") is True
        assert chat_helpers.model_supports_vision("plain-llm", "http://host/v1") is False


# ════════════════════════════════════════════════════════════
# llamacpp_supports_vision — /props outranks the model name
# ════════════════════════════════════════════════════════════

class TestLlamaCppSupportsVision:
    URL = "http://localhost:8001/v1"
    # Qwen3.5 is a vision family by name, but this serve has no --mmproj.
    NAMED = "Qwen3.5-9B-The-Defiant-Fable-Q6_K.gguf"

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        chat_helpers._llamacpp_vision_cache.clear()
        chat_helpers._vision_probe_cache.clear()
        monkeypatch.delenv("ODYSSEUS_VISION_MODELS", raising=False)
        monkeypatch.setattr(chat_helpers, "lmstudio_supports_vision", lambda url, m, headers=None: None)
        yield
        chat_helpers._llamacpp_vision_cache.clear()
        chat_helpers._vision_probe_cache.clear()

    def _serve(self, monkeypatch, payload, ok=True):
        urls = []

        def fake_get(url, timeout=None):
            urls.append(url)
            return _FakeResponse(payload, ok)

        monkeypatch.setattr(chat_helpers.httpx, "get", fake_get)
        return urls

    def _no_image_probe(self, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("no image request should be sent")

        monkeypatch.setattr(chat_helpers, "probe_endpoint_supports_vision", fail)

    def test_reads_props_at_the_server_root(self, monkeypatch):
        urls = self._serve(monkeypatch, {"modalities": {"vision": True}})
        assert chat_helpers.llamacpp_supports_vision(self.URL) is True
        assert urls == ["http://localhost:8001/props"]

    def test_vision_false_beats_a_vision_name(self, monkeypatch):
        self._serve(monkeypatch, {"modalities": {"vision": False}})
        self._no_image_probe(monkeypatch)
        assert chat_helpers.model_supports_vision(self.NAMED, self.URL) is False

    def test_vision_true_beats_a_plain_name(self, monkeypatch):
        self._serve(monkeypatch, {"modalities": {"vision": True}})
        self._no_image_probe(monkeypatch)
        assert chat_helpers.model_supports_vision("plain-llm.gguf", self.URL) is True

    @pytest.mark.parametrize("payload,ok", [
        ({"default_generation_settings": {}}, True),
        ({"modalities": {}}, True),
        ({"modalities": {"vision": "yes"}}, True),
        (["not", "a", "dict"], True),
        ({}, False),
    ])
    def test_no_report_keeps_a_vision_name_without_probing(self, monkeypatch, payload, ok):
        self._serve(monkeypatch, payload, ok)
        self._no_image_probe(monkeypatch)
        assert chat_helpers.llamacpp_supports_vision(self.URL) is None
        assert chat_helpers.model_supports_vision(self.NAMED, self.URL) is True

    def test_explicit_override_beats_the_report(self, monkeypatch):
        monkeypatch.setenv("ODYSSEUS_VISION_MODELS", "defiant-fable")
        self._serve(monkeypatch, {"modalities": {"vision": False}})
        assert chat_helpers.model_supports_vision(self.NAMED, self.URL) is True

    def test_remote_endpoint_never_probed(self, monkeypatch):
        urls = self._serve(monkeypatch, {"modalities": {"vision": False}})
        assert chat_helpers.llamacpp_supports_vision("https://api.openai.com/v1") is None
        assert urls == []

    def test_report_is_cached_briefly(self, monkeypatch):
        urls = self._serve(monkeypatch, {"modalities": {"vision": True}})
        chat_helpers.llamacpp_supports_vision(self.URL)
        chat_helpers.llamacpp_supports_vision(self.URL)
        assert len(urls) == 1

    def test_missing_projector_500_means_no_vision(self, monkeypatch):
        class Rejected:
            status_code = 500
            text = '{"error":{"message":"image input is not supported - hint: you may need to provide the mmproj"}}'

        monkeypatch.setattr(chat_helpers.httpx, "post", lambda *a, **k: Rejected())
        assert chat_helpers.probe_endpoint_supports_vision(self.URL, "plain-llm") is False

    def test_other_500_stays_undecided(self, monkeypatch):
        class Broken:
            status_code = 500
            text = "out of memory"

        monkeypatch.setattr(chat_helpers.httpx, "post", lambda *a, **k: Broken())
        assert chat_helpers.probe_endpoint_supports_vision(self.URL, "plain-llm") is None
