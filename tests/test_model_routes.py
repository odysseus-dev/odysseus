"""Tests for model route helper functions — pure logic, no server needed."""
import asyncio
import json
import sys
import types
from types import SimpleNamespace
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
    _visible_models,
    _normalize_model_ids,
    _is_chat_model,
    _classify_endpoint,
    _probe_endpoint,
    _truthy,
    _speech_settings_using_endpoint,
    _clear_speech_settings_for_endpoint,
    _endpoint_settings_using_endpoint,
    _clear_endpoint_settings_for_endpoint,
    _clear_user_pref_endpoint_refs,
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


def test_endpoint_cleanup_removes_primary_and_fallback_references():
    settings = {
        "default_endpoint_id": "dead",
        "default_model": "primary",
        "default_model_fallbacks": [
            {"endpoint_id": "dead", "model": "fallback-a"},
            {"endpoint_id": "keep", "model": "fallback-b"},
        ],
        "utility_model_fallbacks": [{"endpoint_id": "dead", "model": "utility"}],
        "vision_model_fallbacks": [{"endpoint_id": "dead", "model": "vision"}],
        "stt_provider": "endpoint:dead",
        "stt_model": "whisper",
    }

    assert _endpoint_settings_using_endpoint(settings, "dead", include_speech=True) == [
        "Default Model",
        "Default Model Fallbacks",
        "Utility Model Fallbacks",
        "Vision Model Fallbacks",
        "Speech to Text",
    ]
    assert _clear_endpoint_settings_for_endpoint(settings, "dead", include_speech=True) == [
        "Default Model",
        "Default Model Fallbacks",
        "Utility Model Fallbacks",
        "Vision Model Fallbacks",
        "Speech to Text",
    ]
    assert settings["default_endpoint_id"] == ""
    assert settings["default_model"] == ""
    assert settings["default_model_fallbacks"] == [
        {"endpoint_id": "keep", "model": "fallback-b"},
    ]
    assert settings["utility_model_fallbacks"] == []
    assert settings["vision_model_fallbacks"] == []
    assert settings["stt_provider"] == "disabled"
    assert settings["stt_model"] == "base"


def test_endpoint_cleanup_updates_scoped_and_legacy_user_prefs():
    scoped = {
        "_users": {
            "alice": {
                "utility_endpoint_id": "dead",
                "utility_model": "utility",
                "vision_model_fallbacks": [{"endpoint_id": "dead", "model": "vision"}],
            },
            "bob": {
                "default_endpoint_id": "keep",
                "default_model": "chat",
            },
        },
    }
    assert _clear_user_pref_endpoint_refs(scoped, "dead") == 1
    assert scoped["_users"]["alice"] == {
        "utility_endpoint_id": "",
        "utility_model": "",
        "vision_model_fallbacks": [],
    }
    assert scoped["_users"]["bob"]["default_endpoint_id"] == "keep"

    legacy = {
        "default_model_fallbacks": [{"endpoint_id": "dead", "model": "chat"}],
    }
    assert _clear_user_pref_endpoint_refs(legacy, "dead") == 1
    assert legacy["default_model_fallbacks"] == []


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


# ── pinned model IDs: normalization helper ──


class TestNormalizeModelIds:
    def test_list_passthrough_trims_and_dedupes(self):
        assert _normalize_model_ids([" a ", "a", "b", ""]) == ["a", "b"]

    def test_json_string_list(self):
        assert _normalize_model_ids('["x", "y", "x"]') == ["x", "y"]

    def test_comma_and_newline_string(self):
        assert _normalize_model_ids("a, b\n c ,a") == ["a", "b", "c"]

    def test_none_and_empty(self):
        assert _normalize_model_ids(None) == []
        assert _normalize_model_ids("") == []
        assert _normalize_model_ids("   ") == []

    def test_non_string_values_ignored(self):
        assert _normalize_model_ids([1, "ok", None, {"a": 1}]) == ["ok"]


# ── pinned model IDs: _visible_models merge ──


class TestVisibleModelsPinned:
    def test_includes_pinned_not_in_cached(self):
        visible = _visible_models(["a"], None, ["deploy-1"])
        assert visible == ["a", "deploy-1"]

    def test_cached_plus_pinned_dedup_preserves_order(self):
        visible = _visible_models(["a", "b"], None, ["b", "c"])
        assert visible == ["a", "b", "c"]

    def test_hidden_can_hide_a_pinned_model(self):
        visible = _visible_models(["a"], ["deploy-1"], ["deploy-1"])
        assert visible == ["a"]

    def test_accepts_json_string_inputs(self):
        visible = _visible_models('["a"]', '["a"]', '["b"]')
        assert visible == ["b"]


