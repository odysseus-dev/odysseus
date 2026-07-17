"""Regression tests: Moonshot/Kimi temperature detection and payload behavior.

Moonshot kimi-k2.5+ models reject custom temperature values; the payload
builder must detect the Moonshot provider and omit temperature for the affected
model family. Self-hosted Kimi deployments (non-Moonshot URL) must keep the
caller-specified temperature unchanged.
"""
import httpx
import pytest

from src import llm_core


@pytest.mark.parametrize(
    "model",
    [
        "kimi-k2.5",
        "kimi-k2.6",
        "moonshot/kimi-k2.6",
        "kimi-k2.6-preview",
    ],
)
def test_moonshot_k2_5_plus_uses_fixed_temperature(model):
    assert llm_core._moonshot_rejects_custom_temperature("moonshot", model)


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "kimi-k2.6"),
        ("moonshot", "kimi-k2-0905-preview"),
        ("moonshot", "kimi-k2-thinking"),
        ("moonshot", "kimi-k2.50"),
        ("moonshot", None),
    ],
)
def test_other_models_keep_temperature(provider, model):
    assert not llm_core._moonshot_rejects_custom_temperature(provider, model)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.moonshot.ai/v1/chat/completions",
        "https://api.moonshot.cn/v1/chat/completions",
    ],
)
def test_moonshot_provider_detection(url):
    assert llm_core._detect_provider(url) == "moonshot"


def _capture_openai_payload(
    monkeypatch,
    model,
    temperature,
    url="https://api.openai.com/v1/chat/completions",
):
    """Run a synchronous OpenAI-compatible call and return the posted JSON body."""
    llm_core._response_cache.clear()
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    result = llm_core.llm_call(
        url,
        model,
        [{"role": "user", "content": "Say OK"}],
        temperature=temperature,
        max_tokens=5,
    )
    assert result == "OK"
    return seen["json"]


def test_moonshot_k2_6_payload_omits_temperature(monkeypatch):
    payload = _capture_openai_payload(
        monkeypatch,
        "kimi-k2.6",
        0.7,
        url="https://api.moonshot.ai/v1/chat/completions",
    )
    assert "temperature" not in payload


def test_self_hosted_kimi_k2_6_payload_keeps_temperature(monkeypatch):
    payload = _capture_openai_payload(
        monkeypatch,
        "kimi-k2.6",
        0.7,
        url="http://localhost:8000/v1/chat/completions",
    )
    assert payload["temperature"] == 0.7
