# services/memory/service.py
"""Memory service — persistent memory storage and retrieval."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import os

from .memory import MemoryManager
from .memory_vector import MemoryVectorStore


@dataclass
class Memory:
    """A stored memory."""
    id: str
    text: str
    timestamp: int
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemorySearchResult:
    """Result of memory search."""
    memories: List[Memory]
    query: str
    total: int


class MemoryService:
    """
    Memory storage and retrieval service.

    Usage:
        service = MemoryService()
        await service.remember("User prefers dark mode")
        results = await service.recall("preferences")
    """

    def __init__(self, data_dir: str = "data"):
        self.manager = MemoryManager(data_dir)
        self.vector_store = MemoryVectorStore(data_dir) if os.path.exists(
            os.path.join(data_dir, "memory_vectors")
        ) else None

    async def remember(self, text: str, session_id: Optional[str] = None) -> Memory:
        """
        Store a new memory.

        Args:
            text: Memory content
            session_id: Optional session association

        Returns:
            Created Memory object
        """
        import uuid
        import time

        memory_id = str(uuid.uuid4())[:8]
        timestamp = int(time.time())

        entry = {
            "id": memory_id,
            "text": text,
            "timestamp": timestamp,
            "session_id": session_id,
        }

        self.manager.add_memory(entry)

        # Also add to vector store if available
        if self.vector_store:
            self.vector_store.add(text, {"id": memory_id, "session_id": session_id})

        return Memory(
            id=memory_id,
            text=text,
            timestamp=timestamp,
            session_id=session_id,
        )

    async def recall(self, query: str, top_k: int = 5) -> MemorySearchResult:
        """
        Search memories.

        Args:
            query: Search query
            top_k: Max results

        Returns:
            MemorySearchResult with matching memories
        """
        # Try vector search first
        if self.vector_store:
            results = self.vector_store.search(query, k=top_k)
            memories = [
                Memory(
                    id=r.get("id", ""),
                    text=r.get("text", ""),
                    timestamp=r.get("timestamp", 0),
                    session_id=r.get("session_id"),
                    metadata=r.get("metadata", {}),
                )
                for r in results
                if isinstance(r, dict)
            ]
            return MemorySearchResult(memories=memories, query=query, total=len(memories))

        # Fallback to keyword search
        results = self.manager.search_memories(query, limit=top_k)
        memories = [
            Memory(
                id=m.get("id", ""),
                text=m.get("text", ""),
                timestamp=m.get("timestamp", 0),
                session_id=m.get("session_id"),
            )
            for m in results
        ]
        return MemorySearchResult(memories=memories, query=query, total=len(memories))

    def get_all(self, limit: int = 100) -> List[Memory]:
        """Get all memories."""
        memories = self.manager.get_memories(limit=limit)
        return [
            Memory(
                id=m.get("id", ""),
                text=m.get("text", ""),
                timestamp=m.get("timestamp", 0),
                session_id=m.get("session_id"),
            )
            for m in memories
        ]

    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        return self.manager.delete_memory(memory_id)
