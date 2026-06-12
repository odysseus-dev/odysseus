# services/memory/service.py
"""Memory service — persistent memory storage and retrieval."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import os

import heapq
import json
import time

from .memory import MemoryManager
from .memory_vector import MemoryVectorStore
from src.memory_provider import MemoryRecord, NativeMemoryProvider
from src.constants import DATA_DIR


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

    def __init__(self, data_dir: str = DATA_DIR):
        self.manager = MemoryManager(data_dir)
        self.vector_store = MemoryVectorStore(data_dir) if os.path.exists(
            os.path.join(data_dir, "memory_vectors")
        ) else None
        self.provider = NativeMemoryProvider(self.manager, self.vector_store)

    def _sync_provider(self) -> None:
        self.provider.memory_vector = self.vector_store

    @staticmethod
    def _to_memory(entry: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> Memory:
        return Memory(
            id=entry.get("id", ""),
            text=entry.get("text", ""),
            timestamp=entry.get("timestamp", 0),
            session_id=entry.get("session_id"),
            metadata=metadata or {},
        )

    @staticmethod
    def _record_to_memory(record: MemoryRecord, metadata: Optional[Dict[str, Any]] = None) -> Memory:
        merged_metadata = dict(record.metadata)
        if metadata:
            merged_metadata.update(metadata)
        return Memory(
            id=record.id,
            text=record.text,
            timestamp=record.timestamp,
            session_id=record.session_id,
            metadata=merged_metadata,
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
        self._sync_provider()
        record = await self.provider.remember(text, session_id=session_id)
        return self._record_to_memory(record)

    async def recall(self, query: str, top_k: int = 5) -> MemorySearchResult:
        """
        Search memories.

        Args:
            query: Search query
            top_k: Max results

        Returns:
            MemorySearchResult with matching memories
        """
        self._sync_provider()
        results = await self.provider.recall(query, top_k=top_k)
        memories = [
            self._record_to_memory(hit.memory, metadata={"score": hit.score})
            if hit.score is not None
            else self._record_to_memory(hit.memory)
            for hit in results
        ]
        return MemorySearchResult(memories=memories, query=query, total=len(memories))

    def get_all(self, limit: int = 100) -> List[Memory]:
        """Get frequently used/recent memories, keeping context window lean."""
        records = self.manager.load_all()

        # O(N log K) heap extraction to prioritize relevant context natively
        top_records = heapq.nlargest(
            limit,
            records,
            key=lambda x: (
                x.get("pinned", False),
                x.get("uses", 0),
                x.get("timestamp", 0)
            )
        )
        return [self._to_memory(m) for m in top_records]

    def archive_cold_to_glacier(self, age_threshold_sec: int = 2592000) -> int:
        """
        move older stuff to data/memory_glacier.jsonl, freeing up hot
        json and ram.
        """

        all_memories = self.manager.load_all()
        if not all_memories:
            return 0

        current_time = int(time.time())
        hot_memories, cold_memories = [], []

        for m in all_memories:
            age = current_time - m.get("timestamp", 0)
            if not m.get("pinned", False) and m.get("uses", 0) == 0 and age > age_threshold_sec:
                cold_memories.append(m)
            else:
                hot_memories.append(m)

        if not cold_memories:
            return 0

        # append to colder O(1) memory storage
        glacier_path = os.path.join(os.path.dirname(self.manager.memory_file), "memory_glacier.jsonl")
        with open(glacier_path, "a", encoding="utf-8") as f:
            for cold_mem in cold_memories:
                f.write(json.dumps(cold_mem) + "\n")

        # commit the truncated hot memory array back to disk
        self.manager.save(hot_memories)

        # evict dead weight from the vector store to keep semantic search fast
        if self.vector_store and self.vector_store.healthy:
            for cold_mem in cold_memories:
                try:
                    self.vector_store.remove(cold_mem["id"])
                except Exception:
                    pass

        return len(cold_memories)

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
