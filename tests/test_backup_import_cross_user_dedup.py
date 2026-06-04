"""Backup import must dedup memories against the importing user only.

import_data deduped incoming memories against memory_manager.load_all()
(every tenant\'s rows), so a memory whose text matched ANY other user\'s
memory was silently skipped - the importing user lost their own data. The
dedup must be scoped to the caller\'s own memories. The full multi-tenant
store is still saved back.
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


def _setup(monkeypatch, store, user="alice"):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: user)

    mem = MagicMock()
    mem.load_all.return_value = list(store)
    saved = {}
    mem.save.side_effect = lambda entries: saved.__setitem__("entries", entries)

    skills = MagicMock()
    skills.load_all.return_value = []
    router = br.setup_backup_routes(mem, MagicMock(), skills)
    endpoint = None
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            endpoint = r.endpoint
    assert endpoint is not None
    return endpoint, saved


def _import_endpoint(monkeypatch, skills_manager, user="alice"):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: user)

    mem = MagicMock()
    mem.load_all.return_value = []
    router = br.setup_backup_routes(mem, MagicMock(), skills_manager)
    endpoint = None
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            endpoint = r.endpoint
    assert endpoint is not None
    return endpoint


def test_user_can_import_memory_matching_another_users_text(monkeypatch):
    # bob already has "buy milk"; alice imports her own "Buy Milk".
    endpoint, saved = _setup(monkeypatch, [{"text": "buy milk", "owner": "bob"}])
    body = {"memories": [{"text": "Buy Milk"}]}
    asyncio.run(endpoint(_Req(body)))
    texts_by_owner = {(e.get("owner"), e.get("text")) for e in saved["entries"]}
    assert ("alice", "Buy Milk") in texts_by_owner  # not dropped as a "duplicate"
    assert ("bob", "buy milk") in texts_by_owner     # other tenant preserved


def test_users_own_duplicate_is_still_skipped(monkeypatch):
    endpoint, saved = _setup(monkeypatch, [{"text": "buy milk", "owner": "alice"}])
    body = {"memories": [{"text": "Buy Milk"}]}
    asyncio.run(endpoint(_Req(body)))
    alice_milk = [e for e in saved["entries"]
                  if e.get("owner") == "alice" and e.get("text", "").lower() == "buy milk"]
    assert len(alice_milk) == 1  # the real duplicate is still deduped


def test_skill_import_writes_disk_backed_skill(monkeypatch, tmp_path):
    sm = SkillsManager(str(tmp_path))
    endpoint = _import_endpoint(monkeypatch, sm)

    body = {
        "skills": [{
            "id": "debug-flow",
            "name": "debug-flow",
            "description": "Debug flow",
            "title": "Debug flow",
            "category": "ops",
            "tags": ["debug"],
            "status": "published",
            "confidence": 0.7,
            "source": "imported",
            "owner": "alice",
            "created": "2026-01-01T00:00:00Z",
            "when_to_use": "When debugging failures",
            "procedure": ["Check logs"],
            "pitfalls": ["Do not skip reproduction"],
            "verification": ["Failure is reproduced"],
            "body_extra": "Extra backup context",
        }]
    }

    result = asyncio.run(endpoint(_Req(body)))

    assert result["imported"] == ["1 skills"]
    skills = sm.load(owner="alice")
    assert len(skills) == 1
    skill = skills[0]
    assert skill["name"] == "debug-flow"
    assert skill["description"] == "Debug flow"
    assert skill["category"] == "ops"
    assert skill["procedure"] == ["Check logs"]
    assert skill["verification"] == ["Failure is reproduced"]
    assert skill["body_extra"] == "Extra backup context"
    assert skill["created"] == "2026-01-01T00:00:00Z"


def test_user_can_import_skill_matching_another_users_title(monkeypatch, tmp_path):
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="shared-flow",
        description="Shared Flow",
        when_to_use="Bob's workflow",
        procedure=["Bob step"],
        owner="bob",
        source="user",
    )
    endpoint = _import_endpoint(monkeypatch, sm, user="alice")

    body = {
        "skills": [{
            "name": "shared-flow",
            "description": "Shared Flow",
            "when_to_use": "Alice's workflow",
            "procedure": ["Alice step"],
            "owner": "alice",
            "source": "imported",
        }]
    }

    result = asyncio.run(endpoint(_Req(body)))

    assert result["imported"] == ["1 skills"]
    assert len(sm.load(owner="bob")) == 1
    alice_skills = sm.load(owner="alice")
    assert len(alice_skills) == 1
    assert alice_skills[0]["description"] == "Shared Flow"


def test_import_skips_current_users_existing_skill(monkeypatch, tmp_path):
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="shared-flow",
        description="Shared Flow",
        when_to_use="Alice's workflow",
        procedure=["Alice step"],
        owner="alice",
        source="user",
    )
    endpoint = _import_endpoint(monkeypatch, sm, user="alice")

    body = {
        "skills": [{
            "name": "shared-flow",
            "description": "Shared Flow",
            "when_to_use": "Alice's workflow",
            "procedure": ["Alice step"],
            "owner": "alice",
            "source": "imported",
        }]
    }

    result = asyncio.run(endpoint(_Req(body)))

    assert result["imported"] == ["0 skills"]
    assert len(sm.load(owner="alice")) == 1
