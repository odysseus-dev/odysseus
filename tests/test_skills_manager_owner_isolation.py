"""Independent validation test for the claim that
`SkillsManager.update_skill` mutates the first skill on disk matching
`name` regardless of the caller's owner, and that `owner` is in its
`scalar_keys` whitelist allowing cross-user ownership reassignment.

This test sets up two user-owned skills on disk with the SAME slug
(`login-flow`) — Alice's and Bob's — and then calls `update_skill` with
NO `owner` argument. If the bug is real, exactly one of the two files
will be mutated (whichever `_iter_skill_files` yields first) and the
caller will have effectively re-stamped the file as owned by the value
in `updates["owner"]` ("attacker"). If the manager method is safe (or
the slug uniqueness invariant makes the bug moot), the call should
either:
  * raise (it requires an `owner` argument), OR
  * be a no-op (no other side effect on Bob's file), OR
  * the file that gets modified should still belong to its original
    owner (no ownership reassignment).

We assert the safer behaviors; the test FAILS only when update_skill
silently mutates a file owned by a different user AND overwrites the
`owner` field with an attacker's value.
"""

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── module-load stubbing (matches other tests in this repo) ──────────
# Stub heavy deps so importing the skills manager doesn't pull DB / FastAPI.
for _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative"):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

from services.memory.skills import SkillsManager  # noqa: E402
from services.memory.skill_format import Skill, slugify  # noqa: E402


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


def test_update_skill_does_not_mutate_foreign_owned_skill(tmp_path):
    """Two users own distinct skills with the same slug. update_skill()
    called WITHOUT an owner argument must not silently overwrite the
    wrong file or change its owner field."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    # Create two distinct on-disk skills with the SAME slug but in
    # DIFFERENT category directories so they are real, separately
    # addressable files. (The on-disk layout is
    # `<category>/<name>/SKILL.md`, so two users can in fact have
    # the same slug under different categories — exactly the situation
    # that triggers the first-match-wins bug in update_skill.)
    alice_path = _write_skill_md(
        skills_root, category="alice-cat", name="login-flow",
        owner="alice", description="alice original",
    )
    bob_path = _write_skill_md(
        skills_root, category="bob-cat", name="login-flow",
        owner="bob", description="bob original",
    )
    assert alice_path != bob_path
    assert alice_path.exists() and bob_path.exists()

    sm = SkillsManager(str(tmp_path))

    # Snapshot before.
    before_alice = alice_path.read_text(encoding="utf-8")
    before_bob = bob_path.read_text(encoding="utf-8")

    # Try to reassign + mutate. The caller does NOT supply an owner
    # arg, mirroring the in-process callers in tool_implementations.py
    # (lines 716, 740, 753) which call sm.update_skill(name, updates).
    try:
        result = sm.update_skill(
            "login-flow",
            {"owner": "attacker", "description": "pwned"},
        )
    except TypeError as e:
        # If the method were fixed to require an owner arg, this is
        # the desired (safe) behavior — the call refused.
        pytest.skip(
            f"update_skill raised TypeError (refused unsafe call): {e}"
        )
        return

    # After: read what each file now contains.
    after_alice = alice_path.read_text(encoding="utf-8")
    after_bob = bob_path.read_text(encoding="utf-8")

    # Invariant 1: a file that was owned by `alice` (resp. `bob`) MUST
    # NOT end up owned by `attacker` after the call. If it does, that's
    # the cross-user ownership reassignment bug.
    assert "owner: attacker" not in after_alice, (
        "BUG: Alice's file was silently re-owned as 'attacker' by "
        "update_skill (cross-user ownership reassignment)."
    )
    assert "owner: attacker" not in after_bob, (
        "BUG: Bob's file was silently re-owned as 'attacker' by "
        "update_skill (cross-user ownership reassignment)."
    )

    # Invariant 2: a file that was owned by `alice` and contained
    # description "alice original" must not be silently mutated into
    # "pwned" by a caller that did not supply an owner.
    if "alice original" in before_alice:
        assert "alice original" in after_alice, (
            "BUG: Alice's skill description was overwritten by a call "
            "to update_skill that did not scope to her owner."
        )

    if "bob original" in before_bob:
        assert "bob original" in after_bob, (
            "BUG: Bob's skill description was overwritten by a call "
            "to update_skill that did not scope to his owner."
        )

    # The return value should not lie about success — if the manager
    # touched nothing because both files were foreign-owned, the safer
    # behavior is to return False, not True. (A return of True is the
    # buggy path; we don't assert False, we just don't assert True.)
    _ = result  # not asserted; documented behavior is not the point.


def test_update_skill_scalar_keys_exclude_owner():
    """Static check: the manager's scalar_keys whitelist MUST NOT
    include 'owner' — otherwise a non-owner caller can pass
    updates={'owner': 'attacker'} and reassign the file. The fix
    removed 'owner' from scalar_keys; this test now asserts the
    fix is in place."""
    src = Path("services/memory/skills.py").read_text(encoding="utf-8")
    import re
    m = re.search(
        r"def update_skill\(.*?scalar_keys\s*=\s*\((.*?)\)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate scalar_keys tuple in update_skill"
    body = m.group(1)
    assert '"owner"' not in body and "'owner'" not in body, (
        "BUG (regression): scalar_keys in update_skill includes 'owner'. "
        "The fix removed this to prevent cross-user ownership reassignment "
        "via the updates dict."
    )


def test_read_skill_md_and_references_are_owner_scoped(tmp_path):
    """Two users own distinct skills with the same slug. read_skill_md()
    called with owner='alice' must return Alice's content, not Bob's.
    Called without an owner it must match only ownerless skills."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    alice_path = _write_skill_md(
        skills_root, category="alice-cat", name="login-flow",
        owner="alice", description="alice secret",
    )
    bob_path = _write_skill_md(
        skills_root, category="bob-cat", name="login-flow",
        owner="bob", description="bob secret",
    )
    refs = bob_path.parent / "references"
    refs.mkdir()
    (refs / "notes.txt").write_text("bob private notes", encoding="utf-8")

    sm = SkillsManager(str(tmp_path))

    alice_md = sm.read_skill_md("login-flow", owner="alice")
    assert alice_md is not None, "read_skill_md returned None for alice's skill"
    assert "alice secret" in alice_md

    bob_md = sm.read_skill_md("login-flow", owner="bob")
    assert bob_md is not None, "read_skill_md returned None for bob's skill"
    assert "bob secret" in bob_md

    no_owner_md = sm.read_skill_md("login-flow")
    assert no_owner_md is None, (
        "read_skill_md without owner matched an owned skill — "
        "default should only match ownerless skills."
    )
    assert sm.read_skill_md("login-flow", owner="charlie") is None
    assert sm.read_skill_reference("login-flow", "references/notes.txt", owner="bob") == "bob private notes"
    assert sm.read_skill_reference("login-flow", "references/notes.txt", owner="alice") is None


