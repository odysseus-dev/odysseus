"""Seam (f): agent and editor requests that bypass llm_core get the settings.

Spec: openspec/changes/add-model-customization/specs/model-customization/spec.md
(requirement "Sampling settings reach every consumer"). The agent's provider
request is captured at `preview_model_response`'s `client.stream` call and the
editor agent's at `_post_chat`'s httpx post; nothing reaches the network.
"""

import asyncio
import json

import pytest

from core import database
from src import clean_agent_preview
from src import editor_agent_model
from src import model_customization as mc
from src.generation_sampling import validate_temperature
from tests.helpers.sqlite_db import make_temp_sqlite

DESK = "http://192.168.1.20:8080/v1"          # local-openai
DESK_CHAT = DESK + "/chat/completions"
MODEL = "qwen3-coder"
SAMPLING = {"top_k": 40, "min_p": 0.05, "repeat_penalty": 1.1, "top_p": 0.9}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "STORE_PATH", str(tmp_path / "model_customizations.json"))
    mc._reset_cache()
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    mc._reset_endpoint_cache()
    with factory() as s:
        s.add(database.ModelEndpoint(
            id="desk", name="desk", base_url=DESK, cached_models=json.dumps([MODEL]),
        ))
        s.commit()
    yield
    engine.dispose()
    tmpfile.close()


class _Response:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _StreamContext:
    async def __aenter__(self):
        return _Response()

    async def __aexit__(self, *exc):
        return False


class _AgentClient:
    def __init__(self, seen):
        self.seen = seen

    def stream(self, method, url, headers=None, json=None):
        self.seen.append(json)
        return _StreamContext()


def _agent_request_body(temperature=0.7):
    seen = []
    request = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 64,
        "temperature": temperature,
        "stream": True,
    }

    async def run():
        async with clean_agent_preview.preview_model_response(
            _AgentClient(seen), DESK_CHAT, {}, request, {},
        ):
            pass

    asyncio.run(run())
    return seen[0]


def _editor_request_body(monkeypatch):
    seen = []

    class _Client:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            seen.append(json)
            return _Response()

    monkeypatch.setattr(editor_agent_model.httpx, "AsyncClient", _Client)
    asyncio.run(editor_agent_model._post_chat(DESK, MODEL, [{"role": "user", "content": "hi"}], {}, 5))
    return seen[0]


def test_agent_turns_carry_the_sampling_settings():
    mc.save_entry("desk", DESK, MODEL, SAMPLING)
    body = _agent_request_body()
    assert body["top_k"] == 40
    assert body["min_p"] == 0.05
    assert body["top_p"] == 0.9
    assert body["repeat_penalty"] == 1.1
    assert body["repetition_penalty"] == 1.1
    assert body["temperature"] == 0.7


def test_uncustomized_agent_turns_are_unchanged():
    body = _agent_request_body()
    assert set(body) == {"model", "messages", "max_tokens", "temperature", "stream"}


def test_agent_turns_receive_a_numeric_temperature():
    """The agent path's temperature is computed inside the chat route closure
    (routes/chat_routes.py) as ``resolve_temperature(sess.endpoint_url,
    sess.model, None)`` when no override is set; that closure cannot be driven
    without a full chat turn, so this asserts the value it receives is the
    per-model number and survives the agent's own temperature validator."""
    mc.save_entry("desk", DESK, MODEL, {"temperature": 0.6})
    value = mc.resolve_temperature(DESK_CHAT, MODEL, None)
    assert validate_temperature(value) == 0.6
    assert _agent_request_body(temperature=value)["temperature"] == 0.6


def test_editor_agent_requests_carry_the_sampling_settings_and_keep_their_own_temperature(monkeypatch):
    mc.save_entry("desk", DESK, MODEL, {**SAMPLING, "temperature": 0.9})
    body = _editor_request_body(monkeypatch)
    assert body["top_k"] == 40
    assert body["min_p"] == 0.05
    assert body["repetition_penalty"] == 1.1
    assert body["temperature"] == 0.2


def test_uncustomized_editor_agent_requests_are_unchanged(monkeypatch):
    body = _editor_request_body(monkeypatch)
    assert set(body) == {"model", "messages", "temperature", "max_tokens", "stream"}
