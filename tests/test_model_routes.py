"""Tests for model route helper functions — pure logic, no server needed."""
import sys
import types
from unittest.mock import MagicMock

import httpx
import pytest

_endpoint_resolver = sys.modules.get("src.endpoint_resolver")
if _endpoint_resolver is not None and not getattr(_endpoint_resolver, "__file__", None):
    # Other tests stub this module during collection. These helper tests need
    # the real URL normalization helpers so Anthropic /v1 handling is covered.
    sys.modules.pop("src.endpoint_resolver", None)
    sys.modules.pop("routes.model_routes", None)

if "core.database" not in sys.modules:
    _core_db = types.ModuleType("core.database")
    for _name in [
        "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
        "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
        "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun",
        "McpServer",
    ]:
        setattr(_core_db, _name, MagicMock())
    sys.modules["core.database"] = _core_db

import routes.model_routes as model_routes
import src.endpoint_resolver as endpoint_resolver
from routes.model_routes import (
    _match_provider_curated,
    _curate_models,
    _is_chat_model,
    _classify_endpoint,
    _probe_endpoint,
    _truthy,
    _speech_settings_using_endpoint,
    _clear_speech_settings_for_endpoint,
    _PROVIDER_CURATED,
)
from src.llm_core import ANTHROPIC_MODELS


# ── speech endpoint settings ──

def test_speech_endpoint_dependents_include_stt():
    settings = {"stt_provider": "endpoint:voice"}
    assert _speech_settings_using_endpoint(settings, "voice") == ["Speech to Text"]


def test_clear_speech_endpoint_settings_resets_tts_and_stt():
    settings = {
        "tts_provider": "endpoint:voice",
        "tts_model": "custom-tts",
        "stt_provider": "endpoint:voice",
        "stt_model": "custom-stt",
    }

    assert _clear_speech_settings_for_endpoint(settings, "voice") == [
        "Text to Speech",
        "Speech to Text",
    ]
    assert settings == {
        "tts_provider": "disabled",
        "tts_model": "tts-1",
        "stt_provider": "disabled",
        "stt_model": "base",
    }


# ── _match_provider_curated ──

class TestMatchProviderCurated:
    def test_url_match_overrides_provider(self):
        assert _match_provider_curated("https://z.ai/v1", "openai") == "zai"

    def test_deepseek_url(self):
        assert _match_provider_curated("https://api.deepseek.com/v1", "openai") == "deepseek"

    def test_groq_url(self):
        assert _match_provider_curated("https://api.groq.com/openai/v1", "openai") == "groq"

    def test_mistral_url(self):
        assert _match_provider_curated("https://api.mistral.ai/v1", "openai") == "mistral"

    def test_together_url(self):
        assert _match_provider_curated("https://api.together.xyz/v1", "openai") == "together"

    def test_fireworks_url(self):
        assert _match_provider_curated("https://api.fireworks.ai/inference/v1", "openai") == "fireworks"

    def test_google_url(self):
        assert _match_provider_curated("https://generativelanguage.googleapis.com/v1beta", "openai") == "google"

    def test_xai_url(self):
        assert _match_provider_curated("https://api.x.ai/v1", "openai") == "xai"

    def test_ollama_url(self):
        assert _match_provider_curated("https://ollama.com/api", "openai") == "ollama"

    def test_no_url_match_returns_provider(self):
        assert _match_provider_curated("https://localhost:1234", "openai") == "openai"

    def test_none_provider_passthrough(self):
        assert _match_provider_curated("https://localhost:1234", None) is None

    def test_none_url_safe(self):
        assert _match_provider_curated(None, "openai") == "openai"


# ── _curate_models ──

