"""Backup import must not call the removed skills_manager.save().

Skills migrated from data/skills.json to on-disk SKILL.md files; save() was
removed from SkillsManager. Import still always sees a ``skills`` key in
exported backups (often ``[]``), so calling save() raised AttributeError,
returned a 500 HTML page, and the UI reported a misleading JSON.parse error
from res.json().
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import routes.backup_routes as br


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _setup(monkeypatch, skills_manager):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")

    mem = MagicMock()
    mem.load_all.return_value = []
    mem.save.return_value = None

    presets = MagicMock()
    presets.get_all.return_value = {}
    presets.save.return_value = True

    router = br.setup_backup_routes(mem, presets, skills_manager)
    endpoint = None
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            endpoint = r.endpoint
    assert endpoint is not None
    return endpoint


def test_import_with_empty_skills_list_does_not_call_save(monkeypatch):
    skills = MagicMock(spec=["load_all", "add_skill"])
    skills.load_all.return_value = []
    endpoint = _setup(monkeypatch, skills)

    body = {"settings": {"foo": "bar"}, "skills": []}
    with monkeypatch.context() as m:
        m.setattr(br, "load_settings", lambda: {})
        m.setattr(br, "save_settings", lambda s: None)
        result = asyncio.run(endpoint(_Req(body)))

    assert result["ok"] is True
    skills.add_skill.assert_not_called()
    assert not hasattr(skills, "save") or not getattr(skills, "save", MagicMock()).called


def test_import_adds_new_skill_via_add_skill(monkeypatch):
    skills = MagicMock(spec=["load_all", "add_skill"])
    skills.load_all.return_value = []
    skills.add_skill.return_value = {
        "id": "buy-milk",
        "name": "buy-milk",
        "title": "Buy milk",
    }
    endpoint = _setup(monkeypatch, skills)

    body = {
        "skills": [{"name": "buy-milk", "title": "Buy milk", "description": "Buy milk"}],
        "preferences": {"theme": "dark"},
    }
    with monkeypatch.context() as m:
        m.setattr(br, "load_settings", lambda: {})
        m.setattr(br, "save_settings", lambda s: None)
        m.setattr(br, "load_features", lambda: {})
        m.setattr(br, "save_features", lambda f: None)
        m.setattr(
            "routes.prefs_routes._load_for_user",
            lambda user: {},
        )
        m.setattr(
            "routes.prefs_routes._save_for_user",
            lambda user, prefs: None,
        )
        result = asyncio.run(endpoint(_Req(body)))

    assert result["ok"] is True
    skills.add_skill.assert_called_once()
    assert skills.add_skill.call_args.kwargs.get("source") == "user"
