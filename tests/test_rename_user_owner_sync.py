"""Renaming a user must update all three owner caches, not just the SQL DB.

The DB owner-rename loop in the rename_user route updates every SQL-backed
owner column, but three file-backed / in-memory stores are left stale:

1. session_manager.sessions  — in-memory session objects carry s.owner set at
   load time; get_sessions_for_user does an exact `s.owner == username` check,
   so the renamed user's sidebar empties until a server restart.

2. data/deep_research/*.json  — each report JSON has an `owner` field;
   research_routes filters by `d.get("owner") == user`, making every report
   invisible after rename.

3. research_handler._active_tasks — in-flight research jobs carry the same
   owner key while status/cancel/active routes filter by it.

4. data/memory.json  — a flat array where every entry has an `owner` field;
   memory_manager.load(owner=user) filters on it, so all memories vanish.

Regression coverage: these bugs are invisible in unit tests that mock the DB
loop but don't exercise the file/cache patches added to the route.
"""
import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _route(router, name):
    for r in router.routes:
        if getattr(getattr(r, "endpoint", None), "__name__", "") == name:
            return r.endpoint
    raise AssertionError(name)


@pytest.fixture
def rename_endpoint(monkeypatch, tmp_path):
    import routes.auth_routes as ar
    import core.database as cdb

    # Neutralize the DB owner-rename loop.
    monkeypatch.setattr(cdb, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(cdb, "Base", SimpleNamespace(registry=SimpleNamespace(mappers=[])), raising=False)
    # Neutralize the JSON-prefs rename.
    pr = types.ModuleType("routes.prefs_routes")
    pr._load = lambda: {}
    pr._save = lambda d: None
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", pr)
    # Patch the module-level constants so file-update steps write to tmp_path.
    # (Patching sc.DATA_DIR wouldn't work — auth_routes binds DEEP_RESEARCH_DIR
    # and MEMORY_FILE at import time, so we must patch those names on the module.)
    monkeypatch.setattr(ar, "DEEP_RESEARCH_DIR", str(tmp_path / "deep_research"))
    monkeypatch.setattr(ar, "MEMORY_FILE", str(tmp_path / "memory.json"))
    monkeypatch.setattr(ar, "SKILLS_DIR", str(tmp_path / "skills"))

    am = MagicMock()
    am.is_admin.return_value = True
    am.get_username_for_token.return_value = "admin"
    am.users = {"alice": {}}
    am.rename_user.return_value = True
    return _route(ar.setup_auth_routes(am), "rename_user"), am, tmp_path


def _request(tmp_path, session_manager=None, token="t", research_handler=None):
    state = SimpleNamespace(
        invalidate_token_cache=lambda: None,
        session_manager=session_manager,
        research_handler=research_handler,
    )
    return SimpleNamespace(
        cookies={"odysseus_session": token},
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(current_user="admin"),
    )


def _auth_manager_for_rollback_test(monkeypatch, tmp_path):
    import core.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(auth_mod, "_verify_password", lambda password, hashed: hashed == f"hash:{password}")

    am = auth_mod.AuthManager(str(tmp_path / "auth.json"))
    assert am.create_user("admin", "pw-123456", is_admin=True) is True
    assert am.create_user("alice", "pw-123456") is True
    return am


def _force_sql_owner_migration_failure(monkeypatch):
    import core.database as cdb

    class OwnerModel:
        owner = "owner"

    class FailingQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def update(self, *_args, **_kwargs):
            raise RuntimeError("forced owner migration failure")

    class FailingSession:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def query(self, _model):
            return FailingQuery()

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    db = FailingSession()
    monkeypatch.setattr(cdb, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        cdb,
        "Base",
        SimpleNamespace(registry=SimpleNamespace(mappers=[SimpleNamespace(class_=OwnerModel)])),
        raising=False,
    )
    return db


# ---------------------------------------------------------------------------
# 1. In-memory session cache
# ---------------------------------------------------------------------------

def test_rename_updates_in_memory_session_owner(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    # Build a fake session_manager with one session owned by alice.
    sess = SimpleNamespace(owner="alice")
    sm = SimpleNamespace(sessions={"s1": sess})

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path, sm)))

    assert sess.owner == "alice2", "in-memory session owner was not updated on rename"


