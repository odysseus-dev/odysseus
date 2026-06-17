"""Provider / endpoint resolution tests against the REAL resolver.

`test_endpoint_resolver.py` deliberately *copies* the pure functions to avoid
import side effects. The downside is that those copies silently drift from the
shipped code — they already lag `src/endpoint_resolver.py` (no OpenRouter
headers, no `anthropic.com` host matching). This module instead imports the
real `src.endpoint_resolver`, so it fails the moment the shipped resolution
logic stops matching documented provider behavior. `conftest.py` stubs the
heavy deps (sqlalchemy, `src.database`), so the import is side-effect free.

Covers every provider named in ROADMAP.md "Provider setup/probing audit":
Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, DeepSeek — plus Ollama
(local + cloud) and the Tailscale self-host fallback.
"""
import json
import socket
import types

import pytest

from src import endpoint_resolver as er


@pytest.fixture
def no_dns(monkeypatch):
    """Neutralize resolve_url so URL-building tests never touch DNS/Tailscale.

    build_chat_url/build_models_url call the module-global resolve_url first;
    patching it on the module makes those calls a no-op (functions resolve
    globals by name at call time).
    """
    monkeypatch.setattr(er, "resolve_url", lambda u: u)


# (id, base_url, expected_chat_url, expected_models_url)
PROVIDER_CASES = [
    ("openai", "https://api.openai.com/v1",
     "https://api.openai.com/v1/chat/completions",
     "https://api.openai.com/v1/models"),
    ("openai_pathless", "https://api.openai.com",
     "https://api.openai.com/v1/chat/completions",
     "https://api.openai.com/v1/models"),
    ("anthropic", "https://api.anthropic.com",
     "https://api.anthropic.com/v1/messages",
     "https://api.anthropic.com/v1/models"),
    # Anthropic base that already carries /v1 must not become /v1/v1/messages.
    ("anthropic_v1", "https://api.anthropic.com/v1",
     "https://api.anthropic.com/v1/messages",
     "https://api.anthropic.com/v1/models"),
    ("openrouter", "https://openrouter.ai/api/v1",
     "https://openrouter.ai/api/v1/chat/completions",
     "https://openrouter.ai/api/v1/models"),
    ("groq", "https://api.groq.com/openai/v1",
     "https://api.groq.com/openai/v1/chat/completions",
     "https://api.groq.com/openai/v1/models"),
    ("nvidia", "https://integrate.api.nvidia.com/v1",
     "https://integrate.api.nvidia.com/v1/chat/completions",
     "https://integrate.api.nvidia.com/v1/models"),
    ("xai", "https://api.x.ai/v1",
     "https://api.x.ai/v1/chat/completions",
     "https://api.x.ai/v1/models"),
    ("deepseek", "https://api.deepseek.com",
     "https://api.deepseek.com/chat/completions",
     "https://api.deepseek.com/v1/models"),
    # Gemini's OpenAI-compatible surface — treated as a generic OpenAI endpoint.
    ("gemini_openai", "https://generativelanguage.googleapis.com/v1beta/openai",
     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "https://generativelanguage.googleapis.com/v1beta/openai/models"),
    ("ollama_local", "http://localhost:11434/api",
     "http://localhost:11434/api/chat",
     "http://localhost:11434/api/tags"),
    ("ollama_cloud", "https://ollama.com",
     "https://ollama.com/api/chat",
     "https://ollama.com/api/tags"),
]


@pytest.mark.parametrize(
    "base,expected", [(c[1], c[2]) for c in PROVIDER_CASES],
    ids=[c[0] for c in PROVIDER_CASES],
)
def test_build_chat_url(no_dns, base, expected):
    assert er.build_chat_url(base) == expected


@pytest.mark.parametrize(
    "base,expected", [(c[1], c[3]) for c in PROVIDER_CASES],
    ids=[c[0] for c in PROVIDER_CASES],
)
def test_build_models_url(no_dns, base, expected):
    assert er.build_models_url(base) == expected


def test_chat_url_never_double_prefixes_anthropic(no_dns):
    """Regression guard: the /v1 collapse must not produce /v1/v1/messages."""
    url = er.build_chat_url("https://api.anthropic.com/v1")
    assert "/v1/v1/" not in url
    assert url.count("/v1/messages") == 1


# ── Auth headers per provider ──

