from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.misumi_routes import setup_misumi_routes
from services.memory.skills import SkillsManager


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
