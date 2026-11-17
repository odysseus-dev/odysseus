import json

import pytest

from src.tools.system import do_manage_skills


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": ""},
        {"action": "   "},
        {"name": "demo", "description": "x", "procedure": ["step"]},
    ],
)
async def test_manage_skills_requires_action(payload):
    result = await do_manage_skills(json.dumps(payload), owner="test")

    assert result == {
        "error": "action is required (list|view|view_ref|add|edit|patch|publish|delete|search)",
        "exit_code": 1,
    }


@pytest.mark.asyncio
async def test_manage_skills_supports_metadata_only_edit(tmp_path, monkeypatch):
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    created = await do_manage_skills(json.dumps({
        "action": "add", "name": "demo-skill", "description": "before",
        "procedure": ["verify the change"], "verification": ["description is updated"],
    }), owner="test")
    assert "error" not in created

    edited = await do_manage_skills(json.dumps({
        "action": "edit", "name": "demo-skill", "description": "after",
    }), owner="test")
    assert "error" not in edited

    from services.memory.skills import SkillsManager
    saved = SkillsManager(str(tmp_path)).load(owner="test")
    assert next(skill for skill in saved if skill["name"] == "demo-skill")["description"] == "after"
