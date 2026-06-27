"""Provider endpoint normalization tests.

Covers ``normalize_base`` (strip whatever path the user pasted), and the
provider-root helpers ``_anthropic_api_root`` and ``_ollama_api_root``.
"""
import pytest

from src import endpoint_resolver as er


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