def test_rename_session_owner_case_insensitive(rename_endpoint):
    """Stored owner 'Alice' (mixed case) must match rename of 'alice'."""
    endpoint, _am, tmp_path = rename_endpoint

    sess = SimpleNamespace(owner="Alice")
    sm = SimpleNamespace(sessions={"s1": sess})

    asyncio.run(endpoint("alice", SimpleNamespace(username="bob"), _request(tmp_path, sm)))

    assert sess.owner == "bob"


def test_rename_leaves_other_sessions_untouched(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    sess_alice = SimpleNamespace(owner="alice")
    sess_other = SimpleNamespace(owner="carol")
    sm = SimpleNamespace(sessions={"s1": sess_alice, "s2": sess_other})

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path, sm)))

    assert sess_alice.owner == "alice2"
    assert sess_other.owner == "carol", "unrelated session owner was modified"


def test_rename_no_session_manager_does_not_crash(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint
    # app.state without a session_manager must not raise.
    req = SimpleNamespace(
        cookies={"odysseus_session": "t"},
        app=SimpleNamespace(state=SimpleNamespace(invalidate_token_cache=lambda: None)),
        state=SimpleNamespace(current_user="admin"),
    )
    res = asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), req))
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# 2. deep_research JSON files
# ---------------------------------------------------------------------------

