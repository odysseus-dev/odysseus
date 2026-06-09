import json
import textwrap
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.datastructures import State

from routes.skills_routes import SkillUpdateRequest, setup_skills_routes
from services.memory.skill_format import slugify
from services.memory.skills import SkillsManager


def _write_skill_md(skills_root: Path, category: str, name: str,
                    owner: str, description: str = "test") -> Path:
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


def _request(user: str, body=None) -> Request:
    class DummyApp:
        state = State()

    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope={
        "type": "http",
        "method": "POST" if body is not None else "PUT",
        "headers": [(b"content-type", b"application/json")] if body is not None else [],
        "app": DummyApp(),
        "state": {"current_user": user},
    }, receive=receive)


def _route_handler(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes
        if route.path == path and method in route.methods
    )


@pytest.mark.asyncio
async def test_update_skill_route_passes_owner_to_manager(tmp_path):
    skills_root = tmp_path / "skills"
    alice_path = _write_skill_md(skills_root, "alice-cat", "caveman-mode", "alice", "alice original")
    bob_path = _write_skill_md(skills_root, "bob-cat", "caveman-mode", "bob", "bob original")

    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)
    update_route = _route_handler(router, "/api/skills/{skill_id}", "PUT")

    result = await update_route(
        _request("alice"),
        "caveman-mode",
        SkillUpdateRequest(status="published", description="alice updated"),
    )

    assert result == {"ok": True}
    alice_after = alice_path.read_text(encoding="utf-8")
    bob_after = bob_path.read_text(encoding="utf-8")
    assert "status: published" in alice_after
    assert "alice updated" in alice_after
    assert "status: draft" in bob_after
    assert "bob original" in bob_after


@pytest.mark.asyncio
async def test_save_skill_markdown_route_passes_owner_to_manager(tmp_path):
    skills_root = tmp_path / "skills"
    skill_path = _write_skill_md(skills_root, "general", "caveman-mode", "alice", "before")

    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)
    save_route = _route_handler(router, "/api/skills/{skill_id}/markdown", "POST")
    markdown = textwrap.dedent("""\
        ---
        name: caveman-mode
        description: after
        version: 1.0.0
        category: general
        tags: []
        status: published
        confidence: 0.9
        source: user
        owner: alice
        created: 2026-01-01T00:00:00Z
        ---

        # When to use
        after

        # Procedure
        - updated step
        """)

    result = await save_route(
        _request("alice", {"markdown": markdown}),
        "caveman-mode",
    )

    assert result == {"ok": True, "name": "caveman-mode"}
    saved = skill_path.read_text(encoding="utf-8")
    assert "description: after" in saved
    assert "status: published" in saved
    assert "- updated step" in saved
