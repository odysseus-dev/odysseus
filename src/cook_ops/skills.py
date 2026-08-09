"""Read and bind a curated subset of Odysseus personal skills for Cook."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from src.cook_ops.contracts import SkillBinding
from src.runtime_paths import get_app_root


@dataclass(frozen=True)
class _SkillSpec:
    skill_id: str
    relative_path: str
    allowed_roles: tuple[str, ...]


_CURATED_SKILLS = (
    _SkillSpec(
        "directory-management-protocol",
        "data/skills/general/directory-management-protocol/SKILL.md",
        ("TASK_INTAKE", "FILE_INVENTORY", "FILE_MOVE_PLANNER"),
    ),
    _SkillSpec(
        "verify-and-backup-archive-files",
        "data/skills/general/verify-and-backup-archive-files/SKILL.md",
        ("FILE_INVENTORY", "FILE_MOVE_PLANNER", "FILE_VERIFY"),
    ),
    _SkillSpec(
        "powershell-audit-pre-flight-creation",
        "data/skills/general/powershell-audit-pre-flight-creation/SKILL.md",
        ("POWERSHELL_PREFLIGHT",),
    ),
    _SkillSpec(
        "safe-haven-creation-recovery",
        "data/skills/general/safe-haven-creation-recovery/SKILL.md",
        ("RECOVERY_AND_QUARANTINE", "HUMAN_SPEECH_AND_HANDOFF"),
    ),
    _SkillSpec(
        "odx-backup-file-verification",
        "data/skills/general/odx-backup-file-verification/SKILL.md",
        ("FILE_INVENTORY", "FILE_VERIFY"),
    ),
)


def _version_from_skill(text: str, skill_id: str) -> str:
    match = re.search(r"^version:\s*['\"]?([^'\"\s]+)", text, re.MULTILINE)
    if not match:
        raise ValueError(f"Cook personal skill {skill_id!r} has no version")
    return match.group(1)


def load_personal_skill_registry(app_root: Path | None = None) -> tuple[SkillBinding, ...]:
    """Return only the five approved personal skill packs, hash-bound to bytes."""
    root = Path(app_root or get_app_root()).resolve()
    bindings: list[SkillBinding] = []
    for spec in _CURATED_SKILLS:
        path = (root / spec.relative_path).resolve()
        try:
            path.relative_to(root)
            raw = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cook personal skill unavailable: {spec.skill_id}") from exc
        text = raw.decode("utf-8")
        bindings.append(
            SkillBinding(
                skill_id=spec.skill_id,
                version=_version_from_skill(text, spec.skill_id),
                sha256=hashlib.sha256(raw).hexdigest(),
                allowed_roles=spec.allowed_roles,
            )
        )
    return tuple(bindings)
