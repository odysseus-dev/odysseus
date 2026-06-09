"""Regression test for routes/backup_routes.py import_data skills dedup.

BUG: the skills import block deduplicates against EVERY tenant's skills
(skills_manager.load_all()) instead of the importing user's own skills.
So importing your own backup silently drops any skill whose title (or id)
collides with ANOTHER user's skill — the same cross-tenant data-loss bug
that was already fixed for memories in the block just above.
"""
import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import routes.backup_routes as backup_routes
from routes.backup_routes import setup_backup_routes

# require_admin / get_current_user are bound into routes.backup_routes at import
# time (`from x import name`). We patch them on that module directly per-test
# via monkeypatch — robust to import order and reverted at teardown. (Stubbing
# them through sys.modules only works if backup_routes has not been imported
# yet, which is not guaranteed in a full-suite run.)


class FakeMemoryManager:
    def __init__(self):
        self.rows = []

    def load(self, owner=None):
        return [r for r in self.rows if r.get("owner") == owner]

    def load_all(self):
        return list(self.rows)

    def save(self, rows):
        self.rows = list(rows)


class FakePresetManager:
    def get_all(self):
        return {}

    def save(self, d):
        pass


class FakeSkillsManager:
    """Mimics services.memory.skills: load_all() = all owners,
    load(owner) = that owner's skills only."""

    def __init__(self, rows):
        self.rows = list(rows)

    def load(self, owner=None):
        return [s for s in self.rows if s.get("owner") == owner]

    def load_all(self):
        return list(self.rows)

    def save(self, rows):
        self.rows = list(rows)

    def add_skill(self, title=None, name=None, owner=None, **kwargs):
        # Mirrors services.memory.skills.add_skill: persists a SKILL.md row and
        # returns its identity. source="user" skips auto-dedup, so no _deduped.
        entry = {"id": f"new-{len(self.rows)}", "title": title, "name": name, "owner": owner}
        self.rows.append(entry)
        return {"name": name, "id": entry["id"]}


def _make_client(skills_mgr, monkeypatch):
    # Bypass the admin gate and read the importer straight off request.state.
    monkeypatch.setattr(backup_routes, "require_admin", lambda *a, **k: None)
    monkeypatch.setattr(backup_routes, "get_current_user",
                        lambda req: getattr(req.state, "user", None))
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request: Request, call_next):
        request.state.user = "alice"
        return await call_next(request)

    router = setup_backup_routes(FakeMemoryManager(), FakePresetManager(), skills_mgr)
    app.include_router(router)
    return TestClient(app)


def test_import_skill_not_dropped_by_other_users_title_collision(monkeypatch):
    # Bob already owns a skill titled "Deploy". Alice (the importer) has none.
    skills_mgr = FakeSkillsManager([
        {"id": "bob-1", "title": "Deploy", "name": "Deploy", "owner": "bob"},
    ])
    client = _make_client(skills_mgr, monkeypatch)

    # Alice imports HER OWN backup containing a skill also titled "Deploy".
    payload = {
        "skills": [
            {"id": "alice-1", "title": "Deploy", "name": "Deploy"},
        ],
    }
    resp = client.post("/api/import", json=payload)
    assert resp.status_code == 200, resp.text

    # Alice's skill must have been imported and assigned to her.
    alice_skills = skills_mgr.load(owner="alice")
    titles = {s["title"] for s in alice_skills}
    assert "Deploy" in titles, (
        "Alice's own 'Deploy' skill was silently dropped because Bob owns a "
        "skill with the same title (cross-tenant dedup bug)."
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
