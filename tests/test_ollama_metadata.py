"""Ollama Auto tool transport and actual runner context regressions (#3490)."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from src import agent_loop, model_context, ollama_metadata


def _response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "http://model.test"))


@pytest.fixture(autouse=True)
def offline_metadata(monkeypatch):
    monkeypatch.setattr(ollama_metadata.httpx, "get", lambda *a, **k: _response({}, 404))


@pytest.fixture
def endpoint_flag(monkeypatch):
    from core import database

    row = SimpleNamespace(id="ollama", supports_tools=None)

    class Query:
        def filter(self, *args):
            return self

        def all(self):
            return [row]

    class Db:
        def query(self, *args):
            return Query()

        def close(self):
            pass

    monkeypatch.setattr(database, "SessionLocal", Db)
    return row


@pytest.mark.parametrize("endpoint", [
    "http://host.docker.internal:11434/v1",
    "http://127.0.0.1:11434/v1/chat/completions",
    "http://127.0.0.1:11434/api/chat",
])
def test_auto_uses_reported_tools_for_custom_qwen_alias(monkeypatch, endpoint_flag, endpoint):
    calls = []

    def show(url, **kwargs):
        calls.append((url, kwargs))
        return _response({"capabilities": ["completion", "tools", "thinking"]})

    monkeypatch.setattr(ollama_metadata.httpx, "post", show)
    assert agent_loop._agent_route_tool_mode(endpoint, "qwen3:14b-32k-test")[0] is True
    assert calls[0][0].endswith("/api/show")
    assert calls[0][1]["json"] == {"model": "qwen3:14b-32k-test"}


@pytest.mark.parametrize("flag", [True, False])
def test_explicit_endpoint_flag_skips_probe(monkeypatch, endpoint_flag, flag):
    endpoint_flag.supports_tools = flag

    def unexpected(*args, **kwargs):
        pytest.fail("An explicit On/Off setting must not probe Auto capabilities")

    monkeypatch.setattr(ollama_metadata.httpx, "post", unexpected)
    assert agent_loop._agent_route_tool_mode("http://localhost:11434/v1", "qwen3:14b")[0] is flag


@pytest.mark.parametrize("payload,status", [
    ({"capabilities": ["completion"]}, 200),
    ({"capabilities": []}, 200),
    ({"capabilities": "tools"}, 200),
    ({"capabilities": [{}]}, 200),
    ({}, 200),
    ({}, 404),
])
def test_auto_preserves_textual_fallback_without_reported_tools(
    monkeypatch, endpoint_flag, payload, status,
):
    monkeypatch.setattr(ollama_metadata.httpx, "post", lambda *args, **kwargs: _response(payload, status))
    assert agent_loop._agent_route_tool_mode("http://localhost:11434/v1", "qwen3:14b")[0] is False


def test_auto_timeout_preserves_textual_fallback(monkeypatch, endpoint_flag):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("offline")

    monkeypatch.setattr(ollama_metadata.httpx, "post", timeout)
    assert agent_loop._agent_route_tool_mode("http://localhost:11434/v1", "qwen3:14b")[0] is False


def test_metadata_uses_selected_route_headers(monkeypatch):
    seen = {}

    def show(url, **kwargs):
        seen.update(kwargs)
        return _response({"capabilities": ["completion", "tools"]})

    monkeypatch.setattr(ollama_metadata.httpx, "post", show)
    assert ollama_metadata.ollama_tool_support(
        "https://ollama.com/api/chat", "custom-alias", {"Authorization": "Bearer test-key"},
    ) is True
    assert seen["headers"] == {"Authorization": "Bearer test-key"}


@pytest.mark.parametrize("endpoint", [
    "http://localhost:8000/v1", "https://api.openai.com/v1", "http://localhost:bad/v1",
])
def test_unrelated_or_invalid_endpoint_is_not_probed(monkeypatch, endpoint):
    def unexpected(*args, **kwargs):
        pytest.fail("This endpoint is not an Ollama metadata candidate")

    monkeypatch.setattr(ollama_metadata.httpx, "get", unexpected)
    monkeypatch.setattr(ollama_metadata.httpx, "post", unexpected)
    assert ollama_metadata.ollama_tool_support(endpoint, "qwen3") is None
    assert ollama_metadata.ollama_context_length(endpoint, "qwen3") is None


def test_runtime_context_beats_family_maximum_and_refreshes(monkeypatch):
    contexts = iter([32768, 8192])
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda _: "local")

    def running(url, **kwargs):
        assert url == "http://host.docker.internal:11434/api/ps"
        return _response({"models": [
            {"name": "other-qwen3", "context_length": 131072},
            {"name": "qwen3:14b-32k-test", "context_length": next(contexts)},
        ]})

    monkeypatch.setattr(ollama_metadata.httpx, "get", running)
    endpoint = "http://host.docker.internal:11434/v1/chat/completions"
    assert model_context.get_context_length_known(endpoint, "qwen3:14b-32k-test") == (32768, True)
    assert model_context.get_context_length_known(endpoint, "qwen3:14b-32k-test") == (8192, True)


@pytest.mark.parametrize("parameters", ["num_ctx 32768\nnum_predict 512", {"num_ctx": 32768}])
def test_unloaded_custom_model_uses_modelfile_context(monkeypatch, parameters):
    monkeypatch.setattr(ollama_metadata.httpx, "get", lambda *a, **k: _response({"models": []}))
    monkeypatch.setattr(ollama_metadata.httpx, "post", lambda *a, **k: _response({
        "capabilities": ["completion", "tools"],
        "parameters": parameters,
        "model_info": {"qwen3.context_length": 131072},
    }))
    assert ollama_metadata.ollama_context_length("http://localhost:11434/api", "custom-alias") == 32768


def test_running_latest_alias_matches_bare_name(monkeypatch):
    monkeypatch.setattr(ollama_metadata.httpx, "get", lambda *a, **k: _response({
        "models": [{"model": "qwen3:latest", "context_length": 32768}],
    }))
    assert ollama_metadata.ollama_context_length("http://localhost:11434/v1", "qwen3") == 32768


def test_missing_native_metadata_keeps_known_context_fallback(monkeypatch):
    monkeypatch.setattr(model_context, "_configured_endpoint_kind", lambda _: "local")
    monkeypatch.setattr(ollama_metadata.httpx, "get", lambda *a, **k: _response({}, 404))
    monkeypatch.setattr(ollama_metadata.httpx, "post", lambda *a, **k: _response({}, 404))
    assert model_context.get_context_length_known("http://localhost:11434/v1", "qwen3:14b") == (131072, True)


@pytest.mark.parametrize("truncated_final,final_text", [
    (False, "Summary based on fetched content."),
    (True, "Summary based on fetched content."),
    (False, "Done."),
])
def test_auto_agent_fetches_page_and_threads_result_to_next_round(
    monkeypatch, endpoint_flag, truncated_final, final_text,
):
    from tests.runtime_evidence_helpers import authoritative_executor

    rounds = []
    executed = []
    url = "https://developer.adzuna.com/"
    monkeypatch.setattr(ollama_metadata.httpx, "post", lambda *a, **k: _response({
        "capabilities": ["completion", "tools"],
    }))
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *a, **k: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "_NATIVE_TOOL_REJECTIONS", {})

    async def stream(candidates, messages, **kwargs):
        rounds.append(list(messages))
        names = {schema["function"]["name"] for schema in kwargs.get("tools") or ()}
        if len(rounds) <= 2:
            assert "web_fetch" in names
        else:
            assert not names
        if len(rounds) == 1:
            call = {"id": "fetch-1", "name": "web_fetch", "arguments": json.dumps({"url": url})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            tool_result = next(message for message in messages if message.get("role") == "tool")
            assert tool_result["tool_call_id"] == "fetch-1"
            assert "Fixture page content" in tool_result["content"]
            text = "The" if truncated_final and len(rounds) == 2 else final_text
            yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"

    @authoritative_executor
    async def execute(block, *args, **kwargs):
        executed.append((block.tool_type, json.loads(block.content)["url"]))
        return "web_fetch", {"output": "Fixture page content", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", execute)

    async def collect():
        return [chunk async for chunk in agent_loop.stream_agent_loop(
            "http://localhost:11434/v1", "qwen3:14b-32k-test",
            [{"role": "user", "content": f"use web_fetch tool to read {url} and summarize it"}],
            context_length=32768, relevant_tools={"web_fetch"}, max_rounds=3,
            fallbacks=[], _is_teacher_run=True,
        )]

    chunks = asyncio.run(collect())
    assert executed == [("web_fetch", url)]
    assert len(rounds) == (3 if truncated_final else 2)
    assert any(final_text in chunk for chunk in chunks)
