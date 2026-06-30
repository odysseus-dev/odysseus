"""index_for() toolset gating: requires_toolsets must only filter when the
caller provides an explicit active-toolset list.

Callers that don't know the active tool set (API skill listings, the chat
preface) pass active_toolsets=None. The old behavior coerced None to [] and
hid every skill that declared requires_toolsets — so a skill like a local
notes lookup that needs grep + read_file silently vanished from the index
the moment it declared its tool needs. None now means "don't gate".
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

# ── module-load stubbing (matches other tests in this repo) ──────────
for _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative"):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ImportError:
            sys.modules[_mod] = MagicMock()

from services.memory.skills import SkillsManager  # noqa: E402


def _write_skill_md(skills_root: Path, name: str, *, requires: str = "",
                    fallback: str = "") -> Path:
    skill_dir = skills_root / "general" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f"name: {name}",
        "description: test skill",
        "version: 1.0.0",
        "category: general",
        "tags: []",
    ]
    if requires:
        fm.append(f"requires_toolsets: [{requires}]")
    if fallback:
        fm.append(f"fallback_for_toolsets: [{fallback}]")
    fm += [
        "status: published",
        "confidence: 0.9",
        "source: learned",
        "created: 2026-01-01T00:00:00Z",
        "---",
        "",
        "## When to Use",
        "- test",
        "",
        "## Procedure",
        "1. step 1",
        "",
    ]
    path = skill_dir / "SKILL.md"
    path.write_text("\n".join(fm), encoding="utf-8")
    return path


def _names(idx):
    return {s["name"] for s in idx}


def test_requires_toolsets_not_gated_when_active_set_unknown(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "notes-lookup", requires="grep, read_file")
    sm = SkillsManager(str(tmp_path))

    # None = caller doesn't know the active tool set → no gating.
    assert "notes-lookup" in _names(sm.index_for())
    assert "notes-lookup" in _names(sm.index_for(active_toolsets=None))


def test_requires_toolsets_gates_on_explicit_list(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "notes-lookup", requires="grep, read_file")
    sm = SkillsManager(str(tmp_path))

    # Explicit list missing a required tool → hidden.
    assert "notes-lookup" not in _names(sm.index_for(active_toolsets=["grep"]))
    assert "notes-lookup" not in _names(sm.index_for(active_toolsets=[]))
    # All required tools active → visible.
    assert "notes-lookup" in _names(
        sm.index_for(active_toolsets=["grep", "read_file", "ls"]))


def test_fallback_for_toolsets_unaffected_by_none(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "web-fallback", fallback="web_search")
    sm = SkillsManager(str(tmp_path))

    # Fallback skills hide only when the toolset they substitute for is
    # known to be active.
    assert "web-fallback" in _names(sm.index_for(active_toolsets=None))
    assert "web-fallback" in _names(sm.index_for(active_toolsets=[]))
    assert "web-fallback" not in _names(
        sm.index_for(active_toolsets=["web_search"]))
