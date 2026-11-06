"""Provider endpoint auth-header tests.

Covers ``build_headers`` for every provider: Anthropic (x-api-key + version
header), OpenAI-style providers (Bearer token), OpenRouter (Bearer + attribution
headers), and the no-key case.
"""
import pytest

from src import endpoint_resolver as er
from src.constants import APP_VERSION


@pytest.mark.parametrize("base, expected", [
    ("https://api.perplexity.ai", f"odysseus/{APP_VERSION}"),
    ("https://api.perplexity.ai.example.com", None),
])
def test_model_probe_attribution_is_provider_scoped(monkeypatch, base, expected):
    import httpx
    from routes.model_routes import _probe_endpoint

    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "sonar"}]})

    monkeypatch.setattr(er, "resolve_url", lambda url: url)
    def get(url, *, verify=True, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(respond), verify=verify) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(httpx, "get", get)
    assert _probe_endpoint(base, "fixture-key") == ["sonar"]

    assert len(seen) == 1
    assert str(seen[0].url) == f"{base}/models"
    assert seen[0].headers["Authorization"] == "Bearer fixture-key"
    assert seen[0].headers.get_list("X-Pplx-Integration") == ([expected] if expected else [])


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
