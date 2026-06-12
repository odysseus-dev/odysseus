import json
import os
import time

from services.memory.service import MemoryService


def test_get_all_prioritizes_hot_memories_via_heap(tmp_path):
    svc = MemoryService(str(tmp_path))
    now = int(time.time())

    # Bypass the provider to directly seed the underlying JSON
    svc.manager.save([
        {"id": "old_low_use", "text": "A", "pinned": False, "uses": 1, "timestamp": now - 5000},
        {"id": "high_use", "text": "B", "pinned": False, "uses": 50, "timestamp": now - 1000},
        {"id": "pinned_but_old", "text": "C", "pinned": True, "uses": 0, "timestamp": now - 10000},
        {"id": "recent_low_use", "text": "D", "pinned": False, "uses": 1, "timestamp": now - 100},
        {"id": "zero_use", "text": "E", "pinned": False, "uses": 0, "timestamp": now - 500},
    ])

    hot = svc.get_all(limit=3)

    assert len(hot) == 3
    # Expected LFU/Recency order: Pinned -> High Use -> Recent Low Use
    expected_order = ["pinned_but_old", "high_use", "recent_low_use"]
    assert [m.id for m in hot] == expected_order


def test_archive_cold_to_glacier_flushes_and_evicts(tmp_path):
    svc = MemoryService(str(tmp_path))
    
    # Stub the vector store to track evictions without spinning up ChromaDB
    class _TrackingVectorStore:
        healthy = True
        def __init__(self):
            self.removed = []
        def remove(self, mid):
            self.removed.append(mid)
            
    svc.vector_store = _TrackingVectorStore()
    
    now = int(time.time())
    thirty_days = 2592000

    svc.manager.save([
        {"id": "hot_used", "text": "A", "pinned": False, "uses": 5, "timestamp": now - 100},
        {"id": "hot_pinned", "text": "B", "pinned": True, "uses": 0, "timestamp": now - (thirty_days * 2)},
        {"id": "hot_recent", "text": "C", "pinned": False, "uses": 0, "timestamp": now - 100},
        {"id": "cold_1", "text": "D", "pinned": False, "uses": 0, "timestamp": now - (thirty_days + 100)},
        {"id": "cold_2", "text": "E", "pinned": False, "uses": 0, "timestamp": now - (thirty_days * 3)},
    ])

    archived_count = svc.archive_cold_to_glacier(age_threshold_sec=thirty_days)

    assert archived_count == 2

    # 1. Verify Hot Array Truncation
    remaining = svc.manager.load_all()
    assert len(remaining) == 3
    assert {m["id"] for m in remaining} == {"hot_used", "hot_pinned", "hot_recent"}

    # 2. Verify Vector Store Eviction
    assert set(svc.vector_store.removed) == {"cold_1", "cold_2"}

    # 3. Verify JSONL Append
    glacier_path = os.path.join(str(tmp_path), "memory_glacier.jsonl")
    assert os.path.exists(glacier_path)
    with open(glacier_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
        assert {m["id"] for m in lines} == {"cold_1", "cold_2"}
