"""Canonical runtime capability records shared by execution adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


@dataclass(frozen=True)
class VerifierCapability:
    command: str
    kind: str
    source_path: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionCapabilities:
    cwd: str
    commands: tuple[str, ...] = ()
    verifiers: tuple[VerifierCapability, ...] = ()
    probe_return_code: int = 0
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_probe(cls, payload: Mapping[str, Any]) -> "ExecutionCapabilities":
        cwd = str(payload.get("cwd") or "").strip()
        commands = tuple(sorted({
            str(name).strip()
            for name in payload.get("commands") or ()
            if str(name).strip()
        }))
        verifiers: list[VerifierCapability] = []
        seen_commands: set[str] = set()
        for raw in payload.get("verifiers") or ():
            if not isinstance(raw, Mapping):
                continue
            command = str(raw.get("command") or "").strip()
            kind = str(raw.get("kind") or "").strip()
            source_path = str(raw.get("source_path") or "").strip()
            if (
                not command
                or command in seen_commands
                or not kind
                or not _safe_source_path(source_path)
            ):
                continue
            seen_commands.add(command)
            verifiers.append(VerifierCapability(command, kind, source_path))
        try:
            return_code = int(payload.get("probe_return_code") or 0)
        except (TypeError, ValueError):
            return_code = 1
        warnings = tuple(
            str(value).strip()[:500]
            for value in payload.get("warnings") or ()
            if str(value).strip()
        )
        return cls(
            cwd=cwd,
            commands=commands,
            verifiers=tuple(verifiers),
            probe_return_code=return_code,
            warnings=warnings,
        )

    @property
    def verifier_commands(self) -> tuple[str, ...]:
        return tuple(item.command for item in self.verifiers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "commands": list(self.commands),
            "verifiers": [item.to_dict() for item in self.verifiers],
            "probe_return_code": self.probe_return_code,
            "warnings": list(self.warnings),
        }


def _safe_source_path(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return ".." not in path.parts


def verifier_prompt(capabilities: ExecutionCapabilities) -> str:
    """Return a compact directive containing only executable verifier facts."""

    if not capabilities.verifiers:
        return ""
    commands = "\n".join(
        f"- `{item.command}` ({item.kind}, discovered from `{item.source_path}`)"
        for item in capabilities.verifiers
    )
    return (
        "\n\nVisible task-provided verification is available. After making changes, "
        "run one applicable command below and address any failure before finishing:\n"
        f"{commands}"
    )
