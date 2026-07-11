"""
swarm_types.py

Pure data types for the Odysseus Swarm Intelligence Framework.
No dependencies on Odysseus internals — these are portable, serialisable
value objects used across the entire swarm subsystem.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Role definition
# ---------------------------------------------------------------------------

@dataclass
class SwarmRole:
    """A single role within a swarm (master or worker)."""

    name: str                                   # Human-readable, e.g. "Senior Backend Engineer"
    slug: str                                   # Machine key, e.g. "backend_engineer"
    system_prompt: str                          # Persona system-prompt injected per-call
    tools_allowed: List[str] = field(default_factory=lambda: ["all"])
    tools_denied: List[str] = field(default_factory=list)
    model: Optional[str] = None                 # Override session model; None = inherit
    endpoint_url: Optional[str] = None          # Override session endpoint; None = inherit
    priority: int = 0                           # Lower = scheduled earlier
    description: str = ""                       # Short blurb for master's role catalogue

    def effective_tools(self, available: set[str]) -> set[str]:
        """Compute the resolved tool set given the session's available tools."""
        if "all" in self.tools_allowed:
            allowed = set(available)
        else:
            allowed = set(self.tools_allowed) & available
        return allowed - set(self.tools_denied)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "system_prompt": self.system_prompt,
            "tools_allowed": self.tools_allowed,
            "tools_denied": self.tools_denied,
            "model": self.model,
            "endpoint_url": self.endpoint_url,
            "priority": self.priority,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwarmRole":
        return cls(
            name=data["name"],
            slug=data["slug"],
            system_prompt=data.get("system_prompt", ""),
            tools_allowed=data.get("tools_allowed", ["all"]),
            tools_denied=data.get("tools_denied", []),
            model=data.get("model"),
            endpoint_url=data.get("endpoint_url"),
            priority=data.get("priority", 0),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# Swarm definition
# ---------------------------------------------------------------------------

@dataclass
class SwarmDefinition:
    """Full specification of a swarm — master, workers, routing, memory."""

    id: str
    name: str                                           # "Software Engineering Swarm"
    description: str = ""
    domain: str = "general"                             # "engineering", "research", …
    master: SwarmRole = field(default_factory=lambda: SwarmRole(
        name="Coordinator", slug="coordinator", system_prompt="You are a coordinator.",
    ))
    workers: List[SwarmRole] = field(default_factory=list)
    routing_rules: Dict[str, List[str]] = field(default_factory=dict)
    memory_config: Dict[str, Any] = field(default_factory=lambda: {
        "shared": True,
        "persist_after": True,
    })
    max_parallel: int = 5
    version: str = "1.0.0"

    # -- helpers --

    def worker_by_slug(self, slug: str) -> Optional[SwarmRole]:
        for w in self.workers:
            if w.slug == slug:
                return w
        return None

    def worker_slugs(self) -> List[str]:
        return [w.slug for w in self.workers]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "master": self.master.to_dict(),
            "workers": [w.to_dict() for w in self.workers],
            "routing_rules": self.routing_rules,
            "memory_config": self.memory_config,
            "max_parallel": self.max_parallel,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwarmDefinition":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data["name"],
            description=data.get("description", ""),
            domain=data.get("domain", "general"),
            master=SwarmRole.from_dict(data["master"]),
            workers=[SwarmRole.from_dict(w) for w in data.get("workers", [])],
            routing_rules=data.get("routing_rules", {}),
            memory_config=data.get("memory_config", {"shared": True, "persist_after": True}),
            max_parallel=data.get("max_parallel", 5),
            version=data.get("version", "1.0.0"),
        )


# ---------------------------------------------------------------------------
# Task & execution tracking
# ---------------------------------------------------------------------------

class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class ExecutionStatus:
    PLANNING = "planning"
    EXECUTING = "executing"
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SwarmTask:
    """A single sub-task assigned to a worker by the master."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    role_slug: str = ""
    prompt: str = ""
    dependencies: List[str] = field(default_factory=list)   # Task IDs
    status: str = TaskStatus.PENDING
    result: Optional[str] = None
    skip_reason: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)   # tokens, latency, …
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[int]:
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at) * 1000)
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role_slug": self.role_slug,
            "prompt": self.prompt,
            "dependencies": self.dependencies,
            "status": self.status,
            "result": self.result,
            "skip_reason": self.skip_reason,
            "metrics": self.metrics,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }


@dataclass
class SwarmExecution:
    """Top-level state for a single swarm run."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    swarm_id: str = ""
    swarm_name: str = ""
    session_id: str = ""
    user_query: str = ""
    tasks: List[SwarmTask] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)   # [{slug, reason}]
    master_plan: str = ""
    final_response: Optional[str] = None
    status: str = ExecutionStatus.PLANNING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    total_tokens: int = 0
    owner: Optional[str] = None

    @property
    def workers_activated(self) -> int:
        return len([t for t in self.tasks if t.status != TaskStatus.SKIPPED])

    @property
    def workers_skipped(self) -> int:
        return len(self.skipped)

    @property
    def duration_ms(self) -> Optional[int]:
        if self.completed_at:
            return int((self.completed_at - self.started_at) * 1000)
        return None

    def task_by_id(self, task_id: str) -> Optional[SwarmTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def tasks_ready(self) -> List[SwarmTask]:
        """Return pending tasks whose dependencies are all resolved."""
        done_ids = {t.id for t in self.tasks if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED, TaskStatus.FAILED)}
        return [
            t for t in self.tasks
            if t.status == TaskStatus.PENDING
            and all(dep in done_ids for dep in t.dependencies)
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "swarm_id": self.swarm_id,
            "swarm_name": self.swarm_name,
            "session_id": self.session_id,
            "user_query": self.user_query,
            "tasks": [t.to_dict() for t in self.tasks],
            "skipped": self.skipped,
            "master_plan": self.master_plan,
            "final_response": self.final_response,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_tokens": self.total_tokens,
            "workers_activated": self.workers_activated,
            "workers_skipped": self.workers_skipped,
            "duration_ms": self.duration_ms,
            "owner": self.owner,
        }
