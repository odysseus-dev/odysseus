"""Regression test for routes/backup_routes.py import_data skills dedup.

BUG: the skills import block deduplicates against EVERY tenant's skills
(skills_manager.load_all()) instead of the importing user's own skills.
So importing your own backup silently drops any skill whose title (or id)
collides with ANOTHER user's skill — the same cross-tenant data-loss bug
that was already fixed for memories in the block just above.
"""
import sys
import types

import pytest


def _install_stubs():
    def _stub(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    _stub("core")
    _stub("core.middleware", require_admin=lambda *a, **k: None)
    _stub("src")
    _stub("src.auth_helpers", get_current_user=lambda req: getattr(req.state, "user", None))
    _stub("src.settings",
          load_settings=lambda: {}, save_settings=lambda s: None,
          load_features=lambda: {}, save_features=lambda f: None)


_install_stubs()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routes.backup_routes import setup_backup_routes  # noqa: E402


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


def _make_client(skills_mgr):
    app = FastAPI()

    @app.middleware("http")
    async def _set_user(request: Request, call_next):
        request.state.user = "alice"
        return await call_next(request)

    router = setup_backup_routes(FakeMemoryManager(), FakePresetManager(), skills_mgr)
    app.include_router(router)
    return TestClient(app)


def test_import_skill_not_dropped_by_other_users_title_collision():
    # Bob already owns a skill titled "Deploy". Alice (the importer) has none.
    skills_mgr = FakeSkillsManager([
        {"id": "bob-1", "title": "Deploy", "name": "Deploy", "owner": "bob"},
    ])
    client = _make_client(skills_mgr)

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
