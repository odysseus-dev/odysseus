"""Seam (e): the per-model context window reaches context budgeting.

Spec: openspec/changes/add-model-customization/specs/model-customization/spec.md
(requirement "Context window applies to Ollama-native calls through context
budgeting"). Discovery (`_query_context_length`) is replaced by a stub so no
endpoint is contacted; the per-model override is checked in front of it.
"""

import json

import pytest

from core import database
from src import model_context
from src import model_customization as mc
from tests.helpers.sqlite_db import make_temp_sqlite

LAB = "http://127.0.0.1:11434"
LAB_CHAT = LAB + "/api/chat"
GROQ = "https://api.groq.com/openai/v1"
GROQ_CHAT = GROQ + "/chat/completions"
DISCOVERED = 40960


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "STORE_PATH", str(tmp_path / "model_customizations.json"))
    mc._reset_cache()
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    mc._reset_endpoint_cache()
    monkeypatch.setattr(model_context, "_query_context_length", lambda url, model: (DISCOVERED, True))
    monkeypatch.setattr(model_context, "_context_cache", {})
    yield factory
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def customize(isolated):
    def add(endpoint_id, base_url, model, settings):
        with isolated() as db:
            db.add(database.ModelEndpoint(
                id=endpoint_id, name=endpoint_id, base_url=base_url,
                cached_models=json.dumps([model]),
            ))
            db.commit()
        mc._reset_endpoint_cache()
        mc.save_entry(endpoint_id, base_url, model, settings)
    return add


def test_ollama_receives_the_configured_context_window(customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"num_ctx": 16384})
    assert model_context.get_context_length(LAB_CHAT, "qwen3:8b") == 16384


def test_budget_scaling_trusts_the_configured_context_window(customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"num_ctx": 16384})
    assert model_context.get_context_length_known(LAB_CHAT, "qwen3:8b") == (16384, True)
    assert model_context.budget_context_for_model(LAB_CHAT, "qwen3:8b") == 16384


def test_a_stored_window_budgets_a_hosted_endpoint_without_a_request_field(isolated, tmp_path):
    with isolated() as db:
        db.add(database.ModelEndpoint(
            id="groq", name="groq", base_url=GROQ, cached_models=json.dumps(["llama-3.3-70b"]),
        ))
        db.commit()
    mc.save_entry("groq", GROQ, "llama-3.3-70b", {"num_ctx": 8192})
    assert model_context.get_context_length(GROQ_CHAT, "llama-3.3-70b") == 8192
    assert model_context.get_context_length_known(GROQ_CHAT, "llama-3.3-70b") == (8192, True)


def test_context_window_discovery_is_unchanged_elsewhere(customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    assert model_context.get_context_length(LAB_CHAT, "qwen3:8b") == DISCOVERED


def test_uncustomized_models_keep_discovered_context(customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"temperature": 0.6})
    assert model_context.get_context_length(LAB_CHAT, "qwen3:8b") == DISCOVERED


def test_a_new_context_window_is_seen_without_clearing_the_discovery_cache(customize):
    customize("lab-ollama", LAB, "qwen3:8b", {"num_ctx": 16384})
    assert model_context.get_context_length(LAB_CHAT, "qwen3:8b") == 16384
    mc.save_entry("lab-ollama", LAB, "qwen3:8b", {"num_ctx": 32768})
    assert model_context.get_context_length(LAB_CHAT, "qwen3:8b") == 32768
