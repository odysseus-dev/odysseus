"""Narrow ACP MCP transport adapter.

Translates ACP MCP configuration onto the trusted local broker. The proxy
attaches broker context only. It does not call Odysseus REST, store durable
tokens, or implement domain rules.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BROKER_PATH = Path(__file__).with_name("mcp_broker.py")
_SPEC = importlib.util.spec_from_file_location("_odysseus_mcp_broker", _BROKER_PATH)
assert _SPEC and _SPEC.loader
_broker = importlib.util.module_from_spec(_SPEC)
import sys as _sys
_sys.modules[_SPEC.name] = _broker
_SPEC.loader.exec_module(_broker)
McpBroker = _broker.McpBroker
McpRequest = _broker.McpRequest
WorkloadContext = _broker.WorkloadContext


@dataclass(frozen=True)
class AcpSession:
    profile: str
    execution_id: str
    workload_id: str
    mcp_url: str = "local-broker"
    cancelled: bool = False


@dataclass(frozen=True)
class AcpCallResult:
    allowed: bool
    status: int = 0


class AcpMcpProxy:
    def __init__(
        self,
        broker: McpBroker,
        *,
        allowed_scopes: set[str],
        surfaces: dict[str, Any] | None = None,
    ) -> None:
        self._broker = broker
        self._allowed_scopes = set(allowed_scopes)
        self._surfaces = surfaces if surfaces is not None else {
            "agent_environment": {},
            "events": [],
            "profile_snapshots": [],
        }
        self._sessions: dict[tuple[str, str], AcpSession] = {}

    def connect(self, session: AcpSession, mcp_config: dict[str, object]) -> AcpSession:
        del mcp_config
        bound = AcpSession(
            profile=session.profile,
            execution_id=session.execution_id,
            workload_id=session.workload_id,
            mcp_url="local-broker",
            cancelled=False,
        )
        self._sessions[(session.execution_id, session.workload_id)] = bound
        return bound

    def call(self, session: AcpSession, tool: str) -> AcpCallResult:
        current = self._sessions.get((session.execution_id, session.workload_id), session)
        if current.cancelled or tool not in self._allowed_scopes:
            return AcpCallResult(allowed=False, status=403)
        response = self._broker.forward(
            McpRequest(method="tools/call", tool=tool),
            WorkloadContext(
                execution_id=current.execution_id,
                workload_id=current.workload_id,
                profile=current.profile,
            ),
        )
        allowed = response.status == 200 and bool(response.body.get("allowed", True))
        return AcpCallResult(allowed=allowed, status=response.status)

    def disconnect(self, session: AcpSession) -> None:
        events = self._surfaces.setdefault("events", [])
        if isinstance(events, list):
            events.append({"type": "acp_disconnect", "profile": session.profile})

    def reconnect(self, session: AcpSession) -> AcpSession:
        bound = AcpSession(
            profile=session.profile,
            execution_id=session.execution_id,
            workload_id=session.workload_id,
            mcp_url="local-broker",
            cancelled=False,
        )
        self._sessions[(session.execution_id, session.workload_id)] = bound
        return bound

    def cancel(self, session: AcpSession) -> AcpSession:
        bound = AcpSession(
            profile=session.profile,
            execution_id=session.execution_id,
            workload_id=session.workload_id,
            mcp_url="local-broker",
            cancelled=True,
        )
        self._sessions[(session.execution_id, session.workload_id)] = bound
        return bound

    def snapshot(self, session: AcpSession) -> dict[str, object]:
        current = self._sessions.get((session.execution_id, session.workload_id), session)
        return {
            "profile": current.profile,
            "execution_id": current.execution_id,
            "mcp_url": "local-broker",
            "cancelled": current.cancelled,
        }
