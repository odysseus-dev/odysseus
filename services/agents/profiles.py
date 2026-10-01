"""Odysseus-adjacent profile policy keyed by upstream profile revision."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .archetypes import TaskArchetype
from .contracts import OdysseusProfilePolicy

_POLICY_KEYS = (
    "agent_profile_id",
    "agent_profile_revision",
    "capabilities",
    "archetypes",
    "risk_class",
    "delegation_depth",
    "workspace_classes",
    "budget_class",
    "distribution_metadata",
    "policy_revision",
)


def load_profile_policies(path: str | Path) -> list[OdysseusProfilePolicy]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = payload.get("profiles", payload if isinstance(payload, list) else [payload])
    policies: list[OdysseusProfilePolicy] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"profile {index}: mapping required")
        unknown = set(row) - set(_POLICY_KEYS)
        if unknown:
            raise ValueError(f"profile {index}: unknown keys {sorted(unknown)}")
        missing = [key for key in ("agent_profile_id", "agent_profile_revision", "policy_revision") if key not in row]
        if missing:
            raise ValueError(f"profile {index}: missing {missing[0]}")
        policies.append(
            OdysseusProfilePolicy(
                agent_profile_id=str(row["agent_profile_id"]),
                agent_profile_revision=row["agent_profile_revision"],
                capabilities=tuple(row.get("capabilities") or ()),
                archetypes=tuple(row.get("archetypes") or ()),
                risk_class=str(row.get("risk_class") or "low"),
                delegation_depth=int(row.get("delegation_depth") or 0),
                workspace_classes=tuple(row.get("workspace_classes") or ()),
                budget_class=str(row.get("budget_class") or "default"),
                distribution_metadata=dict(row.get("distribution_metadata") or {}),
                policy_revision=row.get("policy_revision"),
            )
        )
    return policies


def select_compatible_profiles(
    archetype: TaskArchetype,
    policies: Sequence[OdysseusProfilePolicy] | Iterable[OdysseusProfilePolicy],
) -> list[OdysseusProfilePolicy]:
    required = set(archetype.required_capabilities)
    selected: list[OdysseusProfilePolicy] = []
    for policy in policies:
        if archetype.id not in policy.archetypes:
            continue
        if not required.issubset(policy.capabilities):
            continue
        selected.append(policy)
    return selected