# ── pinned model IDs: route behaviour ──

# Building the router exercises FastAPI's Form() routes, which require
# python-multipart. The test env ships without it, so register a minimal stub
# (mirrors tests/test_review_regressions.py) only when it's genuinely missing.
if "python_multipart" not in sys.modules:
    try:
        import python_multipart  # noqa: F401
    except ImportError:
        _mp_stub = types.ModuleType("python_multipart")
        _mp_stub.__version__ = "0.0.13"
        sys.modules["python_multipart"] = _mp_stub


class _PinnedFakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class _PinnedFakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.committed = 0

    def query(self, model):
        return _PinnedFakeQuery(self.rows)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.committed += 1

    def close(self):
        pass


class _FakeCol:
    """Column stand-in: every comparison/operator just returns itself so the
    dedupe query expressions evaluate without a real SQLAlchemy column."""

    __hash__ = None

    def __eq__(self, other):
        return self

    def is_(self, other):
        return self

    def __or__(self, other):
        return self

    def desc(self):
        return self


class _RecordingEndpoint:
    """ModelEndpoint stand-in that stores constructor kwargs as attributes.

    Class-level fake columns let it double as the query class in the dedupe
    lookup; instance attributes (set in __init__) shadow them per-row.
    """

    id = _FakeCol()
    base_url = _FakeCol()
    owner = _FakeCol()

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _PinnedFakeRequest:
    def __init__(self, body=None, headers=None):
        self._body = body if body is not None else {}
        self.headers = headers or {}

    async def json(self):
        return self._body


def _get_route(path, method):
    from routes.model_routes import setup_model_routes
    router = setup_model_routes(model_discovery=None)
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not found")


def _make_endpoint(**kwargs):
    base = dict(
        id="ep1",
        name="EP",
        base_url="http://localhost:9999/v1",
        api_key=None,
        is_enabled=True,
        hidden_models=None,
        cached_models=None,
        pinned_models=None,
        model_type="llm",
        supports_tools=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_patch_models_saves_pinned_models(monkeypatch):
    ep = _make_endpoint()
    db = _PinnedFakeDb([ep])
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    endpoint = _get_route("/api/model-endpoints/{ep_id}/models", "PATCH")

    request = _PinnedFakeRequest(body={"pinned_models": ["deploy-1", "deploy-1", "deploy-2"]})
    result = asyncio.run(endpoint("ep1", request))

    assert json.loads(ep.pinned_models) == ["deploy-1", "deploy-2"]
    assert result["pinned_count"] == 2


def test_patch_models_pinned_does_not_clobber_hidden(monkeypatch):
    ep = _make_endpoint(hidden_models=json.dumps(["hide-me"]))
    db = _PinnedFakeDb([ep])
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    endpoint = _get_route("/api/model-endpoints/{ep_id}/models", "PATCH")

    request = _PinnedFakeRequest(body={"pinned_models": ["deploy-1"]})
    asyncio.run(endpoint("ep1", request))

    assert json.loads(ep.hidden_models) == ["hide-me"]
    assert json.loads(ep.pinned_models) == ["deploy-1"]


def test_get_models_returns_pinned_when_probe_empty(monkeypatch):
    ep = _make_endpoint(pinned_models=json.dumps(["deploy-1"]))
    db = _PinnedFakeDb([ep])
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(model_routes, "_probe_endpoint", lambda *a, **k: [])
    endpoint = _get_route("/api/model-endpoints/{ep_id}/models", "GET")

    result = endpoint("ep1", _PinnedFakeRequest())

    ids = [row["id"] for row in result]
    assert ids == ["deploy-1"]
    assert result[0]["is_pinned"] is True


def test_reprobe_preserves_pinned_models(monkeypatch):
    ep = _make_endpoint(pinned_models=json.dumps(["deploy-1"]))
    db = _PinnedFakeDb([ep])
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(model_routes, "_probe_endpoint", lambda *a, **k: ["m1"])
    monkeypatch.setattr(model_routes, "_is_chat_model", lambda m: True)
    monkeypatch.setattr(
        model_routes, "_probe_single_model", lambda *a, **k: {"status": "ok"}
    )
    endpoint = _get_route("/api/model-endpoints/{ep_id}/probe", "GET")

    response = endpoint("ep1", _PinnedFakeRequest())

    async def _drain():
        async for _ in response.body_iterator:
            pass

    asyncio.run(_drain())

    # Probe rewrites cached/hidden but must never touch admin-pinned IDs.
    assert json.loads(ep.pinned_models) == ["deploy-1"]
    assert json.loads(ep.cached_models) == ["m1"]


def test_visible_models_handles_malformed_strings():
    # Non-JSON cached/pinned strings are treated as comma/newline lists and
    # never raise; a malformed hidden string is normalized too.
    result = _visible_models("a,b", "b", "{bad json")
    assert isinstance(result, list)
    assert result == ["a", "{bad json"]
    assert _visible_models("", None, "") == []
    assert _visible_models("only-cached", None, None) == ["only-cached"]


def _create_form_kwargs(**overrides):
    """Defaults for every Form() param create_model_endpoint reads directly.

    Calling the route as a plain function bypasses FastAPI form parsing, so the
    Form() sentinels must be replaced with real strings.
    """
    kwargs = dict(
        name="",
        api_key="",
        skip_probe="true",  # avoid any network probe in unit tests
        require_models="false",
        model_type="llm",
        supports_tools="",
        pinned_models="",
        container_local="false",
        shared="true",
    )
    kwargs.update(overrides)
    return kwargs


def _patch_create_deps(monkeypatch, db):
    import src.auth_helpers as auth_helpers
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(model_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(model_routes, "ModelEndpoint", _RecordingEndpoint)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda b: b)
    monkeypatch.setattr(model_routes, "_rewrite_loopback_for_docker", lambda b, **k: b)
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {"default_endpoint_id": "exists"})
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda u: u)
    monkeypatch.setattr(auth_helpers, "get_current_user", lambda req: None)


