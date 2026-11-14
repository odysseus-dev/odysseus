"""A backup restore must bring a skill's free-form notes back.

/api/import recreates skills through ``add_skill``, which only filled
``body_extra`` from the legacy ``solution`` field and only when the skill had
no procedure. Any skill with both steps and notes was restored without its
notes, although the export carries them in ``body_extra``.
"""
import asyncio
from unittest.mock import MagicMock

import routes.backup_routes as br
from services.memory.skills import SkillsManager


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _endpoint(monkeypatch, skills_manager):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")
    router = br.setup_backup_routes(MagicMock(), MagicMock(), skills_manager)
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("import route not found")


def test_restore_keeps_notes_of_a_skill_with_a_procedure(monkeypatch, tmp_path):
    source = SkillsManager(str(tmp_path / "source"))
    source.add_skill(name="release", description="Cut a release", source="user",
                     procedure=["bump version", "tag"], pitfalls=["forgetting changelog"],
                     owner="alice")
    source.update_skill("release", {"body_extra": "## Notes\n\nRun the smoke suite on staging first."},
                        owner="alice")
    exported = source.load(owner="alice")

    target = SkillsManager(str(tmp_path / "target"))
    result = asyncio.run(_endpoint(monkeypatch, target)(_Req({"skills": exported})))

    assert "1 skills" in result["imported"]
    restored = target.load(owner="alice")[0]
    assert restored["procedure"] == ["bump version", "tag"]
    assert restored["pitfalls"] == ["forgetting changelog"]
    assert restored["body_extra"] == "## Notes\n\nRun the smoke suite on staging first."


def test_older_backup_without_body_extra_still_uses_solution(monkeypatch, tmp_path):
    target = SkillsManager(str(tmp_path))
    legacy = {"title": "Water plants", "problem": "Plants look dry", "solution": "Water them twice a week."}

    asyncio.run(_endpoint(monkeypatch, target)(_Req({"skills": [legacy]})))

    restored = target.load(owner="alice")[0]
    assert restored["when_to_use"] == "Plants look dry"
    assert restored["body_extra"] == "Water them twice a week."


def test_non_string_body_extra_falls_back_to_solution(monkeypatch, tmp_path):
    target = SkillsManager(str(tmp_path))
    skill = {"name": "odd", "description": "Odd skill", "body_extra": ["not", "text"],
             "solution": "Plain notes."}

    asyncio.run(_endpoint(monkeypatch, target)(_Req({"skills": [skill]})))

    assert target.load(owner="alice")[0]["body_extra"] == "Plain notes."