def test_rename_updates_research_json_owner(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    dr_dir = tmp_path / "deep_research"
    dr_dir.mkdir()
    report = {"query": "test", "owner": "alice", "status": "done"}
    p = dr_dir / "abc123.json"
    p.write_text(json.dumps(report), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    updated = json.loads(p.read_text(encoding="utf-8"))
    assert updated["owner"] == "alice2", "deep_research JSON owner was not updated on rename"


def test_rename_research_json_case_insensitive(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    dr_dir = tmp_path / "deep_research"
    dr_dir.mkdir()
    p = (dr_dir / "r1.json")
    p.write_text(json.dumps({"owner": "Alice"}), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="bob"), _request(tmp_path)))

    assert json.loads(p.read_text())["owner"] == "bob"


def test_rename_leaves_other_research_untouched(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    dr_dir = tmp_path / "deep_research"
    dr_dir.mkdir()
    p_alice = dr_dir / "a.json"
    p_carol = dr_dir / "c.json"
    p_alice.write_text(json.dumps({"owner": "alice"}), encoding="utf-8")
    p_carol.write_text(json.dumps({"owner": "carol"}), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    assert json.loads(p_alice.read_text())["owner"] == "alice2"
    assert json.loads(p_carol.read_text())["owner"] == "carol"


def test_rename_no_deep_research_dir_does_not_crash(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint
    # No deep_research dir — must not crash.
    res = asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))
    assert res["ok"] is True


def test_rename_updates_active_research_task_owner(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    from routes.research_routes import setup_research_routes
    from src.research_handler import ResearchHandler

    rh = ResearchHandler.__new__(ResearchHandler)
    rh._active_tasks = {
        "alice-task": {
            "owner": "Alice",
            "status": "running",
            "query": "q",
            "progress": {},
            "started_at": 1,
        },
        "carol-task": {
            "owner": "carol",
            "status": "running",
            "query": "q2",
            "progress": {},
            "started_at": 2,
        },
    }

    asyncio.run(endpoint(
        "alice",
        SimpleNamespace(username="alice2"),
        _request(tmp_path, research_handler=rh),
    ))

    assert rh._active_tasks["alice-task"]["owner"] == "alice2"
    assert rh._active_tasks["carol-task"]["owner"] == "carol"

    router = setup_research_routes(rh)
    active = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/research/active"
    )

    alice2 = asyncio.run(active(
        SimpleNamespace(state=SimpleNamespace(current_user="alice2")),
    ))
    alice = asyncio.run(active(
        SimpleNamespace(state=SimpleNamespace(current_user="alice")),
    ))

    assert [item["session_id"] for item in alice2["active"]] == ["alice-task"]
    assert alice["active"] == []


def test_research_handler_rename_owner_canonicalizes_new_owner():
    from src.research_handler import ResearchHandler

    rh = ResearchHandler.__new__(ResearchHandler)
    rh._active_tasks = {
        "task": {"owner": "Alice", "status": "running"},
    }

    changed = rh.rename_owner("alice", "Alice2")
    assert changed == 1
    assert rh._active_tasks["task"]["owner"] == "alice2"


def test_research_handler_rename_owner_uses_auth_lower_contract_not_casefold():
    from src.research_handler import ResearchHandler

    rh = ResearchHandler.__new__(ResearchHandler)
    rh._active_tasks = {
        "task-strasse": {"owner": "strasse", "status": "running"},
        "task-sharp-s": {"owner": "straße", "status": "running"},
    }

    changed = rh.rename_owner("straße", "renamed")

    assert changed == 1
    assert rh._active_tasks["task-strasse"]["owner"] == "strasse"
    assert rh._active_tasks["task-sharp-s"]["owner"] == "renamed"


def test_rename_updates_active_research_before_completed_json_sweep(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    dr_dir = tmp_path / "deep_research"
    dr_dir.mkdir()
    report = dr_dir / "race-window.json"
    report.write_text(json.dumps({"owner": "alice", "status": "done"}), encoding="utf-8")
    owner_seen_by_active_hook = []

    class FakeResearchHandler:
        def rename_owner(self, _old, _new):
            owner_seen_by_active_hook.append(json.loads(report.read_text(encoding="utf-8"))["owner"])

    asyncio.run(endpoint(
        "alice",
        SimpleNamespace(username="alice2"),
        _request(tmp_path, research_handler=FakeResearchHandler()),
    ))

    assert owner_seen_by_active_hook == ["alice"]
    assert json.loads(report.read_text(encoding="utf-8"))["owner"] == "alice2"


def test_rename_research_respects_custom_data_dir(monkeypatch, tmp_path):
    """DEEP_RESEARCH_DIR (which honours ODYSSEUS_DATA_DIR) is used, not a
    hardcoded relative path. Before the fix, setting ODYSSEUS_DATA_DIR made
    the rename silently patch a different directory from where research files
    actually live, so reports still disappeared after rename."""
    import routes.auth_routes as ar
    import core.database as cdb

    custom_dr = tmp_path / "custom_data" / "deep_research"
    custom_dr.mkdir(parents=True)
    p = custom_dr / "rp-abc.json"
    p.write_text(json.dumps({"query": "q", "owner": "alice", "status": "done"}), encoding="utf-8")

    monkeypatch.setattr(cdb, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(cdb, "Base", SimpleNamespace(registry=SimpleNamespace(mappers=[])), raising=False)
    pr = types.ModuleType("routes.prefs_routes")
    pr._load = lambda: {}
    pr._save = lambda d: None
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", pr)
    monkeypatch.setattr(ar, "DEEP_RESEARCH_DIR", str(custom_dr))
    monkeypatch.setattr(ar, "MEMORY_FILE", str(tmp_path / "memory.json"))

    am = MagicMock()
    am.is_admin.return_value = True
    am.get_username_for_token.return_value = "admin"
    am.users = {"alice": {}}
    am.rename_user.return_value = True
    endpoint = _route(ar.setup_auth_routes(am), "rename_user")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    assert json.loads(p.read_text(encoding="utf-8"))["owner"] == "alice2", (
        "research JSON at custom DATA_DIR was not patched — DEEP_RESEARCH_DIR constant not used"
    )


# ---------------------------------------------------------------------------
# 3. memory.json
# ---------------------------------------------------------------------------

def test_rename_updates_memory_json_owner(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    entries = [
        {"id": "1", "text": "Lives in Berlin", "owner": "alice"},
        {"id": "2", "text": "Likes Python",    "owner": "carol"},
    ]
    (tmp_path / "memory.json").write_text(json.dumps(entries), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    updated = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    assert updated[0]["owner"] == "alice2", "memory.json entry owner was not updated on rename"
    assert updated[1]["owner"] == "carol",  "unrelated memory entry was modified"


def test_rename_memory_json_case_insensitive(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    entries = [{"id": "1", "text": "x", "owner": "Alice"}]
    (tmp_path / "memory.json").write_text(json.dumps(entries), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="bob"), _request(tmp_path)))

    assert json.loads((tmp_path / "memory.json").read_text())[0]["owner"] == "bob"


def test_rename_no_memory_json_does_not_crash(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint
    # No memory.json — must not crash.
    res = asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# 4. Skills (SKILL.md frontmatter + _usage.json sidecar)
# ---------------------------------------------------------------------------

_SKILL_MD = """\
---
name: test-skill
description: A test skill.
version: 1.0.0
category: general
status: published
confidence: 0.9
source: learned
owner: {owner}
---

## When to Use
When testing.
"""


def test_rename_updates_skill_md_owner(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    skill_dir = tmp_path / "skills" / "general" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD.format(owner="alice"), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "owner: alice2" in content
    assert "owner: alice\n" not in content


def test_rename_leaves_other_skill_owners_untouched(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    for owner, name in [("alice", "alice-skill"), ("carol", "carol-skill")]:
        d = tmp_path / "skills" / "general" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_SKILL_MD.format(owner=owner).replace("test-skill", name), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    assert "owner: alice2" in (tmp_path / "skills" / "general" / "alice-skill" / "SKILL.md").read_text()
    assert "owner: carol" in (tmp_path / "skills" / "general" / "carol-skill" / "SKILL.md").read_text()


def test_rename_updates_usage_sidecar_keys(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint

    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True)
    usage = {
        "alice::test-skill": {"uses": 3, "last_used": 1000},
        "carol::other-skill": {"uses": 1, "last_used": 500},
        "unscoped-skill": {"uses": 2, "last_used": 200},
    }
    (skills_root / "_usage.json").write_text(json.dumps(usage), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    updated = json.loads((skills_root / "_usage.json").read_text(encoding="utf-8"))
    assert "alice2::test-skill" in updated
    assert "alice::test-skill" not in updated
    assert "carol::other-skill" in updated
    assert "unscoped-skill" in updated


def test_rename_no_skills_dir_does_not_crash(rename_endpoint):
    endpoint, _am, tmp_path = rename_endpoint
    res = asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))
    assert res["ok"] is True


def test_rename_skill_md_owner_case_insensitive(rename_endpoint):
    """SKILL.md written with owner: Alice (mixed case) must be updated when
    renaming alice — the regex was missing re.IGNORECASE."""
    endpoint, _am, tmp_path = rename_endpoint

    skill_dir = tmp_path / "skills" / "general" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD.format(owner="Alice"), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    assert "owner: alice2" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


def test_rename_usage_keys_case_insensitive(rename_endpoint):
    """_usage.json keys stored as Alice::skill-name must be migrated when
    renaming alice — the old startswith check was not lowercasing."""
    endpoint, _am, tmp_path = rename_endpoint

    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True)
    usage = {"Alice::my-skill": {"uses": 5, "last_used": 999}}
    (skills_root / "_usage.json").write_text(json.dumps(usage), encoding="utf-8")

    asyncio.run(endpoint("alice", SimpleNamespace(username="alice2"), _request(tmp_path)))

    updated = json.loads((skills_root / "_usage.json").read_text(encoding="utf-8"))
    assert "alice2::my-skill" in updated
    assert "Alice::my-skill" not in updated


# ---------------------------------------------------------------------------
# 5. Rollback: auth rename must be restored if SQL owner migration fails
# ---------------------------------------------------------------------------

def test_owner_migration_failure_rolls_back_auth_rename(monkeypatch, tmp_path):
    import routes.auth_routes as ar

    db = _force_sql_owner_migration_failure(monkeypatch)
    am = _auth_manager_for_rollback_test(monkeypatch, tmp_path)
    admin_token = am.create_session_trusted("admin")
    alice_token = am.create_session_trusted("alice")
    endpoint = _route(ar.setup_auth_routes(am), "rename_user")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint(
                "alice",
                SimpleNamespace(username="alice2"),
                _request(tmp_path, token=admin_token),
            )
        )

    assert exc.value.status_code == 500
    assert db.rolled_back is True
    assert db.closed is True
    assert "alice" in am.users
    assert "alice2" not in am.users
    assert am.get_username_for_token(alice_token) == "alice"
    saved_users = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))["users"]
    assert "alice" in saved_users
    assert "alice2" not in saved_users


def test_self_rename_owner_migration_failure_rolls_back_auth_session(monkeypatch, tmp_path):
    import routes.auth_routes as ar

    db = _force_sql_owner_migration_failure(monkeypatch)
    am = _auth_manager_for_rollback_test(monkeypatch, tmp_path)
    admin_token = am.create_session_trusted("admin")
    endpoint = _route(ar.setup_auth_routes(am), "rename_user")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint(
                "admin",
                SimpleNamespace(username="chief"),
                _request(tmp_path, token=admin_token),
            )
        )

    assert exc.value.status_code == 500
    assert db.rolled_back is True
    assert db.closed is True
    assert "admin" in am.users
    assert "chief" not in am.users
    assert am.get_username_for_token(admin_token) == "admin"
    saved_users = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))["users"]
    assert "admin" in saved_users
    assert "chief" not in saved_users