def test_post_creates_endpoint_with_pinned_models(monkeypatch):
    db = _PinnedFakeDb([])  # no existing row → fresh create path
    _patch_create_deps(monkeypatch, db)
    create = _get_route("/api/model-endpoints", "POST")

    result = create(
        _PinnedFakeRequest(),
        base_url="http://host:1234/v1",
        **_create_form_kwargs(pinned_models="deploy-1, deploy-1\ndeploy-2"),
    )

    assert result["pinned_models"] == ["deploy-1", "deploy-2"]
    assert result["models"] == ["deploy-1", "deploy-2"]
    assert result["online"] is True
    # Persisted onto the created row.
    assert len(db.added) == 1
    assert json.loads(db.added[0].pinned_models) == ["deploy-1", "deploy-2"]


def test_post_dedupe_existing_merges_and_returns_pinned(monkeypatch):
    existing = _make_endpoint(
        cached_models=json.dumps(["m1"]),
        hidden_models=None,
        pinned_models=json.dumps(["old-pin"]),
    )
    db = _PinnedFakeDb([existing])
    _patch_create_deps(monkeypatch, db)
    create = _get_route("/api/model-endpoints", "POST")

    result = create(
        _PinnedFakeRequest(),
        base_url="http://host:1234/v1",
        **_create_form_kwargs(pinned_models="new-pin"),
    )

    assert result["existing"] is True
    # Incoming pin merged onto the existing pins (no clobber, order preserved).
    assert json.loads(existing.pinned_models) == ["old-pin", "new-pin"]
    assert result["pinned_models"] == ["old-pin", "new-pin"]
    # models = cached + pinned - hidden, visible merged list.
    assert result["models"] == ["m1", "old-pin", "new-pin"]
    # No new row created on the dedupe path.
    assert db.added == []


def test_post_dedupe_existing_does_not_clobber_pinned_when_omitted(monkeypatch):
    existing = _make_endpoint(
        cached_models=json.dumps(["m1"]),
        pinned_models=json.dumps(["keep-me"]),
    )
    db = _PinnedFakeDb([existing])
    _patch_create_deps(monkeypatch, db)
    create = _get_route("/api/model-endpoints", "POST")

    result = create(
        _PinnedFakeRequest(),
        base_url="http://host:1234/v1",
        **_create_form_kwargs(),  # pinned_models defaults to ""
    )

    assert json.loads(existing.pinned_models) == ["keep-me"]
    assert result["pinned_models"] == ["keep-me"]
    assert db.committed == 0  # nothing to persist
