"""Single-dispatch controller for immutable Odysseus Cook task contracts."""

from __future__ import annotations

import uuid
from pathlib import Path

from src.cook_ops.contracts import Operation, SkillBinding, TaskContract, resolve_paths


_PROTECTED_ZONE_ROOTS = resolve_paths((
    Path(r"G:\AIW\00_IN"),
    Path(r"G:\AIW\05_BAK"),
))


class CookController:
    """Build and approve bounded contracts; execution is intentionally absent here."""

    def __init__(self, skill_registry: tuple[SkillBinding, ...]) -> None:
        self._skills = {binding.skill_id: binding for binding in skill_registry}

    @staticmethod
    def _validate_inside_allowed_roots(paths: tuple[Path, ...], roots: tuple[Path, ...]) -> None:
        if not roots:
            raise ValueError("allowed roots are required")
        for path in paths:
            if not any(path == root or root in path.parents for root in roots):
                raise ValueError(f"path {path} is outside allowed roots")

    @staticmethod
    def _validate_protected_zone_paths(paths: tuple[Path, ...], exceptions: tuple[Path, ...]) -> None:
        protected_paths = tuple(
            path for path in paths
            if any(path == root or root in path.parents for root in _PROTECTED_ZONE_ROOTS)
        )
        protected_set = set(protected_paths)
        exception_set = set(exceptions)
        if protected_set != exception_set:
            raise ValueError("protected zone paths require exact protected zone exceptions")

    def propose(
        self,
        *,
        operation: Operation,
        source_paths: list[Path],
        destination_path: Path | None,
        allowed_roots: list[Path],
        skill_ids: list[str] | None = None,
        protected_zone_exceptions: list[Path] | None = None,
    ) -> TaskContract:
        sources = resolve_paths(source_paths)
        roots = resolve_paths(allowed_roots)
        destination = Path(destination_path).expanduser().resolve() if destination_path else None
        exceptions = resolve_paths(protected_zone_exceptions or [])
        if operation in {Operation.COPY, Operation.MOVE} and (not sources or destination is None):
            raise ValueError("copy and move require source paths and a destination")
        self._validate_inside_allowed_roots(sources, roots)
        if destination is not None:
            self._validate_inside_allowed_roots((destination,), roots)
        self._validate_protected_zone_paths(sources + ((destination,) if destination else ()), exceptions)
        requested = skill_ids or []
        unknown = [skill_id for skill_id in requested if skill_id not in self._skills]
        if unknown:
            raise ValueError(f"unknown Cook personal skill: {unknown[0]}")
        return TaskContract(
            task_id=f"COOK-{uuid.uuid4().hex[:12].upper()}",
            operation=operation,
            source_paths=sources,
            destination_path=destination,
            allowed_roots=roots,
            skill_bindings=tuple(self._skills[skill_id] for skill_id in requested),
            protected_zone_exceptions=exceptions,
        ).with_hash()

    @staticmethod
    def approve(contract: TaskContract, approved_contract_sha256: str) -> TaskContract:
        if not approved_contract_sha256 or approved_contract_sha256 != contract.contract_sha256:
            raise ValueError("approval hash does not match the current contract")
        return contract.mark_approved()
