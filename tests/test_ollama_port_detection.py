"""Pin path-aware Ollama detection for URLs on port 11434.

Port 11434 is Ollama's default, but it is not Ollama-exclusive.
LM Studio, vLLM, and other OpenAI-compatible servers commonly run on the same
port. A URL on port 11434 with a /v1 path must remain OpenAI-compatible;
only explicit /api or /api/... paths (and ollama.com) are native Ollama.
"""
import pytest

from src import llm_core, endpoint_resolver
from src.endpoint_resolver import build_chat_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    """Stub out resolve_url so tests are offline and deterministic."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda u: u)


# ---------------------------------------------------------------------------
# _is_ollama_native_url: /v1 on port 11434 is NOT native Ollama
# ---------------------------------------------------------------------------

class TestIsOllamaNativeUrlRejectsV1Paths:
    """Port alone is not enough — /v1 paths are OpenAI-compatible."""

    def test_localhost_v1(self):
        assert not llm_core._is_ollama_native_url("http://localhost:11434/v1")

    def test_localhost_v1_trailing_slash(self):
        assert not llm_core._is_ollama_native_url("http://localhost:11434/v1/")

    def test_localhost_v1_chat_completions(self):
        assert not llm_core._is_ollama_native_url("http://localhost:11434/v1/chat/completions")

    def test_loopback_ip_v1(self):
        assert not llm_core._is_ollama_native_url("http://127.0.0.1:11434/v1")

    def test_named_host_v1(self):
        assert not llm_core._is_ollama_native_url("http://ollama:11434/v1")

    def test_lan_ip_v1(self):
        assert not llm_core._is_ollama_native_url("http://192.168.1.100:11434/v1")

    def test_lan_ip_v1_chat_completions(self):
        assert not llm_core._is_ollama_native_url("http://192.168.1.100:11434/v1/chat/completions")


# ---------------------------------------------------------------------------
# _is_ollama_native_url: /api paths and ollama.com ARE native Ollama
# ---------------------------------------------------------------------------

class TestIsOllamaNativeUrlAcceptsNativePaths:
    def test_localhost_api(self):
        assert llm_core._is_ollama_native_url("http://localhost:11434/api")

    def test_localhost_api_trailing_slash(self):
        assert llm_core._is_ollama_native_url("http://localhost:11434/api/")

    def test_localhost_api_chat(self):
        assert llm_core._is_ollama_native_url("http://localhost:11434/api/chat")

    def test_localhost_api_generate(self):
        assert llm_core._is_ollama_native_url("http://localhost:11434/api/generate")

    def test_ollama_com(self):
        assert llm_core._is_ollama_native_url("https://ollama.com")

    def test_ollama_com_api(self):
        assert llm_core._is_ollama_native_url("https://ollama.com/api")


# ---------------------------------------------------------------------------
# build_chat_url: port 11434 + /v1 → OpenAI-compatible /chat/completions
# ---------------------------------------------------------------------------

class TestBuildChatUrlPort11434V1IsOpenAICompat:
    def test_localhost_v1(self):
        assert build_chat_url("http://localhost:11434/v1") == "http://localhost:11434/v1/chat/completions"

    def test_loopback_ip_v1(self):
        assert build_chat_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1/chat/completions"

    def test_lan_ip_v1(self):
        assert build_chat_url("http://192.168.1.100:11434/v1") == "http://192.168.1.100:11434/v1/chat/completions"


# ---------------------------------------------------------------------------
# build_chat_url: native Ollama /api → /api/chat
# ---------------------------------------------------------------------------

class TestBuildChatUrlNativeOllamaRoutesToApiChat:
    def test_localhost_api(self):
        assert build_chat_url("http://localhost:11434/api") == "http://localhost:11434/api/chat"

    def test_ollama_com(self):
        assert build_chat_url("https://ollama.com") == "https://ollama.com/api/chat"

    def test_ollama_com_api(self):
        assert build_chat_url("https://ollama.com/api") == "https://ollama.com/api/chat"
