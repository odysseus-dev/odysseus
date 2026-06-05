"""Test that backup import for skills uses the correct SkillsManager API."""
import asyncio
from unittest.mock import MagicMock
import routes.backup_routes as br

class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

def test_skills_import_calls_add_skill(monkeypatch):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")

    mem_manager = MagicMock()
    preset_manager = MagicMock()
    skills_manager = MagicMock()
    
    # Mock existing skills
    skills_manager.load_all.return_value = [
        {"id": "existing-id", "title": "Existing Skill", "owner": "bob"}
    ]
    
    router = br.setup_backup_routes(mem_manager, preset_manager, skills_manager)
    endpoint = None
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            endpoint = r.endpoint
    
    assert endpoint is not None
    
    body = {
        "skills": [
            {
                "title": "New Skill",
                "problem": "The Problem",
                "solution": "The Solution",
                "tags": ["test"],
                "owner": "alice"
            }
        ]
    }
    
    asyncio.run(endpoint(_Req(body)))
    
    # Verify add_skill was called instead of save
    assert not skills_manager.save.called
    skills_manager.add_skill.assert_called_once()
    args, kwargs = skills_manager.add_skill.call_args
    assert kwargs["title"] == "New Skill"
    assert kwargs["problem"] == "The Problem"
    assert kwargs["solution"] == "The Solution"
    assert kwargs["owner"] == "alice"

def test_skills_import_deduplication(monkeypatch):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")

    skills_manager = MagicMock()
    skills_manager.load_all.return_value = [
        {"id": "s1", "title": "Existing Title", "owner": "bob"}
    ]
    
    router = br.setup_backup_routes(MagicMock(), MagicMock(), skills_manager)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/import")
    
    # Try importing a skill with same title
    body = {
        "skills": [
            {"title": "Existing Title"}
        ]
    }
    
    asyncio.run(endpoint(_Req(body)))
    
    # Should be skipped because of global title collision (current behavior we are maintaining for now)
    assert not skills_manager.add_skill.called
