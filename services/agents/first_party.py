"""First-party OpenHands profile packaging. OdysseusAgent is composition only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .delegation import DelegationAuthority, DelegationToken

_DEFAULT_PROFILES = Path(__file__).resolve().parents[2] / "deploy" / "openhands" / "profiles"
_SYSTEM = Path(__file__).resolve().parents[2] / "deploy" / "openhands" / "agents" / "odysseus-system.md"
_ALLOWED = {
    "agent_profile_id",
    "agent_profile_revision",
    "transport",
    "agent",
    "command",
    "snapshot_rejects_unknown_fields",
    "capabilities",
    "archetypes",
    "system_suffix_file",
    "mcp",
    "extra",
}


@dataclass(frozen=True)
class OdysseusAgent:
    base_agent: str
    system_suffix: str
    skills: tuple[str, ...]
    mcp: tuple[str, ...]
    archetype_context: str
    touches_database: bool = False
    touches_domain_services: bool = False
    touches_provider_clients: bool = False


@dataclass(frozen=True)
class LaunchedProfile:
    profile_id: str
    profile_revision: int
    snapshot_rejects_unknown_fields: bool
    agent: OdysseusAgent | None
    transport: str


@dataclass(frozen=True)
class LaunchedExecution:
    execution_id: str
    profile_id: str
    profile_revision: int
    scopes: frozenset[str]
    budget: int
    workspace_policy: str | None
    token: DelegationToken
    delegation_id: str | None = None


class FirstPartyProfileStack:
    def __init__(
        self,
        profiles_dir: str | Path | None = None,
        authority: DelegationAuthority | None = None,
    ) -> None:
        self.profiles_dir = Path(profiles_dir or _DEFAULT_PROFILES)
        self.authority = authority or DelegationAuthority(hmac_key=b"first-party-hmac-key-32-bytes!!")
        self._seq = 100

    def _load(self, profile_id: str) -> dict[str, Any]:
        path = self.profiles_dir / f"{profile_id}.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = set(payload) - _ALLOWED
        if unknown:
            raise ValueError(f"unknown keys {sorted(unknown)}")
        return payload

    def launch_profile(self, profile_id: str) -> LaunchedProfile:
        payload = self._load(profile_id)
        suffix = ""
        suffix_file = payload.get("system_suffix_file")
        if suffix_file:
            suffix = Path(__file__).resolve().parents[2].joinpath(suffix_file).read_text(encoding="utf-8")
        agent = None
        if payload.get("agent") == "CodeActAgent":
            agent = OdysseusAgent(
                base_agent="CodeActAgent",
                system_suffix=suffix,
                skills=("approved",),
                mcp=tuple(payload.get("mcp") or ()),
                archetype_context="chat",
            )
        return LaunchedProfile(
            profile_id=payload["agent_profile_id"],
            profile_revision=int(payload["agent_profile_revision"]),
            snapshot_rejects_unknown_fields=bool(payload.get("snapshot_rejects_unknown_fields") or payload.get("extra") == "forbid"),
            agent=agent,
            transport=str(payload.get("transport") or "native"),
        )

    def launch_execution(
        self,
        *,
        profile: str,
        execution_id: str,
        authority: DelegationAuthority | None = None,
    ) -> LaunchedExecution:
        launched = self.launch_profile(profile)
        auth = authority or self.authority
        token = auth.issue(
            audience="odysseus-mcp",
            execution_id=execution_id,
            conversation_id="C10",
            owner="user-1",
            scopes={"notes.read", "documents.read", "research.invoke"},
            resources={"note:1"},
            profile_id=launched.profile_id,
            profile_revision=launched.profile_revision,
            archetype_id="chat",
            archetype_version=1,
            budget=100,
            max_depth=1,
            ttl_seconds=60,
            allowed_child_archetypes={"chat", "deep-research"},
        )
        return LaunchedExecution(
            execution_id=execution_id,
            profile_id=launched.profile_id,
            profile_revision=launched.profile_revision,
            scopes=token.claims.scopes,
            budget=token.claims.budget,
            workspace_policy=None,
            token=token,
        )

    def launch_local_subagent(
        self,
        parent: LaunchedExecution,
        *,
        scopes: set[str],
        delegation_id: str,
    ) -> LaunchedExecution:
        if not set(scopes) <= parent.scopes:
            raise ValueError("child scopes exceed parent")
        return LaunchedExecution(
            execution_id=parent.execution_id,
            profile_id=parent.profile_id,
            profile_revision=parent.profile_revision,
            scopes=frozenset(scopes),
            budget=parent.budget,
            workspace_policy=parent.workspace_policy,
            token=parent.token,
            delegation_id=delegation_id,
        )

    def launch_managed_child(
        self,
        parent: LaunchedExecution,
        *,
        profile: str,
        archetype: str,
    ) -> LaunchedExecution:
        launched = self.launch_profile(profile)
        self._seq += 1
        execution_id = f"R{self._seq}"
        token = self.authority.issue_child(
            parent.token,
            scopes={"research.invoke", "documents.read"},
            resources={"note:1"},
            archetype_id=archetype,
            profile_id=launched.profile_id,
            profile_revision=launched.profile_revision,
        )
        return LaunchedExecution(
            execution_id=execution_id,
            profile_id=launched.profile_id,
            profile_revision=launched.profile_revision,
            scopes=token.claims.scopes,
            budget=token.claims.budget,
            workspace_policy="research",
            token=token,
        )