class TestCurateModels:
    def test_known_provider_partitions(self):
        models = ["gpt-4o", "gpt-4o-mini", "ft:gpt-4o:custom", "some-random-model"]
        curated, extra = _curate_models(models, "openai")
        assert "gpt-4o" in curated
        assert "gpt-4o-mini" in curated
        assert "some-random-model" in extra

    def test_unknown_provider_returns_all_as_curated(self):
        models = ["model-a", "model-b"]
        curated, extra = _curate_models(models, "unknown_provider")
        assert curated == models
        assert extra == []

    def test_curated_sorted_by_priority(self):
        models = ["gpt-4o-mini", "gpt-4o", "o3"]
        curated, _ = _curate_models(models, "openai")
        # gpt-4o should come before gpt-4o-mini in the curated list priority
        gpt4o_idx = curated.index("gpt-4o")
        gpt4o_mini_idx = curated.index("gpt-4o-mini")
        assert gpt4o_idx < gpt4o_mini_idx

    def test_empty_models(self):
        curated, extra = _curate_models([], "openai")
        assert curated == []
        assert extra == []

    def test_deepseek_curated(self):
        models = ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]
        curated, extra = _curate_models(models, "deepseek")
        assert "deepseek-chat" in curated
        assert "deepseek-reasoner" in curated
        assert "deepseek-coder" in extra

    def test_xai_curated(self):
        models = ["grok-4", "grok-3-fast", "grok-2"]
        curated, extra = _curate_models(models, "xai")
        assert "grok-4" in curated
        assert "grok-3-fast" in curated
        assert "grok-2" in extra

    def test_xai_current_grok_43_curated(self):
        curated, extra = _curate_models(["grok-4.3", "grok-4.3-fast"], "xai")
        assert curated == ["grok-4.3", "grok-4.3-fast"]
        assert extra == []

    def test_groq_current_models_curated(self):
        models = [
            "openai/gpt-oss-120b",
            "groq/compound",
            "llama-3.1-8b-instant",
            "llama-4-scout-17b-16e-instruct",
        ]
        curated, extra = _curate_models(models, "groq")
        assert curated == models
        assert extra == []

    def test_google_current_gemini_curated(self):
        curated, extra = _curate_models(["gemini-3.5-flash", "gemini-3.1-pro"], "google")
        assert curated == ["gemini-3.5-flash", "gemini-3.1-pro"]
        assert extra == []


# ── _is_chat_model ──

class TestIsChatModel:
    @pytest.mark.parametrize("model_id", [
        "gpt-4o", "gpt-4o-mini", "claude-sonnet-4", "llama-3.3-70b",
        "deepseek-chat", "gemini-2.0-flash", "o3",
        "llama-4-scout-17b-16e-instruct",
    ])
    def test_chat_models(self, model_id):
        assert _is_chat_model(model_id) is True

    @pytest.mark.parametrize("model_id", [
        "dall-e-3", "tts-1", "whisper-1", "text-embedding-3-small",
        "gpt-image-1", "sora-1",
    ])
    def test_non_chat_models(self, model_id):
        assert _is_chat_model(model_id) is False

    def test_realtime_excluded(self):
        assert _is_chat_model("gpt-4o-realtime-preview") is False

    def test_audio_preview_is_chat(self):
        # gpt-4o-audio-preview is a chat model (has "audio" not "gpt-audio")
        assert _is_chat_model("gpt-4o-audio-preview") is True

    def test_gpt_audio_is_not_chat(self):
        assert _is_chat_model("gpt-audio") is False

    def test_legacy_openai_instruct_is_not_chat(self):
        assert _is_chat_model("gpt-3.5-turbo-instruct") is False


# ── _classify_endpoint ──

class TestClassifyEndpoint:
    def test_localhost(self):
        assert _classify_endpoint("http://localhost:1234") == "local"

    def test_127(self):
        assert _classify_endpoint("http://127.0.0.1:8080/v1") == "local"

    def test_private_192(self):
        assert _classify_endpoint("http://192.168.1.100:5000") == "local"

    def test_private_10(self):
        assert _classify_endpoint("http://10.0.0.5:8000") == "local"

    def test_public_api(self):
        assert _classify_endpoint("https://api.openai.com/v1") == "api"

    def test_empty_string(self):
        assert _classify_endpoint("") == "api"

    def test_malformed_url(self):
        assert _classify_endpoint("not-a-url") == "api"


# ── setup probing ──

