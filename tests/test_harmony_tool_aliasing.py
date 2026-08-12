"""Tools whose names collide with harmony built-ins must be aliased for gpt-oss.

gpt-oss (harmony format) ships BUILT-IN tools named `python` and `browser`,
invoked with the raw body as the argument (`to=python` + bare source), while
custom functions use `to=functions.NAME` + JSON. Exposing our own tool under a
built-in's name makes the model answer with the built-in convention: it emits
raw code, the server tries to parse it as JSON, and the request dies with
"error parsing tool call: raw='import sys, ...'". Streaming is worse — Ollama
truncates the stream instead of reporting it, so the turn looks like an empty
response and the agent loop reads it as a stall.

Measured on gpt-oss:20b via Ollama /v1 with a fixed agentic prompt:
python+bash as-is 2/6, python renamed 5/6, both renamed 6/6.

The aliasing is transport-only and gpt-oss-only: every other model's tool
schemas must pass through untouched, and real tool names must come back out.
"""
from src.llm_core import (
    _alias_harmony_tools,
    _unalias_harmony_tool_name,
    _is_harmony_model,
)


def _tools(*names):
    return [
        {"type": "function", "function": {"name": n, "parameters": {}}}
        for n in names
    ]


def _names(tools):
    return [t["function"]["name"] for t in tools]


def test_gpt_oss_colliding_names_are_aliased():
    out = _alias_harmony_tools(_tools("python", "bash", "web_search"), "gpt-oss:20b")
    assert _names(out) == ["run_python_code", "run_shell_command", "web_search"]


def test_non_harmony_models_are_untouched():
    tools = _tools("python", "bash", "web_search")
    for model in ("qwen3-coder:30b", "gemma4:12b", "claude-opus-5", "gpt-4o", "llama-3.3"):
        out = _alias_harmony_tools(tools, model)
        assert _names(out) == ["python", "bash", "web_search"], model
        assert out is tools, f"{model} should get the same list object, not a copy"


def test_aliasing_does_not_mutate_the_caller_list():
    tools = _tools("python")
    _alias_harmony_tools(tools, "gpt-oss:20b")
    assert _names(tools) == ["python"], "caller's schema list must not be mutated"


def test_response_names_map_back_for_gpt_oss():
    assert _unalias_harmony_tool_name("run_python_code", "gpt-oss:20b") == "python"
    assert _unalias_harmony_tool_name("run_shell_command", "gpt-oss:20b") == "bash"
    # Unrelated names pass through untouched.
    assert _unalias_harmony_tool_name("web_search", "gpt-oss:20b") == "web_search"


def test_response_names_untouched_for_other_models():
    # A non-harmony model that genuinely has a tool called run_python_code
    # must not have it rewritten to `python`.
    assert _unalias_harmony_tool_name("run_python_code", "qwen3-coder:30b") == "run_python_code"


def test_harmony_detection():
    assert _is_harmony_model("gpt-oss:20b") is True
    assert _is_harmony_model("GPT-OSS:120B") is True
    assert _is_harmony_model("qwen3-coder:30b") is False
    assert _is_harmony_model("") is False
    assert _is_harmony_model(None) is False


def test_empty_and_none_tools_are_safe():
    assert _alias_harmony_tools(None, "gpt-oss:20b") is None
    assert _alias_harmony_tools([], "gpt-oss:20b") == []
