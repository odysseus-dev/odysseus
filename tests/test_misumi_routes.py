from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.api_token_routes import _normalize_scopes
from routes import misumi_routes
from routes.misumi_routes import setup_misumi_routes
from services.memory.skills import SkillsManager
from src import endpoint_resolver


def _household(tmp_path: Path) -> Path:
    root = tmp_path / "household-repo"
    (root / "household" / "food").mkdir(parents=True)
    (root / "household" / "food" / "shopping-list.md").write_text(
        "# Shopping list\n- [ ] miso\n", encoding="utf-8"
    )
    (root / "agent-tasks" / "inbox").mkdir(parents=True)
    (root / "agent-tasks" / "inbox" / "deploy-odysseus-host.md").write_text(
        "---\ntitle: Deploy Odysseus host\npriority: high\nstatus: open\n---\nRuntime health.\n",
        encoding="utf-8",
    )
    return root


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("MISUMI_HOUSEHOLD_ROOT", str(_household(tmp_path)))
    app = FastAPI()
    app.include_router(setup_misumi_routes(SkillsManager(str(tmp_path / "data"))))
    return TestClient(app)


def test_health_and_grounded_respond(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/misumi/health").status_code == 200
    response = client.post("/misumi/respond", json={"prompt": "what is on the shopping list?", "persona": "sanji"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "odysseus"
    assert body["persona"] == "sanji"
    assert body["sources"]
    assert "shopping-list.md" in body["sources"][0]["path"]


def test_respond_accepts_interface_string_context(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/misumi/respond",
        json={
            "intent": "reply",
            "state": "idle",
            "mood": "steady",
            "context": "what is on the shopping list?",
            "persona": "sanji",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["persona"] == "sanji"
    assert body["sources"]
    assert "shopping-list.md" in body["sources"][0]["path"]


def test_general_chat_ignores_weak_single_term_repository_hits(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/misumi/respond",
        json={
            "intent": "reply",
            "context": "Explain runtime in plain language",
            "persona": "aoteru",
        },
    )

    assert response.status_code == 200
    assert response.json()["sources"] == []


def test_domain_request_reports_absent_data_without_model_fallback(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.post(
        "/misumi/respond", json={"prompt": "What plant watering data exists?", "persona": "ginko"}
    ).json()

    assert body["sources"] == []
    assert "no plants data surface" in body["text"].lower()


def test_task_returns_structured_plan(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/misumi/task", json={
        "prompt": "autonomously complete agentic routed tasks",
        "persona": "aoteru",
        "mode": "task",
        "approval": "none",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "planned"
    assert body["task_candidates"]
    assert body["files_changed"] == []
    assert body["policy"]["writes_allowed"] is False


def test_persona_skill_endpoint_filters(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/misumi/personas/kurisu/skills").json()
    assert body["persona"] == "kurisu"
    assert body["count"] == 3
    assert all(skill["category"] in {"evidence", "archive", "citation", "document-analysis"} for skill in body["skills"])


def test_status_is_explicitly_read_only(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.get("/misumi/status").json()
    assert body["writes_allowed"] is False
    assert body["household"]["mode"] == "read_only"
    assert "model_resolution" not in body


def test_admin_status_reports_env_model_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MISUMI_MODEL_URL", "http://user:secret@127.0.0.1:11434/api?token=hidden")
    monkeypatch.setenv("MISUMI_MODEL", "qwen3:8b")
    monkeypatch.setattr(misumi_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda *args, **kwargs: (
            kwargs["fallback_url"], kwargs["fallback_model"], {"Authorization": "secret"}
        ),
    )
    monkeypatch.setattr(misumi_routes, "_last_model_error", "Misumi model reply failed: reasoning-only")

    body = _client(tmp_path, monkeypatch).get("/misumi/status").json()

    assert body["model_resolution"] == {
        "url": "http://127.0.0.1:11434/api",
        "model": "qwen3:8b",
        "source": "env-fallback",
        "env_fallback_present": True,
        "last_model_error": "Misumi model reply failed: reasoning-only",
    }
    assert "secret" not in str(body["model_resolution"])


def test_misumi_interface_token_profile_has_narrow_required_scopes():
    assert _normalize_scopes(profile="misumi_interface") == ["misumi:read", "misumi:execute"]
    assert _normalize_scopes("misumi:execute") == ["misumi:read", "misumi:execute"]
