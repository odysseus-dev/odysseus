"""Install tracked built-in skills into the shared immutable skill catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from src.constants import BUILTIN_SKILLS_DIR

from .skill_format import Skill
from .skills import SkillsManager


_BUILTIN_ROOT = Path(BUILTIN_SKILLS_DIR)
_SYNC_FIELDS = (
    "name",
    "description",
    "version",
    "category",
    "tags",
    "status",
    "confidence",
    "source",
    "owner",
    "when_to_use",
    "procedure",
    "pitfalls",
    "verification",
    "platforms",
    "requires_toolsets",
    "fallback_for_toolsets",
    "body_extra",
)


def install_builtin_skills(manager: SkillsManager, owners: Iterable[str]) -> int:
    """Copy missing built-in skills into the ownerless shared catalog.

    Built-ins are explicitly marked and remain ownerless because the on-disk
    skill path is not owner-qualified. ``SkillsManager.load(owner=...)``
    exposes only these immutable built-ins in addition to that owner's files.
    Installation is safe before first-user setup because no owner identity is
    assigned and unauthenticated requests still cannot access skill routes.
    """
    existing = {row.get("name"): row for row in manager.load_all()}
    installed = 0
    paths = sorted(_BUILTIN_ROOT.rglob("SKILL.md")) if _BUILTIN_ROOT.is_dir() else []
    for path in paths:
        try:
            skill = Skill.from_markdown(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Tracked procedures ship as trusted application behavior. They are
        # available immediately and never enter the user's audit queue.
        skill.status = "published"
        skill.confidence = 1.0
        row = existing.get(skill.name)
        if row:
            # Built-ins are immutable tracked assets. Synchronize updated
            # versions/procedures on startup while leaving usage counters in
            # their sidecar untouched. Older startup code could also stamp the
            # first admin onto one; normalize that migration at the same time.
            if row.get("source") == "builtin":
                skill.owner = ""
                skill.source = "builtin"
                desired = skill.to_dict()
                if any(row.get(field) != desired.get(field) for field in _SYNC_FIELDS):
                    manager.sync_builtin_skill(skill)
            continue
        skill.owner = ""
        skill.source = "builtin"
        manager.sync_builtin_skill(skill)
        existing[skill.name] = skill.to_dict()
        installed += 1
    return installed