class TestSetupProbeSafety:
    @pytest.mark.parametrize("value", ["true", "1", "yes", "on", " TRUE "])
    def test_truthy_true_values(self, value):
        assert _truthy(value) is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "", None])
    def test_truthy_false_values(self, value):
        assert _truthy(value) is False

    def test_keyed_probe_does_not_fallback_to_curated_on_auth_failure(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
        monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))

        def fake_get(url, headers=None, timeout=None):
            request = httpx.Request("GET", url)
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)

        assert _probe_endpoint("https://api.groq.com/openai/v1", "bad-key") == []

    def test_unkeyed_probe_can_still_use_curated_fallback(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
        monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))

        def fake_get(url, headers=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)

        assert _probe_endpoint("https://api.groq.com/openai/v1") == _PROVIDER_CURATED["groq"]

    def test_keyed_anthropic_probe_does_not_fallback_on_failure(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
        monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))

        def fake_get(url, headers=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)

        assert _probe_endpoint("https://api.anthropic.com/v1", "bad-key") == []

    def test_anthropic_probe_does_not_double_v1(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
        monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))
        seen = []

        def fake_get(url, headers=None, timeout=None):
            seen.append(url)
            request = httpx.Request("GET", url)
            response = httpx.Response(
                200,
                request=request,
                json={"data": [{"id": "claude-sonnet-4-5"}]},
            )
            return response

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)

        assert _probe_endpoint("https://api.anthropic.com/v1", "good-key") == ["claude-sonnet-4-5"]
        assert seen == ["https://api.anthropic.com/v1/models"]

    def test_ollama_cloud_probe_uses_native_tags_endpoint(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
        monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))
        seen = []

        def fake_get(url, headers=None, timeout=None):
            seen.append((url, headers))
            request = httpx.Request("GET", url)
            response = httpx.Response(
                200,
                request=request,
                json={"models": [{"name": "gpt-oss:120b"}, {"model": "qwen3:235b"}]},
            )
            return response

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)

        assert _probe_endpoint("https://ollama.com/api", "ollama-key") == ["gpt-oss:120b", "qwen3:235b"]
        assert seen == [("https://ollama.com/api/tags", {"Authorization": "Bearer ollama-key"})]

    def test_unkeyed_anthropic_probe_can_use_curated_fallback(self, monkeypatch):
        monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
        monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))

        def fake_get(url, headers=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)

        assert _probe_endpoint("https://api.anthropic.com/v1") == ANTHROPIC_MODELS

def test_ollama_endpoint_error_message_includes_troubleshooting():
    msg = model_routes._model_endpoint_error_message(
        "http://localhost:11434/v1",
        {"error": "Connection refused"},
    )

    assert "No Ollama models found" in msg
    assert "Connection refused" in msg
    assert "http://localhost:11434/v1" in msg
    assert "ollama list" in msg


def test_generic_endpoint_error_message_preserves_probe_error():
    msg = model_routes._model_endpoint_error_message(
        "https://api.example.com/v1",
        {"error": "HTTP 401"},
    )

    assert msg == "No models found for that provider/key. Last probe error: HTTP 401."


# ── _rewrite_loopback_for_docker (issue #25: LM Studio on host loopback) ──

class TestDockerLoopbackRewrite:
    def test_rewrites_loopback_when_in_docker(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: True)
        assert (model_routes._rewrite_loopback_for_docker("http://localhost:1234/v1")
                == "http://host.docker.internal:1234/v1")
        assert (model_routes._rewrite_loopback_for_docker("http://127.0.0.1:1234/v1")
                == "http://host.docker.internal:1234/v1")

    def test_no_rewrite_when_not_in_docker(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: False)
        assert (model_routes._rewrite_loopback_for_docker("http://localhost:1234/v1")
                == "http://localhost:1234/v1")

    def test_non_loopback_untouched_even_in_docker(self, monkeypatch):
        # Cloud and LAN hosts must never be rewritten or they would break.
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: True)
        assert (model_routes._rewrite_loopback_for_docker("https://api.openai.com/v1")
                == "https://api.openai.com/v1")
        assert (model_routes._rewrite_loopback_for_docker("http://192.168.1.50:1234/v1")
                == "http://192.168.1.50:1234/v1")


class TestDockerHostGatewayReachable:
    def test_native_host_is_false_and_skips_dns(self, monkeypatch):
        monkeypatch.setattr(model_routes.os.path, "exists", lambda p: False)

        def _no_cgroup(*a, **k):
            raise FileNotFoundError

        monkeypatch.setattr("builtins.open", _no_cgroup)

        def _must_not_run(*a, **k):
            raise AssertionError("getaddrinfo must not run on native hosts")

        monkeypatch.setattr(model_routes.socket, "getaddrinfo", _must_not_run)
        assert model_routes._docker_host_gateway_reachable() is False

    def test_container_with_host_gateway_is_true(self, monkeypatch):
        monkeypatch.setattr(model_routes.os.path, "exists", lambda p: p == "/.dockerenv")
        monkeypatch.setattr(model_routes.socket, "getaddrinfo", lambda *a, **k: [("ok",)])
        assert model_routes._docker_host_gateway_reachable() is True

    def test_container_without_host_gateway_is_false(self, monkeypatch):
        monkeypatch.setattr(model_routes.os.path, "exists", lambda p: p == "/.dockerenv")

        def _fail(*a, **k):
            raise OSError("name or service not known")

        monkeypatch.setattr(model_routes.socket, "getaddrinfo", _fail)
        assert model_routes._docker_host_gateway_reachable() is False
