"""Renaming a skill onto a name the owner already uses must be refused.

``update_skill`` only checked whether the target directory existed. A
skill in another category with the wanted name lives in a different
directory, so the rename went through and the owner ended up with two
skills sharing one name. Update and delete look skills up by name, so the
next DELETE removed whichever of the two ``os.walk`` reached first, which
was often the one the user did not pick.
"""
import pytest

from routes.skills_routes import SkillUpdateRequest, setup_skills_routes
from services.memory.skills import SkillsManager

from tests.test_skills_routes_owner_update import _request, _route_handler, _write_skill_md


def _two_skills(tmp_path, owner="alice"):
    mgr = SkillsManager(str(tmp_path))
    mgr.add_skill(name="deploy-app", description="Deploy the web app", category="ops",
                  source="user", procedure=["ship it"], owner=owner)
    mgr.add_skill(name="draft-notes", description="Draft meeting notes", category="writing",
                  source="user", procedure=["write"], owner=owner)
    return mgr


def _names(mgr, owner="alice"):
    return sorted((s["name"], s["category"]) for s in mgr.load(owner=owner))


def test_rename_onto_a_name_in_another_category_is_refused(tmp_path):
    mgr = _two_skills(tmp_path)

    assert mgr.update_skill("draft-notes", {"name": "deploy-app"}, owner="alice") is False
    assert _names(mgr) == [("deploy-app", "ops"), ("draft-notes", "writing")]


def test_delete_after_refused_rename_removes_only_the_named_skill(tmp_path):
    mgr = _two_skills(tmp_path)
    mgr.update_skill("draft-notes", {"name": "deploy-app"}, owner="alice")

    assert mgr.delete_skill("draft-notes", owner="alice") is True
    assert _names(mgr) == [("deploy-app", "ops")]


def test_rename_to_a_free_name_still_moves_the_skill(tmp_path):
    mgr = _two_skills(tmp_path)

    assert mgr.update_skill("draft-notes", {"name": "meeting-notes"}, owner="alice") is True
    assert _names(mgr) == [("deploy-app", "ops"), ("meeting-notes", "writing")]


def test_another_owners_skill_does_not_block_the_rename(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill_md(skills_root, "bob-cat", "caveman-mode", "bob")
    _write_skill_md(skills_root, "alice-cat", "terse-mode", "alice")
    mgr = SkillsManager(str(tmp_path))

    assert mgr.update_skill("terse-mode", {"name": "caveman-mode"}, owner="alice") is True
    assert [s["name"] for s in mgr.load(owner="alice")] == ["caveman-mode"]
    assert [s["name"] for s in mgr.load(owner="bob")] == ["caveman-mode"]


def test_keeping_the_same_name_is_not_a_conflict(tmp_path):
    mgr = _two_skills(tmp_path)

    assert mgr.update_skill("draft-notes", {"name": "Draft Notes", "status": "published"},
                            owner="alice") is True
    assert [s["status"] for s in mgr.load(owner="alice") if s["name"] == "draft-notes"] == ["published"]


@pytest.mark.asyncio
async def test_put_route_answers_409_for_a_taken_name(tmp_path):
    from fastapi import HTTPException

    mgr = _two_skills(tmp_path)
    update_route = _route_handler(setup_skills_routes(mgr), "/api/skills/{skill_id}", "PUT")

    with pytest.raises(HTTPException) as exc:
        await update_route(_request("alice"), "draft-notes", SkillUpdateRequest(name="deploy-app"))

    assert exc.value.status_code == 409
    assert _names(mgr) == [("deploy-app", "ops"), ("draft-notes", "writing")]
