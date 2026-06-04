import os
import pytest
import textwrap
from pathlib import Path
try:
    from fastapi import Request, HTTPException
except ImportError:
    from unittest.mock import MagicMock
    Request = MagicMock
    HTTPException = Exception

from services.memory.skills import SkillsManager
from services.memory.skill_format import slugify
from routes.skills_routes import setup_skills_routes
from unittest.mock import MagicMock
import sys

def _write_skill_md(skills_root: Path, category: str, name: str,
                    owner: str, description: str) -> Path:
    """Drop a real SKILL.md on disk for the given owner."""
    skill_dir = skills_root / slugify(category or "general", fallback="general") / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # If owner is None, omit the owner field from frontmatter
    owner_line = f"owner: {owner}" if owner else ""
    
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
        {owner_line}
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

@pytest.mark.asyncio
async def test_admin_can_delete_ownerless_skill(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    
    # Create an OWNERLESS skill (owner=None)
    path = _write_skill_md(
        skills_root,
        category="general",
        name="ownerless-skill",
        owner=None,
        description="no owner",
    )
    
    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)
    
    # Mock auth_mgr.is_admin to return True for "admin-user"
    mock_auth_mgr = MagicMock()
    mock_auth_mgr.is_admin.side_effect = lambda u: u == "admin-user"
    
    # We need to ensure core.middleware.auth_mgr is our mock
    import core.middleware
    monkeypatch.setattr(core.middleware, "auth_mgr", mock_auth_mgr)
    
    # Find the delete route handler endpoint
    delete_route_handler = next(
        route.endpoint for route in router.routes
        if route.path == "/{skill_id}" and "DELETE" in route.methods
    )
    
    # 1. Non-admin user tries to delete ownerless skill -> 404
    request_bob = Request(scope={
        "type": "http",
        "state": {"current_user": "bob"}
    })
    with pytest.raises(HTTPException) as exc:
        await delete_route_handler(request_bob, "ownerless-skill")
    assert exc.value.status_code == 404
    assert path.exists()
    
    # 2. Admin user tries to delete ownerless skill -> Success
    request_admin = Request(scope={
        "type": "http",
        "state": {"current_user": "admin-user"}
    })
    res = await delete_route_handler(request_admin, "ownerless-skill")
    assert res == {"ok": True}
    assert not path.exists()

@pytest.mark.asyncio
async def test_admin_list_includes_ownerless_skills(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    
    _write_skill_md(skills_root, "general", "alice-skill", "alice", "alice owned")
    _write_skill_md(skills_root, "general", "ownerless-skill", None, "no owner")
    
    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)
    
    mock_auth_mgr = MagicMock()
    mock_auth_mgr.is_admin.side_effect = lambda u: u == "admin-user"
    import core.middleware
    monkeypatch.setattr(core.middleware, "auth_mgr", mock_auth_mgr)
    
    list_handler = next(
        route.endpoint for route in router.routes
        if route.path == "" and "GET" in route.methods
    )
    
    # 1. Alice sees only her skill
    request_alice = Request(scope={"type": "http", "state": {"current_user": "alice"}})
    res_alice = await list_handler(request_alice)
    names_alice = [s["name"] for s in res_alice["skills"]]
    assert "alice-skill" in names_alice
    assert "ownerless-skill" not in names_alice
    
    # 2. Admin sees both (her own + ownerless)
    # Wait, admin doesn't have an owner field in these skills, but they should see ownerless ones.
    request_admin = Request(scope={"type": "http", "state": {"current_user": "admin-user"}})
    res_admin = await list_handler(request_admin)
    names_admin = [s["name"] for s in res_admin["skills"]]
    assert "ownerless-skill" in names_admin
