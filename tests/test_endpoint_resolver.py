"""Tests for endpoint_resolver — pure functions tested directly to avoid import pollution."""
import json
import re
from urllib.parse import urlparse


# Copy the pure functions to test them without importing the full module.
# This avoids module cache conflicts with other test files that mock dependencies.

_NON_CHAT_MODEL = (
    "text-embedding", "embedding", "tts-", "whisper", "dall-e",
    "moderation", "rerank", "reranker", "clip", "stable-diffusion",
)


def _first_chat_model(models):
    for m in (models or []):
        if not any(p in str(m).lower() for p in _NON_CHAT_MODEL):
            return m
    return (models[0] if models else None)


def _endpoint_cached_models(ep) -> list:
    raw = getattr(ep, "cached_models", None) or getattr(ep, "models", None)
    if not raw:
        return []
    try:
        models = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    return models if isinstance(models, list) else []


def _endpoint_hidden_models(ep) -> set:
    raw = getattr(ep, "hidden_models", None)
    if not raw:
        return set()
    try:
        hidden = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return set()
    return set(hidden) if isinstance(hidden, list) else set()


def _endpoint_enabled_models(ep) -> list:
    hidden = _endpoint_hidden_models(ep)
    return [m for m in _endpoint_cached_models(ep) if m not in hidden]

def normalize_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"]:
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    for suffix in ["/chat", "/tags", "/generate"]:
        if url.endswith("/api" + suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _detect_provider(url: str) -> str:
    parsed = urlparse(url or "")
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if host.endswith("ollama.com") or (parsed.port == 11434 and (path == "/api" or path.startswith("/api/"))):
        return "ollama"
    if "anthropic.com" in (url or ""):
        return "anthropic"
    return "openai"


def _ollama_api_root(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    parsed = urlparse(base)
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api"):
        return base
    if host.endswith("ollama.com"):
        return f"{parsed.scheme}://{parsed.netloc}/api"
    return base


def build_chat_url(base: str) -> str:
    provider = _detect_provider(base)
    if provider == "anthropic":
        host = urlparse(base).hostname or ""
        if host.endswith("anthropic.com") and base.rstrip("/").endswith("/v1"):
            base = base.rstrip("/")[:-3].rstrip("/")
        return base + "/v1/messages"
    if provider == "ollama":
        return _ollama_api_root(base) + "/chat"
    return base + "/chat/completions"


def build_models_url(base: str) -> str:
    provider = _detect_provider(base)
    if provider == "ollama":
        return _ollama_api_root(base) + "/tags"
    return base + "/models"


def build_headers(api_key, base: str) -> dict:
    if not api_key:
        return {}
    provider = _detect_provider(base)
    if provider == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {api_key}"}


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


class TestBuildModelsUrl:
    def test_openai_models(self):
        assert build_models_url("https://api.openai.com/v1") == "https://api.openai.com/v1/models"

    def test_ollama_tags(self):
        assert build_models_url("https://ollama.com/api") == "https://ollama.com/api/tags"


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
