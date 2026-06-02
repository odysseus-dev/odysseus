import textwrap
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.datastructures import State

from routes.skills_routes import SkillUpdateRequest, setup_skills_routes
from services.memory.skill_format import slugify
from services.memory.skills import SkillsManager


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


def test_update_skill_manager_requires_owner(tmp_path):
    """Documents the contract that the PUT /api/skills/{id} route relies on:
    update_skill() must be called WITH the owner to mutate an owned skill,
    and without it (the bug shape) the call must fail rather than succeed.

    If this test ever flips, the route may silently mutate a foreign-owned
    skill — re-audit before changing.
    """
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    path = _write_skill_md(
        skills_root, category="general", name="caveman-mode",
        owner="alice", description="original",
    )
    sm = SkillsManager(str(tmp_path))

    # 1. Bug shape: no owner → the manager refuses (returns False).
    assert sm.update_skill("caveman-mode", {"status": "published"}) is False
    assert "status: draft" in path.read_text(encoding="utf-8")

    # 2. Fixed shape: owner passed → succeeds.
    assert sm.update_skill(
        "caveman-mode", {"status": "published"}, owner="alice"
    ) is True
    assert "status: published" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_put_skill_route_publishes_owned_skill(tmp_path):
    """End-to-end regression for the original issue:
    `PUT /api/skills/caveman-mode` with `{"status": "published"}` returned
    404 because the route called update_skill() without `owner=`, so the
    manager's owner filter skipped every owned skill on disk.
    The fix: the route now passes `owner=user`, so the publish round-trips.
    """
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    path = _write_skill_md(
        skills_root, category="general", name="caveman-mode",
        owner="alice", description="published-via-route",
    )
    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)

    update_route_handler = next(
        route.endpoint for route in router.routes
        if route.path == "/api/skills/{skill_id}" and "PUT" in route.methods
    )

    class DummyApp:
        state = State()
    request = Request(scope={
        "type": "http",
        "app": DummyApp(),
        "state": {"current_user": "alice"},
    })

    body = SkillUpdateRequest(status="published")
    res = await update_route_handler(request, "caveman-mode", body)
    assert res == {"ok": True}
    assert "status: published" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_put_skill_route_404s_for_other_owner(tmp_path):
    """Cross-tenant safety: a logged-in user must not be able to mutate
    another user's same-slug skill via PUT, even after the owner-passing
    fix. The route's own owner filter must still hide bob's file from alice.
    """
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    bob_path = _write_skill_md(
        skills_root, category="bobcat", name="caveman-mode",
        owner="bob", description="bob's secret",
    )
    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)

    update_route_handler = next(
        route.endpoint for route in router.routes
        if route.path == "/api/skills/{skill_id}" and "PUT" in route.methods
    )

    class DummyApp:
        state = State()
    request = Request(scope={
        "type": "http",
        "app": DummyApp(),
        "state": {"current_user": "alice"},
    })

    from fastapi import HTTPException
    body = SkillUpdateRequest(status="published")
    with pytest.raises(HTTPException) as exc:
        await update_route_handler(request, "caveman-mode", body)
    assert exc.value.status_code == 404
    # Bob's file must be untouched.
    assert "status: draft" in bob_path.read_text(encoding="utf-8")
    assert "bob's secret" in bob_path.read_text(encoding="utf-8")
