# tests/test_memory_service_injection.py
from services.memory.service import MemoryService


class _FakeStore:
    healthy = True


def test_memory_service_accepts_injected_vector_store(tmp_path):
    """Projects inject a per-project MemoryVectorStore; the service must
    use it instead of constructing its own."""
    sentinel = _FakeStore()
    svc = MemoryService(data_dir=str(tmp_path), vector_store=sentinel)
    assert svc.vector_store is sentinel
