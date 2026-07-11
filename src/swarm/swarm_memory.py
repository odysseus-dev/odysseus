"""
swarm_memory.py

Shared context layer for intra-swarm communication.

During a single swarm execution, workers contribute their findings to a
shared memory space.  The master agent can read the full accumulated context
when synthesising the final response.

Short-term context lives in-process (dict-backed).  After execution completes,
results are optionally persisted to the existing Odysseus memory system
(SQLite ``memories`` table + ChromaDB vectors) for long-term retrieval.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Contribution:
    """A single worker contribution to the shared memory."""

    role_slug: str
    role_name: str
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None


class SwarmMemory:
    """In-memory shared context for a single swarm execution.

    Thread-safe-ish for asyncio (single-threaded event loop), but NOT
    designed for multi-process sharing.  Each execution gets its own
    instance.

    Usage::

        mem = SwarmMemory(execution_id="abc123")
        mem.contribute("backend_engineer", "Backend Engineer", "Found N+1 query in users.py")
        context = mem.get_context()                        # all contributions
        context = mem.get_context(exclude_role="backend_engineer")  # everyone else
    """

    def __init__(self, execution_id: str):
        self._execution_id = execution_id
        self._contributions: List[Contribution] = []
        self._artifacts: List[Dict[str, Any]] = []
        self._metadata: Dict[str, Any] = {}

    # -- write --

    def contribute(
        self,
        role_slug: str,
        role_name: str,
        content: str,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> None:
        """Add a worker's output to the shared context."""
        if not content or not content.strip():
            return
        self._contributions.append(Contribution(
            role_slug=role_slug,
            role_name=role_name,
            content=content.strip(),
            metadata=metadata or {},
            task_id=task_id,
        ))

    def add_artifact(self, name: str, content: str, artifact_type: str = "text") -> None:
        """Store a shared artifact (code file, data, etc.)."""
        self._artifacts.append({
            "name": name,
            "content": content,
            "type": artifact_type,
            "timestamp": time.time(),
        })

    def set_metadata(self, key: str, value: Any) -> None:
        """Store arbitrary key-value metadata on the execution."""
        self._metadata[key] = value

    # -- read --

    def get_context(self, *, exclude_role: Optional[str] = None, max_chars: int = 0) -> str:
        """Render all contributions as a formatted context string.

        Args:
            exclude_role: Omit contributions from this role (useful so a
                worker doesn't see its own prior output in multi-round).
            max_chars: If > 0, truncate the rendered context to this many chars.
        """
        parts: List[str] = []
        for c in self._contributions:
            if exclude_role and c.role_slug == exclude_role:
                continue
            parts.append(f"### {c.role_name}\n{c.content}")

        text = "\n\n".join(parts)
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "\n\n[...context truncated...]"
        return text

    def get_contributions(self, role_slug: Optional[str] = None) -> List[Contribution]:
        """Return raw contributions, optionally filtered by role."""
        if role_slug:
            return [c for c in self._contributions if c.role_slug == role_slug]
        return list(self._contributions)

    def get_artifacts(self) -> List[Dict[str, Any]]:
        return list(self._artifacts)

    @property
    def contribution_count(self) -> int:
        return len(self._contributions)

    @property
    def total_chars(self) -> int:
        return sum(len(c.content) for c in self._contributions)

    # -- persistence --

    async def persist(self, session_id: str, owner: Optional[str] = None) -> None:
        """Persist the swarm's shared findings to the Odysseus memory system.

        This stores each worker's contribution as a memory entry with the
        source ``swarm`` and category ``swarm_result``, linked to the session.
        The existing ``MemoryManager`` + ``memory_vector`` ChromaDB pipeline
        handles the rest (dedup, embedding, retrieval).
        """
        if not self._contributions:
            return

        try:
            from src.memory import MemoryManager
            from src.constants import DATA_DIR

            mm = MemoryManager(DATA_DIR)
            entries = mm.load_all()

            for c in self._contributions:
                text = f"[{c.role_name}] {c.content}"
                # Truncate very long contributions to avoid blowing up memory.json
                if len(text) > 2000:
                    text = text[:2000] + "..."
                entry = mm.add_entry(
                    text=text,
                    source="swarm",
                    category="swarm_result",
                    owner=owner,
                )
                entry["session_id"] = session_id
                entry["swarm_execution_id"] = self._execution_id
                entries.append(entry)

            mm.save(entries)
            logger.info(
                "Persisted %d swarm contributions to memory (execution=%s)",
                len(self._contributions),
                self._execution_id,
            )
        except Exception:
            logger.warning(
                "Failed to persist swarm memory (execution=%s)",
                self._execution_id,
                exc_info=True,
            )

    # -- serialisation --

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self._execution_id,
            "contributions": [
                {
                    "role_slug": c.role_slug,
                    "role_name": c.role_name,
                    "content": c.content,
                    "timestamp": c.timestamp,
                    "metadata": c.metadata,
                    "task_id": c.task_id,
                }
                for c in self._contributions
            ],
            "artifacts": self._artifacts,
            "metadata": self._metadata,
        }
