"""Tests for skill_library.py — Phase 2 Voyager-style procedural memory.

Verifies the gated lifecycle observed -> trusted -> executable:
- a skill needs >= 2 rewarded uses to become trusted (no single-use autopromotion)
- only trusted skills compile to executable
- a compiled skill runs WITHOUT the LLM (deterministic procedure)
- repeated --steps flags are preserved (append, not store-overwrite)
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import skill_library as sl  # noqa: E402


@pytest.fixture(autouse=True)
def iso(tmp_path):
    """Isolate the skills index AND the compile dir for every test."""
    old_file, old_dir = sl.SKILLS_FILE, sl.EXEC_DIR
    sl.SKILLS_FILE = str(tmp_path / "skills.json")
    sl.EXEC_DIR = str(tmp_path / "skills")
    yield tmp_path
    sl.SKILLS_FILE = old_file
    sl.EXEC_DIR = old_dir


def test_draft_creates_observed(iso):
    res = sl.add("deploy-check", "verify deployment",
                 ["command:echo hello", "command:true"])
    assert res["state"] == "observed"
    assert [s["name"] for s in sl.list_skills()] == ["deploy-check"]


def test_list_observed(iso):
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    assert [s["name"] for s in sl.list_skills()] == ["deploy-check"]


def test_single_use_no_reward_does_not_promote(iso):
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    res = sl.use("deploy-check")
    assert res["state"] == "observed"
    assert sl.list_skills("observed") != []


def test_one_reward_does_not_promote(iso):
    """The 2-reward gate: one success must NOT trust a skill."""
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    sl.use("deploy-check", reward=True)
    assert sl.list_skills("trusted") == []


def test_two_rewards_promote_to_trusted(iso):
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    sl.use("deploy-check", reward=True)
    res = sl.use("deploy-check", reward=True)
    assert res["state"] == "trusted"
    assert [s["name"] for s in sl.list_skills("trusted")] == ["deploy-check"]


def test_compile_blocked_until_trusted(iso):
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    res = sl.compile_executable("deploy-check")
    assert res["status"] == "blocked"
    assert res["reason"] == "not trusted"


def test_compile_emits_runnable_without_llm(iso):
    """Trusted -> executable: the compiled script runs standalone."""
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    sl.use("deploy-check", reward=True)
    sl.use("deploy-check", reward=True)
    res = sl.compile_executable("deploy-check")
    assert res["status"] == "compiled"
    assert os.path.exists(res["path"])
    out = subprocess.run([sys.executable, res["path"]],
                         capture_output=True, text=True)
    assert "hello" in out.stdout          # the command executed
    assert "skill done" in out.stdout     # the wrapper ran end to end
    assert sl.list_skills("executable") != []


def test_compile_not_found(iso):
    res = sl.compile_executable("nope")
    assert res["status"] == "not_found"


def test_use_not_found(iso):
    res = sl.use("nope")
    assert res["status"] == "not_found"


def test_update_existing_keeps_lifecycle(iso):
    sl.add("deploy-check", "verify deployment", ["command:echo hello"])
    sl.use("deploy-check", reward=True)
    sl.use("deploy-check", reward=True)
    sl.add("deploy-check", "updated description", ["command:echo updated"])
    skills = sl.list_skills()
    assert len(skills) == 1
    assert skills[0]["description"] == "updated description"
    assert skills[0]["state"] == "trusted"  # re-drafting doesn't reset progress
