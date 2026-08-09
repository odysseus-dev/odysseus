"""Immutable task and skill binding contracts for Odysseus Cook."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterable


class Operation(str, Enum):
    INSPECT = "inspect"
    COPY = "copy"
    MOVE = "move"
    POWERSHELL = "powershell"


class TaskStatus(str, Enum):
    PROPOSED = "PROPOSED"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class SkillBinding:
    skill_id: str
    version: str
    sha256: str
    allowed_roles: tuple[str, ...]


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    operation: Operation
    source_paths: tuple[Path, ...]
    destination_path: Path | None
    allowed_roots: tuple[Path, ...]
    skill_bindings: tuple[SkillBinding, ...]
    protected_zone_exceptions: tuple[Path, ...] = ()
    status: TaskStatus = TaskStatus.PROPOSED
    approved: bool = False
    contract_sha256: str = ""

    def canonical_payload(self) -> dict:
        return {
            "task_id": self.task_id,
            "operation": self.operation.value,
            "source_paths": [str(path) for path in self.source_paths],
            "destination_path": str(self.destination_path) if self.destination_path else None,
            "allowed_roots": [str(path) for path in self.allowed_roots],
            "protected_zone_exceptions": [str(path) for path in self.protected_zone_exceptions],
            "skill_bindings": [
                {
                    "skill_id": binding.skill_id,
                    "version": binding.version,
                    "sha256": binding.sha256,
                    "allowed_roles": list(binding.allowed_roles),
                }
                for binding in self.skill_bindings
            ],
        }

    def with_hash(self) -> "TaskContract":
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return replace(self, contract_sha256=hashlib.sha256(encoded).hexdigest())

    def mark_approved(self) -> "TaskContract":
        return replace(self, approved=True, status=TaskStatus.READY_FOR_APPROVAL)


def resolve_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(Path(path).expanduser().resolve() for path in paths)
