"""Actor/Subagent system — lifecycle management and registry."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ActorMode(Enum):
    SUBAGENT = "subagent"
    PEER = "peer"


class ActorStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    IDLE = "idle"


class ActorOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass
class Actor:
    id: str
    session_id: str
    mode: ActorMode
    status: ActorStatus = ActorStatus.PENDING
    outcome: Optional[ActorOutcome] = None
    parent_id: Optional[str] = None
    tool_allowlist: Optional[set] = None
    context_mode: str = "none"
    background: bool = False
    error: Optional[str] = None
    turn_count: int = 0
    _waiters: List[asyncio.Future] = field(default_factory=list, repr=False)

    @property
    def is_active(self) -> bool:
        return self.status in (ActorStatus.PENDING, ActorStatus.RUNNING)


class ActorRegistry:
    def __init__(self) -> None:
        self._actors: Dict[str, Actor] = {}
        self._counters: Dict[str, int] = {}

    def register(self, actor: Actor) -> None:
        self._actors[actor.id] = actor
        logger.info(f"Actor registered: {actor.id}")

    def get(self, id: str) -> Optional[Actor]:
        return self._actors.get(id)

    def list_by_session(self, session_id: str) -> List[Actor]:
        return [a for a in self._actors.values() if a.session_id == session_id]

    def list_by_parent(self, parent_id: str) -> List[Actor]:
        return [a for a in self._actors.values() if a.parent_id == parent_id]

    def list_active(self) -> List[Actor]:
        return [a for a in self._actors.values() if a.is_active]

    def allocate_id(self, agent_type: str) -> str:
        count = self._counters.get(agent_type, 0) + 1
        self._counters[agent_type] = count
        return f"{agent_type}-{count}"

    def update_status(self, id: str, status: ActorStatus, outcome: Optional[ActorOutcome] = None, error: Optional[str] = None) -> None:
        actor = self._actors.get(id)
        if not actor:
            return
        actor.status = status
        if outcome:
            actor.outcome = outcome
        if error:
            actor.error = error
        if status == ActorStatus.RUNNING:
            actor.turn_count += 1
        if status == ActorStatus.IDLE:
            for fut in actor._waiters:
                if not fut.done():
                    fut.set_result(actor)
            actor._waiters.clear()
        logger.info(f"Actor {id} status: {status.value}")

    async def wait(self, id: str, timeout: float = 600.0) -> Actor:
        actor = self._actors.get(id)
        if not actor:
            raise ValueError(f"Actor {id} not found")
        if actor.status == ActorStatus.IDLE:
            return actor
        fut = asyncio.get_event_loop().create_future()
        actor._waiters.append(fut)
        return await asyncio.wait_for(fut, timeout=timeout)

    def cancel(self, id: str) -> bool:
        actor = self._actors.get(id)
        if not actor or not actor.is_active:
            return False
        self.update_status(id, ActorStatus.IDLE, outcome=ActorOutcome.CANCELLED)
        return True

    def render_for_agent(self) -> str:
        active = self.list_active()
        if not active:
            return ""
        lines = ["## Active actors"]
        for a in active:
            outcome_str = f" ({a.outcome.value})" if a.outcome else ""
            lines.append(f"- `{a.id}` — {a.mode.value}, {a.status.value}{outcome_str}")
        return "\n".join(lines)