def test_headers_anthropic_uses_x_api_key():
    h = er.build_headers("secret", "https://api.anthropic.com")
    assert h["x-api-key"] == "secret"
    assert h["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in h


def test_headers_anthropic_without_key_still_sends_version():
    h = er.build_headers(None, "https://api.anthropic.com")
    assert h["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in h


@pytest.mark.parametrize("base", [
    "https://api.openai.com/v1",
    "https://api.x.ai/v1",
    "https://api.deepseek.com",
    "https://api.groq.com/openai/v1",
    "https://integrate.api.nvidia.com/v1",
    "https://generativelanguage.googleapis.com/v1beta/openai",
])
def test_headers_openai_style_use_bearer(base):
    h = er.build_headers("secret", base)
    assert h["Authorization"] == "Bearer secret"
    assert "HTTP-Referer" not in h
    assert "x-api-key" not in h


def test_headers_openrouter_adds_attribution():
    h = er.build_headers("secret", "https://openrouter.ai/api/v1")
    assert h["Authorization"] == "Bearer secret"
    # OpenRouter ranks/labels apps via these headers.
    assert h["HTTP-Referer"].startswith("https://github.com/")
    assert h["X-OpenRouter-Title"] == "Odysseus"


def test_headers_omit_authorization_when_no_key():
    assert er.build_headers(None, "https://api.openai.com/v1") == {}


# ── normalize_base: strip whatever path the user pasted ──

@pytest.mark.parametrize("raw,expected", [
    ("https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1"),
    ("https://api.openai.com/v1/completions", "https://api.openai.com/v1"),
    ("https://api.openai.com/v1/models/", "https://api.openai.com/v1"),
    ("https://api.anthropic.com/v1/messages", "https://api.anthropic.com"),
    ("http://localhost:11434/api/chat", "http://localhost:11434/api"),
    ("http://localhost:11434/api/tags", "http://localhost:11434/api"),
    ("http://localhost:11434/api/generate", "http://localhost:11434/api"),
    ("https://api.openai.com/v1/", "https://api.openai.com/v1"),
    ("  https://api.openai.com/v1  ", "https://api.openai.com/v1"),
    ("", ""),
    (None, ""),
])
def test_normalize_base(raw, expected):
    assert er.normalize_base(raw) == expected


# ── _first_chat_model: never auto-pick an embedding/tts/etc. model ──

def test_first_chat_model_skips_non_chat():
    models = ["text-embedding-ada-002", "whisper-1", "gpt-4o", "dall-e-3"]
    assert er._first_chat_model(models) == "gpt-4o"


def test_first_chat_model_falls_back_to_first_when_all_non_chat():
    models = ["text-embedding-3-large", "text-embedding-3-small"]
    assert er._first_chat_model(models) == "text-embedding-3-large"


@pytest.mark.parametrize("models", [[], None])
def test_first_chat_model_empty(models):
    assert er._first_chat_model(models) is None


# ── provider-root helpers ──

@pytest.mark.parametrize("base,expected", [
    ("https://api.anthropic.com/v1", "https://api.anthropic.com"),
    ("https://api.anthropic.com", "https://api.anthropic.com"),
    # /v1 on a non-Anthropic host (OpenAI-compatible) must be preserved.
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
])
def test_anthropic_api_root(base, expected):
    assert er._anthropic_api_root(base) == expected


@pytest.mark.parametrize("base,expected", [
    ("https://ollama.com", "https://ollama.com/api"),
    ("http://localhost:11434/api", "http://localhost:11434/api"),
    # A non-Ollama host is returned untouched.
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
])
def test_ollama_api_root(base, expected):
    assert er._ollama_api_root(base) == expected


# ── resolve_url: Tailscale self-host fallback ──
# ROADMAP flags plain-HTTP Tailscale URLs as a self-host trap; resolve_url is
# the hop that rewrites an unresolvable hostname to its Tailscale IP.

class TestResolveUrlTailscale:
    def setup_method(self):
        # The module memoizes hostname→IP; clear it so cases don't bleed.
        er._tailscale_cache.clear()

    def test_dns_success_returns_url_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            er.socket, "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("1.2.3.4", 0))],
        )
        assert er.resolve_url("http://myhost:7000/api") == "http://myhost:7000/api"

    def test_dns_failure_rewrites_to_tailscale_ip(self, monkeypatch):
        def _fail(*a, **k):
            raise socket.gaierror("no DNS")
        monkeypatch.setattr(er.socket, "getaddrinfo", _fail)
        peers = {"Peer": {"x": {
            "HostName": "myhost",
            "DNSName": "myhost.tail.ts.net.",
            "TailscaleIPs": ["100.64.0.5"],
        }}}
        monkeypatch.setattr(
            er.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(peers)),
        )
        # Port is preserved, host swapped for the Tailscale IP.
        assert er.resolve_url("http://myhost:7000/api") == "http://100.64.0.5:7000/api"

    def test_dns_failure_no_peer_match_keeps_url(self, monkeypatch):
        def _fail(*a, **k):
            raise socket.gaierror("no DNS")
        monkeypatch.setattr(er.socket, "getaddrinfo", _fail)
        monkeypatch.setattr(
            er.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps({"Peer": {}})),
        )
        assert er.resolve_url("http://myhost:7000/api") == "http://myhost:7000/api"

    def test_url_without_hostname_is_returned_as_is(self):
        assert er.resolve_url("") == ""
