import os
import pytest
import textwrap
from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.datastructures import State
from services.memory.skills import SkillsManager
from services.memory.skill_format import slugify
from routes.skills_routes import setup_skills_routes


def _write_skill_md(skills_root: Path, category: str, name: str,
                    owner: str, description: str) -> Path:
    """Drop a real SKILL.md on disk for the given owner."""
    skill_dir = skills_root / slugify(category or "general", fallback="general") / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        version: 1.0.0
        category: {category}
        tags: []
        status: draft
        confidence: 0.8
        source: learned
        owner: {owner}
        created: 2026-01-01T00:00:00Z
        ---

        # When to use
        test

        # Procedure
        - step 1
        """)
    path = skill_dir / "SKILL.md"
    path.write_text(md, encoding="utf-8")
    return path


def test_delete_skill_manager_direct_scoping(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    
    # Create an owner-scoped skill (owner="alice")
    path = _write_skill_md(
        skills_root,
        category="general",
        name="test-skill",
        owner="alice",
        description="test",
    )
    
    sm = SkillsManager(str(tmp_path))
    
    # 1. Assert that calling delete_skill without owner returns False (documents the bug/regression lock)
    assert sm.delete_skill("test-skill") is False
    assert path.exists() is True
    
    # 2. Call the manager exactly as the fixed route does (with owner), assert it returns True and the skill is gone
    assert sm.delete_skill("test-skill", owner="alice") is True
    assert path.exists() is False


@pytest.mark.asyncio
async def test_delete_skill_route_handler_scoping(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    
    # Create an owner-scoped skill (owner="alice")
    path = _write_skill_md(
        skills_root,
        category="general",
        name="test-skill",
        owner="alice",
        description="test",
    )
    
    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)
    
    # Find the delete route handler endpoint
    delete_route_handler = next(
        route.endpoint for route in router.routes
        if route.path == "/api/skills/{skill_id}" and "DELETE" in route.methods
    )
    
    # Construct a mock FastAPI Request
    class DummyApp:
        state = State()
    app = DummyApp()
    
    request = Request(scope={
        "type": "http",
        "app": app,
        "state": {
            "current_user": "alice"
        }
    })
    
    # Before the fix, this raises HTTPException 404 because delete_skill was called without owner.
    # After the fix, it deletes successfully and returns {"ok": True}.
    res = await delete_route_handler(request, "test-skill")
    assert res == {"ok": True}
    assert not path.exists()
