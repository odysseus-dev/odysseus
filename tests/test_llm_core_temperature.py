"""Regression tests: OpenAI reasoning models reject a non-default temperature.

o1/o3/o4/gpt-5 only accept the default temperature (1); sending an explicit
value — even 0.0 — returns HTTP 400 "Only the default (1) value is supported".
The OpenAI-compatible payload builders must omit the temperature field for these
models so chat (with a non-default preset) and endpoint probing don't break.
"""
import httpx
import pytest

from src import llm_core


@pytest.mark.parametrize(
    "model",
    ["o1", "o1-mini", "o3", "o3-mini", "o4-mini", "gpt-5", "gpt-5-mini",
     "openrouter/openai/o3-mini", "OpenAI/GPT-5"],
)
def test_reasoning_models_restrict_temperature(model):
    assert llm_core._restricts_temperature(model) is True


@pytest.mark.parametrize(
    "model",
    ["gpt-4o", "gpt-4.1", "gpt-3.5-turbo", "gpt-4.5-preview",
     "claude-3-5-sonnet", "llama3.1", "", None],
)
def test_normal_models_allow_temperature(model):
    assert llm_core._restricts_temperature(model) is False


def _capture_openai_payload(monkeypatch, model, temperature):
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
        "https://api.openai.com/v1/chat/completions",
        model,
        [{"role": "user", "content": "Say OK"}],
        temperature=temperature,
        max_tokens=5,
    )
    assert result == "OK"
    return seen["json"]


def test_reasoning_model_payload_omits_temperature(monkeypatch):
    payload = _capture_openai_payload(monkeypatch, "o3-mini", 0.0)
    assert "temperature" not in payload
    # Reasoning models also use max_completion_tokens, which must survive.
    assert payload["max_completion_tokens"] == 5


def test_normal_model_payload_keeps_temperature(monkeypatch):
    payload = _capture_openai_payload(monkeypatch, "gpt-4o", 0.2)
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 5