def test_update_skill_positive_scoping(tmp_path):
    """Alice CAN update her own skill. Two users with the same slug;
    update_skill(owner='alice') modifies only Alice's file."""
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    alice_path = _write_skill_md(
        skills_root, category="alice-cat", name="login-flow",
        owner="alice", description="alice original",
    )
    bob_path = _write_skill_md(
        skills_root, category="bob-cat", name="login-flow",
        owner="bob", description="bob original",
    )

    sm = SkillsManager(str(tmp_path))

    ok = sm.update_skill("login-flow", {"description": "alice updated"}, owner="alice")
    assert ok, "update_skill(owner='alice') should succeed on alice's file"

    after_alice = alice_path.read_text(encoding="utf-8")
    after_bob = bob_path.read_text(encoding="utf-8")

    assert "alice updated" in after_alice, (
        "Alice's file was not updated despite passing owner='alice'."
    )
    assert "bob original" in after_bob and "alice updated" not in after_bob, (
        "Bob's file was mutated by Alice's update_skill call — cross-tenant leak."
    )


def test_add_skill_dedup_does_not_cross_owners(tmp_path):
    sm = SkillsManager(str(tmp_path))
    first = sm.add_skill(
        name="shared-flow",
        description="same description",
        category="general",
        when_to_use="same trigger",
        procedure=["same procedure"],
        owner="alice",
        source="learned",
    )
    second = sm.add_skill(
        name="shared-flow",
        description="same description",
        category="general",
        when_to_use="same trigger",
        procedure=["same procedure"],
        owner="bob",
        source="learned",
    )

    assert not first.get("_deduped")
    assert not second.get("_deduped")
    assert second.get("owner") == "bob"


def test_usage_sidecar_is_owner_scoped(tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    _write_skill_md(
        skills_root, category="alice-cat", name="shared-flow",
        owner="alice", description="alice secret",
    )
    _write_skill_md(
        skills_root, category="bob-cat", name="shared-flow",
        owner="bob", description="bob secret",
    )

    sm = SkillsManager(str(tmp_path))
    sm.record_use("shared-flow", owner="alice")
    sm.set_audit("shared-flow", "pass", by_teacher=False, owner="bob")
    sm.set_necessity("shared-flow", False, ["other-flow"], "redundant", owner="bob")

    alice = sm.load(owner="alice")[0]
    bob = sm.load(owner="bob")[0]

    assert alice["uses"] == 1
    assert alice["audit_verdict"] is None
    assert alice["necessity"] is None
    assert bob["uses"] == 0
    assert bob["audit_verdict"] == "pass"
    assert bob["necessity"] == {
        "necessary": False,
        "redundant_with": ["other-flow"],
        "reason": "redundant",
    }
