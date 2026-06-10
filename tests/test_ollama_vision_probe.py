"""Ollama vision-capability probe (model_supports_vision).

Modern Ollama vision models (e.g. qwen3.5:9b) carry no "vl"/"vision" marker
in their tag, so the name-based heuristic misclassified them as text-only and
their images were silently swapped for a caption. model_supports_vision now
asks Ollama's /api/show for the model's reported capabilities first.
"""

import pytest

from src import chat_helpers
from src.chat_helpers import (
    _probe_ollama_capabilities,
    model_supports_vision,
    ollama_supports_vision,
)


@pytest.fixture(autouse=True)
def _clear_probe_caches():
    chat_helpers._ollama_caps_cache.clear()
    chat_helpers._lmstudio_models_cache.clear()
    yield
    chat_helpers._ollama_caps_cache.clear()
    chat_helpers._lmstudio_models_cache.clear()


class _Resp:
    def __init__(self, payload, success=True):
        self._payload = payload
        self.is_success = success

    def json(self):
        return self._payload


def test_ollama_vision_capability_detected(monkeypatch):
    """qwen3.5:9b reports 'vision' via /api/show → treated as vision-capable
    even though the name-based heuristic says no."""
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return _Resp({"capabilities": ["completion", "vision", "tools"]})

    monkeypatch.setattr(chat_helpers.httpx, "post", fake_post)
    # LM Studio probe must not answer first.
    monkeypatch.setattr(chat_helpers, "_probe_lmstudio_models", lambda url: None)

    assert model_supports_vision("qwen3.5:9b", "http://172.24.224.1:11434/v1") is True
    assert calls["url"] == "http://172.24.224.1:11434/api/show"
    assert calls["json"] == {"model": "qwen3.5:9b"}


def test_ollama_text_only_capability_detected(monkeypatch):
    """A model whose /api/show lacks 'vision' is text-only — even when the
    name heuristic would err toward True (e.g. a hypothetical '-vl' tag)."""
    monkeypatch.setattr(
        chat_helpers.httpx, "post",
        lambda url, json=None, timeout=None: _Resp({"capabilities": ["completion"]}),
    )
    monkeypatch.setattr(chat_helpers, "_probe_lmstudio_models", lambda url: None)

    assert model_supports_vision("some-vl-model", "http://127.0.0.1:11434/v1") is False


def test_non_ollama_endpoint_falls_back_to_name_heuristic(monkeypatch):
    """No capabilities reported (404 / not Ollama) → name-based fallback."""
    monkeypatch.setattr(
        chat_helpers.httpx, "post",
        lambda url, json=None, timeout=None: _Resp({}, success=False),
    )
    monkeypatch.setattr(chat_helpers, "_probe_lmstudio_models", lambda url: None)

    assert model_supports_vision("llava:13b", "http://127.0.0.1:8080/v1") is True
    assert model_supports_vision("qwen3.5:9b", "http://127.0.0.1:8080/v1") is False


def test_remote_hosts_are_never_probed(monkeypatch):
    """Public providers must not receive /api/show probes."""
    def boom(*a, **kw):
        raise AssertionError("remote host was probed")

    monkeypatch.setattr(chat_helpers.httpx, "post", boom)

    assert ollama_supports_vision("https://api.openai.com/v1", "gpt-4o") is None


def test_probe_result_is_cached(monkeypatch):
    counter = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        counter["n"] += 1
        return _Resp({"capabilities": ["completion", "vision"]})

    monkeypatch.setattr(chat_helpers.httpx, "post", fake_post)

    for _ in range(3):
        assert _probe_ollama_capabilities("http://127.0.0.1:11434/v1", "m") == [
            "completion", "vision",
        ]
    assert counter["n"] == 1


def test_unreachable_endpoint_is_not_cached_and_falls_back(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(chat_helpers.httpx, "post", fake_post)
    monkeypatch.setattr(chat_helpers, "_probe_lmstudio_models", lambda url: None)

    # Transient failure → no crash, no cache entry, name heuristic decides.
    assert model_supports_vision("gemma4:latest", "http://127.0.0.1:11434/v1") is True
    assert chat_helpers._ollama_caps_cache == {}
