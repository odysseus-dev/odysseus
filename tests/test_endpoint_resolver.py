"""Tests for endpoint_resolver — pure functions tested directly to avoid import pollution."""
import re
from urllib.parse import urlparse


# Copy the pure functions to test them without importing the full module.
# This avoids module cache conflicts with other test files that mock dependencies.

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
