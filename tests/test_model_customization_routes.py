"""Admin routes for Model Customization: GET/PUT /api/model-customizations.

One test per Gherkin scenario in
openspec/changes/add-model-customization/specs/model-customization/spec.md
that the read/write operations own.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import database
from routes.model_customization import setup_model_customization_routes
from src import model_customization as mc
from tests.helpers.sqlite_db import make_temp_sqlite

LAB_URL = "http://127.0.0.1:11434"
GROQ_URL = "https://api.groq.com/openai/v1"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "model_customizations.json"
    monkeypatch.setattr(mc, "STORE_PATH", str(path))
    mc._reset_cache()
    return path


@pytest.fixture
def endpoints(monkeypatch, store):
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(mc, "_session_factory", lambda: factory)
    mc._reset_endpoint_cache()

    def add(endpoint_id, base_url, models, **extra):
        with factory() as db:
            db.add(database.ModelEndpoint(
                id=endpoint_id, name=endpoint_id, base_url=base_url,
                cached_models=json.dumps(models), **extra,
            ))
            db.commit()
        mc._reset_endpoint_cache()

    def delete(endpoint_id):
        with factory() as db:
            db.query(database.ModelEndpoint).filter_by(id=endpoint_id).delete()
            db.commit()
        mc._reset_endpoint_cache()

    add.delete = delete
    yield add
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def client_for(monkeypatch):
    """A TestClient signed in as ``user``; the real require_admin decides access."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: user == "ada")
    signed_in = {}

    @app.middleware("http")
    async def fixture_session(request, call_next):
        request.state.current_user = signed_in.get("user")
        return await call_next(request)

    app.include_router(setup_model_customization_routes())

    def make(user):
        signed_in["user"] = user
        return TestClient(app)

    return make


def _listed(body):
    return {ep["id"]: {m["model"]: m for m in ep["models"]} for ep in body["endpoints"]}


def _save(client, endpoint_id, model, settings):
    return client.put("/api/model-customizations",
                      json={"endpoint_id": endpoint_id, "model": model, "settings": settings})


# ── Admin gate ──────────────────────────────────────────────────────────────

def test_a_non_admin_cannot_read_customizations(client_for, endpoints, store):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    response = client_for("sam").get("/api/model-customizations")
    assert response.status_code == 403
    assert "0.6" not in response.text


def test_an_anonymous_request_cannot_read_customizations(client_for, endpoints):
    assert client_for(None).get("/api/model-customizations").status_code in (401, 403)


def test_a_non_admin_cannot_save_a_customization(client_for, endpoints, store):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    response = _save(client_for("sam"), "lab-ollama", "qwen3:8b", {"temperature": 0.6})
    assert response.status_code == 403
    assert not store.exists()


def test_an_admin_can_read_and_save_customizations(client_for, endpoints):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    client = client_for("ada")
    saved = _save(client, "lab-ollama", "qwen3:8b", {"temperature": 0.6})
    assert saved.status_code == 200
    assert saved.json()["entry"]["settings"] == {"temperature": 0.6}
    listed = _listed(client.get("/api/model-customizations").json())
    assert listed["lab-ollama"]["qwen3:8b"]["settings"] == {"temperature": 0.6}


def test_saving_blank_settings_removes_the_entry(client_for, endpoints, store):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    client = client_for("ada")
    _save(client, "lab-ollama", "qwen3:8b", {"temperature": 0.6})
    response = _save(client, "lab-ollama", "qwen3:8b", {"temperature": None, "top_p": ""})
    assert response.status_code == 200
    assert response.json()["entry"] is None
    assert json.loads(store.read_text())["entries"] == []


# ── Rejected saves ──────────────────────────────────────────────────────────

def test_an_unknown_endpoint_is_rejected(client_for, endpoints, store):
    response = _save(client_for("ada"), "nowhere", "qwen3:8b", {"temperature": 0.6})
    assert response.status_code == 400
    assert "endpoint" in response.json()["detail"].lower()
    assert not store.exists()


def test_an_excluded_endpoint_is_rejected(client_for, endpoints, store):
    endpoints("anthropic-direct", "https://api.anthropic.com", ["claude-sonnet-4"])
    response = _save(client_for("ada"), "anthropic-direct", "claude-sonnet-4", {"temperature": 0.6})
    assert response.status_code == 400
    assert not store.exists()


def test_an_out_of_range_value_is_rejected_naming_the_field(client_for, endpoints, store):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    response = _save(client_for("ada"), "lab-ollama", "qwen3:8b", {"temperature": 2.5})
    assert response.status_code == 400
    assert "temperature" in response.json()["detail"]
    assert not store.exists()


