# services/memory/service.py
"""Memory service — persistent memory storage and retrieval."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import os

import logging
import heapq
import json
import time
from datetime import timedelta
import threading

logger = logging.getLogger(__name__)

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

# Default age before memories are moved to cold storage
DEFAULT_GLACIER_AGE_SEC = int(timedelta(days=30).total_seconds())

class MemoryService:
    """
    Memory storage and retrieval service.

    Usage:
        service = MemoryService()
        await service.remember("User prefers dark mode")
        results = await service.recall("preferences")
    """

    # Class-level cache to persist across ephemeral MemoryService() instantiations
    _hot_cache: List[Dict[str, Any]] = []
    _last_disk_mtime: float = 0.0
    # Thread lock to serialize disk I/O and cache updates across the threadpool
    _io_lock = threading.RLock()

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
        """Get frequently used/recent memories, using an mtime-validated memory cache."""
        file_path = self.manager.memory_file

        with self.manager.lock:
            try:
                current_mtime = os.path.getmtime(file_path)
            except OSError:
                current_mtime = 0.0

            if MemoryService._hot_cache and current_mtime <= MemoryService._last_disk_mtime:
                records = MemoryService._hot_cache
            else:
                records = self.manager.load_all()
                if records:
                    MemoryService._hot_cache = records
                    MemoryService._last_disk_mtime = current_mtime

        if not records:
            return []

        def _safe_int(val: Any) -> int:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        def _safe_bool(val: Any) -> bool:
            if isinstance(val, str):
                return val.lower() in ('true', '1', 't', 'y', 'yes')
            return bool(val)

        # O(N log K) heap extraction runs entirely in RAM.
        top_records = heapq.nlargest(
            limit,
            records,
            key=lambda x: (
                _safe_bool(x.get("pinned", False)),
                _safe_int(x.get("uses")),
                _safe_int(x.get("timestamp")),
                str(x.get("id", ""))  # Tie-breaker guarantees it never compares raw dicts
            )
        )
        return [self._to_memory(m) for m in top_records]

    def archive_cold_to_glacier(self, age_threshold_sec: int = DEFAULT_GLACIER_AGE_SEC) -> int:
        """
        Moves older records to memory_glacier.jsonl atomically.
        Thread-safe to prevent read-modify-write data loss and cache races.
        """
        with self._io_lock:
            all_memories = self.manager.load_all()
            if not all_memories:
                return 0

            current_time = int(time.time())
            hot_memories, cold_memories = [], []

            for m in all_memories:
                ts = m.get("timestamp", 0)
                if not isinstance(ts, (int, float)):
                    ts = 0
                age = current_time - ts

                if not m.get("pinned", False) and m.get("uses", 0) == 0 and age > age_threshold_sec:
                    cold_memories.append(m)
                else:
                    hot_memories.append(m)

            if not cold_memories:
                return 0

            # 1. Append to colder O(1) memory storage using a single batched I/O write
            glacier_path = os.path.join(os.path.dirname(self.manager.memory_file), "memory_glacier.jsonl")
            try:
                with open(glacier_path, "a", encoding="utf-8") as f:
                    f.writelines(json.dumps(cold_mem) + "\n" for cold_mem in cold_memories)
            except IOError as e:
                logger.error(f"Glacier append failed, aborting archive: {e}")
                return 0

            # 2. Evict dead weight from the vector DB BEFORE committing to JSON.
            # This prevents ghost vectors if the network call fails or times out.
            if self.vector_store and self.vector_store.healthy:
                cold_ids = [m["id"] for m in cold_memories if "id" in m]
                try:
                    if hasattr(self.vector_store, 'remove_batch'):
                        self.vector_store.remove_batch(cold_ids)
                    else:
                        for cid in cold_ids:
                            self.vector_store.remove(cid)
                except Exception as e:
                    logger.error(f"Vector store eviction failed, aborting disk save to prevent split-brain: {e}", exc_info=True)
                    return 0

            # 3. Commit the truncated hot memory array back to disk safely inside the lock.
            try:
                self.manager.save(hot_memories)
            except Exception as e:
                logger.critical(f"Failed to save hot memories! Duplication risk! {e}")

            return len(cold_memories)

    def delete(self, memory_id: str) -> bool:
       """Delete a memory by ID."""
       with self._io_lock:
            memories = self.manager.load_all()
            remaining = [m for m in memories if m.get("id") != memory_id]
            if len(remaining) == len(memories):
                return False

            # Vector DB deletion first to prevent ghost vectors
            if self.vector_store and self.vector_store.healthy:
                try:
                    self.vector_store.remove(memory_id)
                except Exception as e:
                    logger.error(f"Failed to delete {memory_id} from vector DB. Aborting JSON delete. {e}")
                    return False

            self.manager.save(remaining)
            return True
