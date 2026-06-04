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

    @staticmethod
    def _to_memory(entry: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Memory:
        return Memory(
            id=entry.get("id", ""),
            text=entry.get("text", ""),
            timestamp=entry.get("timestamp", 0),
            session_id=entry.get("session_id"),
            metadata=metadata or {},
        )

    async def remember(self, text: str, session_id: Optional[str] = None) -> Memory:
        """
        Store a new memory.

        Args:
            text: Memory content
            session_id: Optional session association

        Returns:
            Created Memory object
        """
        entry = self.manager.add_entry(text)
        if session_id:
            entry["session_id"] = session_id

        memories = self.manager.load_all()
        memories.append(entry)
        self.manager.save(memories)

        # Also add to vector store if available
        if self.vector_store and self.vector_store.healthy:
            self.vector_store.add(entry["id"], entry["text"])

        return self._to_memory(entry)

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
        all_memories = self.manager.load_all()
        by_id = {m.get("id"): m for m in all_memories}
        if self.vector_store and self.vector_store.healthy:
            results = self.vector_store.search(query, k=top_k)
            found = []
            for result in results:
                entry = by_id.get(result.get("memory_id"))
                if entry:
                    found.append(self._to_memory(entry, metadata={"score": result.get("score")}))
            if found:
                return MemorySearchResult(memories=found, query=query, total=len(found))

        # Fallback to keyword search
        results = self.manager.get_relevant_memories(query, all_memories, max_items=top_k)
        memories = [self._to_memory(m) for m in results]
        return MemorySearchResult(memories=memories, query=query, total=len(memories))

    def get_all(self, limit: int = 100) -> List[Memory]:
        """Get all memories."""
        memories = self.manager.load_all()[:limit]
        return [self._to_memory(m) for m in memories]

    def delete(self, memory_id: str) -> bool:
        """Delete a memory by ID."""
        memories = self.manager.load_all()
        remaining = [m for m in memories if m.get("id") != memory_id]
        if len(remaining) == len(memories):
            return False

        self.manager.save(remaining)
        if self.vector_store and self.vector_store.healthy:
            self.vector_store.remove(memory_id)
        return True