def test_unknown_setting_names_are_rejected(client_for, endpoints, store):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    response = _save(client_for("ada"), "lab-ollama", "qwen3:8b", {"mirostat": 2})
    assert response.status_code == 400
    assert "mirostat" in response.json()["detail"]


def test_a_setting_the_endpoint_cannot_accept_is_rejected(client_for, endpoints, store):
    endpoints("groq-cloud", GROQ_URL, ["llama-3.3-70b"])
    response = _save(client_for("ada"), "groq-cloud", "llama-3.3-70b", {"top_k": 40})
    assert response.status_code == 400
    assert "top_k" in response.json()["detail"]


def test_saving_an_ambiguous_setting_warns_the_admin(client_for, endpoints):
    endpoints("team-a", "http://127.0.0.2:8080/v1", ["qwen3:8b"], api_key="key-a")
    endpoints("team-b", "http://127.0.0.2:8080/v1", ["qwen3:8b"], api_key="key-b")
    client = client_for("ada")
    first = _save(client, "team-a", "qwen3:8b", {"temperature": 0.6})
    assert first.json().get("warning") is None
    second = _save(client, "team-b", "qwen3:8b", {"temperature": 0.9})
    assert second.status_code == 200
    assert "ambiguous" in second.json()["warning"].lower()


# ── Cascade contents ────────────────────────────────────────────────────────

def test_anthropic_and_chatgpt_subscription_endpoints_are_not_listed(client_for, endpoints):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    endpoints("anthropic-direct", "https://api.anthropic.com", ["claude-sonnet-4"])
    endpoints("chatgpt-plan", "https://chatgpt.com/backend-api/codex", ["gpt-5"])
    listed = _listed(client_for("ada").get("/api/model-customizations").json())
    assert set(listed) == {"lab-ollama"}


def test_image_endpoints_are_not_listed(client_for, endpoints):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"], model_type=None)
    endpoints("lab-comfy", "http://127.0.0.3:8188", ["flux-dev"], model_type="image")
    listed = _listed(client_for("ada").get("/api/model-customizations").json())
    assert set(listed) == {"lab-ollama"}
    assert set(listed["lab-ollama"]) == {"qwen3:8b"}


def test_hidden_models_are_not_listed(client_for, endpoints):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b", "broken-model"],
              hidden_models=json.dumps(["broken-model"]))
    listed = _listed(client_for("ada").get("/api/model-customizations").json())
    assert set(listed["lab-ollama"]) == {"qwen3:8b"}


def test_supported_settings_are_reported_per_provider_format(client_for, endpoints):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    endpoints("groq-cloud", GROQ_URL, ["llama-3.3-70b"])
    listed = _listed(client_for("ada").get("/api/model-customizations").json())
    lab = set(listed["lab-ollama"]["qwen3:8b"]["supported"])
    groq = set(listed["groq-cloud"]["llama-3.3-70b"]["supported"])
    assert {"num_ctx", "temperature", "top_p", "top_k", "min_p",
            "presence_penalty", "repeat_penalty"} <= lab
    assert {"temperature", "top_p", "presence_penalty"} <= groq
    assert not groq & {"num_ctx", "top_k", "min_p", "repeat_penalty"}


def test_stored_settings_are_reported_with_the_model(client_for, endpoints):
    endpoints("lab-ollama", LAB_URL, ["qwen3:8b"])
    mc.save_entry("lab-ollama", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    listed = _listed(client_for("ada").get("/api/model-customizations").json())
    assert listed["lab-ollama"]["qwen3:8b"]["settings"] == {"temperature": 0.6}


def test_orphaned_entries_are_hidden_but_kept(client_for, endpoints, store):
    endpoints("old-box", LAB_URL, ["qwen3:8b"])
    mc.save_entry("old-box", LAB_URL, "qwen3:8b", {"temperature": 0.6})
    endpoints.delete("old-box")
    listed = _listed(client_for("ada").get("/api/model-customizations").json())
    assert "old-box" not in listed
    assert [e["endpoint_id"] for e in json.loads(store.read_text())["entries"]] == ["old-box"]


def test_the_cascade_never_contains_api_keys(client_for, endpoints):
    endpoints("groq-cloud", GROQ_URL, ["llama-3.3-70b"], api_key="sk-secret-fixture-key")
    response = client_for("ada").get("/api/model-customizations")
    assert response.status_code == 200
    assert "sk-secret-fixture-key" not in response.text
