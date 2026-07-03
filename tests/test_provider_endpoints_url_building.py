"""Provider endpoint URL-building tests.

Covers ``build_chat_url`` and ``build_models_url`` for every provider named in
ROADMAP.md: Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, DeepSeek, Ollama
(local + cloud).
"""
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
