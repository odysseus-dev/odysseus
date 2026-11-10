"""index_for() local-model gating: a `remote-only` tag must hide a skill
from local/private-endpoint models while staying visible to remote API
models and to callers that don't know the endpoint at all.

Big on-demand reference lookups overwhelm small local contexts, so the
skill must vanish from the injected index there. local_model=None (API
listings, chat preface) means "don't gate" — same contract as
active_toolsets=None.
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


def _write_skill_md(skills_root: Path, name: str, *, tags: str = "[]",
                    owner: str = "admin") -> None:
    skill_dir = skills_root / "general" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = "\n".join([
        "---",
        f"name: {name}",
        "description: test skill",
        "version: 1.0.0",
        "category: general",
        f"tags: [{tags}]",
        "status: published",
        "confidence: 0.9",
        "audit_verdict: pass",
        "source: learned",
        f"owner: {owner}",
        "created: 2026-01-01T00:00:00Z",
        "---",
        "",
        "## When to Use",
        "- test",
        "",
        "## Procedure",
        "1. step 1",
        "",
    ])
    (skill_dir / "SKILL.md").write_text(fm, encoding="utf-8")


def _names(idx):
    return {s["name"] for s in idx}


def test_remote_only_hidden_from_local_model(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "agents-map", tags="reference, remote-only")
    sm = SkillsManager(str(tmp_path))
    sm.set_audit("agents-map", "pass", owner="admin")

    assert "agents-map" not in _names(
        sm.index_for(owner="admin", local_model=True))


def test_remote_only_visible_to_remote_model_and_unknown_endpoint(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "agents-map", tags="reference, remote-only")
    sm = SkillsManager(str(tmp_path))
    sm.set_audit("agents-map", "pass", owner="admin")

    assert "agents-map" in _names(sm.index_for(owner="admin", local_model=False))
    # Unknown endpoint (background callers without a URL) fails open.
    assert "agents-map" in _names(sm.index_for(owner="admin", local_model=None))
    assert "agents-map" in _names(sm.index_for(owner="admin"))


def test_untagged_skills_are_never_gated(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "plain-skill", tags="reference")
    sm = SkillsManager(str(tmp_path))
    sm.set_audit("plain-skill", "pass", owner="admin")

    assert "plain-skill" in _names(sm.index_for(owner="admin", local_model=True))
    assert "plain-skill" in _names(sm.index_for(owner="admin", local_model=False))


def test_tag_in_ui_listings_is_not_gated(tmp_path):
    (tmp_path / "skills").mkdir()
    _write_skill_md(tmp_path / "skills", "agents-map", tags="reference, remote-only")
    sm = SkillsManager(str(tmp_path))
    sm.set_audit("agents-map", "pass", owner="admin")

    # API listings / UI pass no local_model → must still see the skill.
    assert "agents-map" in _names(sm.index_for(owner="admin", local_model=None))