# ---------------------------------------------------------------------------
# 6. P1 regression: rejected auth rename must not mutate file-backed stores
# ---------------------------------------------------------------------------

def test_rejected_rename_does_not_mutate_files(monkeypatch, tmp_path):
    """If auth_manager.rename_user() returns False, no file-backed store
    should be touched. Before the fix the deep_research and memory writes
    ran before the auth check, so a rejected rename (e.g. reserved username)
    silently moved owner fields to the new name."""
    import routes.auth_routes as ar
    import core.database as cdb

    monkeypatch.setattr(cdb, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(cdb, "Base", SimpleNamespace(registry=SimpleNamespace(mappers=[])), raising=False)
    pr = types.ModuleType("routes.prefs_routes")
    pr._load = lambda: {}
    pr._save = lambda d: None
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", pr)
    monkeypatch.setattr(ar, "DEEP_RESEARCH_DIR", str(tmp_path / "deep_research"))
    monkeypatch.setattr(ar, "MEMORY_FILE", str(tmp_path / "memory.json"))
    monkeypatch.setattr(ar, "SKILLS_DIR", str(tmp_path / "skills"))

    # Seed files for alice.
    dr = tmp_path / "deep_research"
    dr.mkdir()
    rp = dr / "rp-abc.json"
    rp.write_text(json.dumps({"owner": "alice", "query": "q"}), encoding="utf-8")

    mem = tmp_path / "memory.json"
    mem.write_text(json.dumps([{"owner": "alice", "text": "x"}]), encoding="utf-8")

    skill_dir = tmp_path / "skills" / "general" / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD.format(owner="alice"), encoding="utf-8")

    # Auth rejects the rename (reserved name, race, etc.).
    am = MagicMock()
    am.is_admin.return_value = True
    am.get_username_for_token.return_value = "admin"
    am.users = {"alice": {}}
    am.rename_user.return_value = False
    endpoint = _route(ar.setup_auth_routes(am), "rename_user")

    with pytest.raises(Exception):
        asyncio.run(endpoint("alice", SimpleNamespace(username="api"), _request(tmp_path)))

    assert json.loads(rp.read_text())["owner"] == "alice", "research owner mutated after rejected rename"
    assert json.loads(mem.read_text())[0]["owner"] == "alice", "memory owner mutated after rejected rename"
    assert "owner: alice" in (skill_dir / "SKILL.md").read_text(), "skill owner mutated after rejected rename"
