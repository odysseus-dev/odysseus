"""Trusted local MCP credential broker.

Holds the current server-side delegation and attaches it on the way to
Odysseus MCP. The agent keeps a stable local connection and never sees
the token. This module is transport-only: no domain behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol


log = logging.getLogger(__name__)


class DelegationResolver(Protocol):
    def current_token(self, *, execution_id: str, workload_id: str) -> str:
        """Return the current delegation for this execution/workload."""


class McpTransport(Protocol):
    def send(self, request: McpRequest, token: str) -> McpResponse:
        """Deliver one MCP request with a server-side token attached."""


@dataclass(frozen=True)
class McpRequest:
    method: str
    tool: str
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class McpResponse:
    status: int
    body: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkloadContext:
    execution_id: str
    workload_id: str
    profile: str = ""


class McpBroker:
    def __init__(
        self,
        resolver: DelegationResolver,
        transport: McpTransport,
        *,
        surfaces: dict[str, object] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._surfaces = surfaces if surfaces is not None else {
            "agent_environment": {},
            "events": [],
            "profile_snapshots": [],
        }
        self._log = logger or log

    def forward(self, request: McpRequest, context: WorkloadContext) -> McpResponse:
        token = self._resolver.current_token(
            execution_id=context.execution_id,
            workload_id=context.workload_id,
        )
        self._record_surfaces(request, context)
        self._log.info(
            "mcp_forward tool=%s execution_id=%s workload_id=%s",
            request.tool,
            context.execution_id,
            context.workload_id,
        )
        return self._transport.send(request, token)

    def _record_surfaces(self, request: McpRequest, context: WorkloadContext) -> None:
        env = self._surfaces.get("agent_environment")
        if isinstance(env, dict):
            env["MCP_BROKER"] = "local"
            env["MCP_PROFILE"] = context.profile
        events = self._surfaces.get("events")
        if isinstance(events, list):
            events.append({
                "type": "mcp_forward",
                "tool": request.tool,
                "execution_id": context.execution_id,
                "workload_id": context.workload_id,
            })
        snapshots = self._surfaces.get("profile_snapshots")
        if isinstance(snapshots, list):
            snapshots.append({"profile": context.profile, "mcp": "local-broker"})
