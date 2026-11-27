import sys
import types
from unittest.mock import MagicMock

import httpx

from src.model_discovery import (
    ModelDiscovery,
    get_provider_presets,
    match_provider_preset,
    validate_provider_setup,
)
from tests.helpers.import_state import clear_fake_endpoint_resolver_modules, preserve_import_state

with preserve_import_state("core.database", "src.database", "core.session_manager", "routes.model_routes"):
    clear_fake_endpoint_resolver_modules()
    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
            "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        _core_db.utcnow_naive = MagicMock()
        sys.modules["core.database"] = _core_db

    import routes.model_routes as model_routes
    import src.endpoint_resolver as endpoint_resolver


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


def test_get_provider_presets_covers_local_and_hosted():
    all_presets = get_provider_presets()
    local = get_provider_presets("local")
    hosted = get_provider_presets("hosted")

    assert len(all_presets) == len(local) + len(hosted)
    local_ids = {p["id"] for p in local}
    hosted_ids = {p["id"] for p in hosted}
    assert {"ollama", "lmstudio", "llamacpp", "vllm", "sglang"}.issubset(local_ids)
    assert {"openai", "anthropic", "google", "openrouter", "groq", "deepseek"}.issubset(hosted_ids)


def test_validate_provider_setup_catches_missing_key_prefix_and_missing_v1():
    missing_key = validate_provider_setup("https://api.openai.com/v1", api_key="")
    assert missing_key["valid"] is False
    assert missing_key["provider"] == "openai"
    assert any("OPENAI_API_KEY" in w for w in missing_key["warnings"])

    bad_prefix = validate_provider_setup("https://api.anthropic.com/v1", api_key="sk-wrong-123")
    assert bad_prefix["valid"] is False
    assert any("sk-ant-" in w for w in bad_prefix["warnings"])

    missing_v1 = validate_provider_setup("https://api.openai.com", api_key="sk-valid123")
    assert missing_v1["valid"] is False
    assert any("/v1" in w for w in missing_v1["warnings"])

    valid_local = validate_provider_setup("http://localhost:11434/v1", detected_provider="ollama")
    assert valid_local["valid"] is True
    assert valid_local["suggested_name"] == "Ollama (localhost:11434)"
    assert valid_local["endpoint_kind"] == "local"


def test_fingerprint_and_check_port_detect_ollama_sglang_and_vllm(monkeypatch):
    discovery = ModelDiscovery(default_host="localhost")

    def fake_ollama_get(url, timeout=None):
        if url.endswith("/v1/models"):
            return _FakeResponse({"data": []})
        if url.endswith("/api/tags"):
            return _FakeResponse({"models": [{"name": "qwen2.5:14b"}]})
        if url.endswith("/api/version"):
            return _FakeResponse({"version": "0.6.2"})
        return _FakeResponse({}, ok=False)

    monkeypatch.setattr("src.model_discovery.httpx.get", fake_ollama_get)
    assert discovery._fingerprint_provider("localhost", 11434) == "ollama"
    ollama_item = discovery._check_port("localhost", 11434)
    assert ollama_item is not None
    assert ollama_item["provider"] == "ollama"
    assert ollama_item["models"] == ["qwen2.5:14b"]
    assert ollama_item["suggested_name"] == "Ollama (localhost:11434)"

    def fake_sglang_get(url, timeout=None):
        if url.endswith("/get_model_info"):
            return _FakeResponse({"model_path": "/models/Llama-3.3-70B"})
        return _FakeResponse({}, ok=False)

    monkeypatch.setattr("src.model_discovery.httpx.get", fake_sglang_get)
    assert discovery._fingerprint_provider("localhost", 30000) == "sglang"

    def fake_vllm_get(url, timeout=None):
        if url.endswith(":8000/version"):
            return _FakeResponse({"version": "0.8.4"})
        return _FakeResponse({}, ok=False)

    monkeypatch.setattr("src.model_discovery.httpx.get", fake_vllm_get)
    assert discovery._fingerprint_provider("localhost", 8000) == "vllm"


def test_discover_models_includes_sglang_default_port_30000(monkeypatch):
    discovery = ModelDiscovery(default_host="localhost")
    scanned = []
    monkeypatch.setattr(discovery, "_check_port", lambda h, p: scanned.append(p) or None)
    monkeypatch.setattr("src.model_discovery.discover_tailscale_hosts", lambda: [])
    discovery.discover_models()
    assert 30000 in scanned


def test_assess_endpoint_setup_and_capabilities_runs_live_chat_and_tool_checks(monkeypatch):
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))

    calls = []

    def fake_probe_single(base, api_key, model_id, timeout=10, with_tools=False):
        calls.append((model_id, with_tools))
        if with_tools:
            return {"status": "fail", "latency_ms": 42, "error": "HTTP 400: tools not supported"}
        return {"status": "ok", "latency_ms": 35}

    monkeypatch.setattr(model_routes, "_probe_single_model", fake_probe_single)
    monkeypatch.setattr(model_routes, "_fingerprint_local_base_url", lambda url: "llamacpp")

    report = model_routes._assess_endpoint_setup_and_capabilities(
        "http://localhost:8080/v1",
        None,
        endpoint_kind="local",
        models=["gemma-3-12b-it-Q4_K_M"],
        is_online=True,
        run_live_probe=True,
    )
    assert report["detected_provider"] == "llamacpp"
    assert report["suggested_name"] == "llama.cpp (llama-server) (localhost:8080)"
    caps = report["capabilities"]
    assert caps["models_listed"] is True
    assert caps["chat_completions"] is True
    assert caps["tool_calling"] is False
    assert caps["probe_model"] == "gemma-3-12b-it-Q4_K_M"
    assert "tools not supported" in (caps["probe_error"] or "")
    assert calls == [
        ("gemma-3-12b-it-Q4_K_M", False),
        ("gemma-3-12b-it-Q4_K_M", True),
    ]
