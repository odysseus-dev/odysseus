"""Saving a skill's markdown must NOT rename it (issue #1333: can't delete skills).

`save_skill_markdown` (POST /api/skills/{id}/markdown) parsed the new markdown
and set `sk.name = slugify(sk.name or match["name"])` — so editing the frontmatter
`name:` silently renamed the skill, which moves its directory on disk
(`update_skill`) and orphans the original id. A later DELETE by the id the UI
still holds then 404s ("can't delete them now"). The audit save path
(`_apply_skill_md`) already pins the name with the comment that a save must
NEVER rename; this locks that same guarantee for the markdown-save endpoint.

Pure unit test: calls the route handlers directly with a mock Request (no
server, network, or browser), mirroring tests/test_skills_delete_owner.py.
"""

import json
import textwrap
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.datastructures import State

from services.memory.skills import SkillsManager
from services.memory.skill_format import slugify
from routes.skills_routes import setup_skills_routes


def _write_skill_md(skills_root: Path, category: str, name: str, owner: str) -> Path:
    skill_dir = skills_root / slugify(category or "general", fallback="general") / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = textwrap.dedent(f"""\
        ---
        name: {name}
        description: original description
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


def _md_named(name: str) -> str:
    return textwrap.dedent(f"""\
        ---
        name: {name}
        description: edited description
        version: 1.0.0
        category: general
        tags: []
        status: draft
        confidence: 0.8
        source: learned
        owner: alice
        created: 2026-01-01T00:00:00Z
        ---

        # When to use
        edited

        # Procedure
        - step 1
        """)


def _request(user: str, body: dict | None = None) -> Request:
    scope = {"type": "http", "app": type("App", (), {"state": State()})(),
             "state": {"current_user": user}, "headers": []}
    if body is None:
        return Request(scope=scope)

    async def _receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    return Request(scope=scope, receive=_receive)


def _handler(router, path: str, method: str):
    return next(r.endpoint for r in router.routes
               if r.path == path and method in r.methods)


@pytest.mark.asyncio
async def test_markdown_save_does_not_rename_then_delete_works(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill_md(skills_root, "general", "test-skill", "alice")

    sm = SkillsManager(str(tmp_path))
    router = setup_skills_routes(sm)
    save = _handler(router, "/api/skills/{skill_id}/markdown", "POST")
    delete = _handler(router, "/api/skills/{skill_id}", "DELETE")

    # Save markdown whose frontmatter renames the skill. The save must keep the
    # original name (no rename), so the returned name is unchanged.
    res = await save(_request("alice", {"markdown": _md_named("renamed-skill")}), "test-skill")
    assert res["name"] == "test-skill", f"save renamed the skill to {res.get('name')!r}"

    # The skill still lives under its original id (the edit DID apply).
    names = {s.get("name") for s in sm.load(owner="alice")}
    assert names == {"test-skill"}, names
    descriptions = {s.get("description") for s in sm.load(owner="alice")}
    assert "edited description" in descriptions  # the content edit took effect

    # The crux of #1333: deleting by the original id now succeeds.
    assert await delete(_request("alice"), "test-skill") == {"ok": True}
    assert sm.load(owner="alice") == []
