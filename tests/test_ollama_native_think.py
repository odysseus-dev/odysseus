"""Thinking must be suppressed on the NATIVE Ollama endpoint too.

`think: false` is already injected on Ollama's /v1 surface, but that surface
ignores it: measured on Ollama 0.32.4 with qwen3:14b, the same short question
generated 395 tokens there against 4 on /api/chat with the flag set. The native
payload builder never emitted the field at all, so no code path actually
suppressed reasoning.

The consequence is not just slowness. Odysseus strips thinking from the round
response and accumulates only native tool calls, so a round whose budget is
consumed by reasoning ends with 0 chars, 0 native calls and 0 tool blocks — the
agent appears to silently do nothing.
"""

import pytest

from src.llm_core import _build_ollama_payload


MSGS = [{"role": "user", "content": "hello"}]


def _payload(model, **kw):
    return _build_ollama_payload(
        model, MSGS, temperature=0.7, max_tokens=256, **kw
    )


@pytest.mark.parametrize("model", ["qwen3:14b", "qwq:32b", "deepseek-r1:7b"])
def test_thinking_models_get_think_false(model):
    assert _payload(model)["think"] is False


@pytest.mark.parametrize("model", ["llama3.2:latest", "mistral:7b"])
def test_non_thinking_models_are_left_alone(model):
    """Do not send a field a model has no use for."""
    assert "think" not in _payload(model)


def test_suppression_does_not_disturb_the_rest_of_the_payload():
    """The flag is additive — options, tools and messages are untouched."""
    tools = [{"type": "function", "function": {"name": "web_fetch", "parameters": {}}}]
    p = _payload("qwen3:14b", tools=tools)

    assert p["think"] is False
    assert p["model"] == "qwen3:14b"
    assert p["messages"]
    assert p["options"]["temperature"] == 0.7
    assert p["options"]["num_predict"] == 256
    assert p["tools"]
