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


def test_normal_model_payload_keeps_temperature_above_one(monkeypatch):
    # OpenAI/local providers may validly use temperatures above 1.0; the clamp
    # is Anthropic-only and must not touch this path.
    payload = _capture_openai_payload(monkeypatch, "gpt-4o", 1.2)
    assert payload["temperature"] == 1.2


def test_chatgpt_subscription_payload_uses_max_output_tokens():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.1-codex",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=37,
    )

    assert payload["max_output_tokens"] == 37


def test_chatgpt_subscription_payload_omits_empty_max_output_tokens():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.1-codex",
        [{"role": "user", "content": "Say OK"}],
        temperature=0.2,
        max_tokens=0,
    )

    assert "max_output_tokens" not in payload


def _anthropic_payload(temperature):
    return llm_core._build_anthropic_payload(
        "claude-3-5-sonnet",
        [{"role": "user", "content": "Hi"}],
        temperature,
        max_tokens=5,
    )


def test_anthropic_payload_clamps_above_one():
    # Anthropic rejects temperature > 1.0 (e.g. the Nietzsche preset's 1.2).
    assert _anthropic_payload(1.2)["temperature"] == 1.0


def test_anthropic_payload_keeps_in_range():
    assert _anthropic_payload(0.7)["temperature"] == 0.7


def test_anthropic_payload_clamps_negative():
    assert _anthropic_payload(-0.5)["temperature"] == 0.0


def test_anthropic_payload_none_temperature_does_not_crash():
    payload = _anthropic_payload(None)
    assert payload["temperature"] is None
