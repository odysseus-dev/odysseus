"""Regression: a present-but-unhealthy MemoryVectorStore must survive initialization.

When MemoryVectorStore._initialize() fails (ChromaDB unavailable / embeddings not
installed) it swallows the exception and leaves `.healthy == False` — the object
exists but is unhealthy. app_initializer.initialize_managers() previously reset that
object to ``None`` in the ``else`` branch, so service_health.chromadb_health() saw
``memory_vector is None`` and reported the vector memory as DISABLED ("not
configured") instead of DEGRADED/DOWN ("initialization failed") — losing the
diagnostic distinction the /api/diagnostics/services probe is built to surface.

This test fails before the fix (memory_vector is None) and passes after it.
"""
from unittest.mock import MagicMock

import src.app_initializer as app_init
import src.memory_vector as memory_vector_mod
import src.service_health as sh


class _UnhealthyVectorStore:
    """Stand-in for a MemoryVectorStore whose init failed: present but inert."""
    healthy = False

    def count(self):
        return 0

    def search(self, *a, **k):
        return []


def _neutralize_collaborators(monkeypatch):
    """Stub out everything initialize_managers() builds except the vector store,
    so the test isolates the memory_vector health-handling branch."""
    for name in [
        "MemoryManager", "SkillsManager", "SessionManager", "UploadHandler",
        "PersonalDocsManager", "APIKeyManager", "PresetManager",
        "MemoryProviderRegistry", "NativeMemoryProvider", "ChatProcessor",
        "ResearchHandler", "ChatHandler", "ModelDiscovery",
    ]:
        monkeypatch.setattr(app_init, name, lambda *a, **k: MagicMock())
    monkeypatch.setattr(app_init, "set_session_manager", lambda *a, **k: None)
    monkeypatch.setattr(app_init, "update_search_config", lambda *a, **k: None)
    monkeypatch.setattr(app_init, "create_directories", lambda: None)


def test_failed_memory_vector_init_is_kept_not_discarded(monkeypatch, tmp_path):
    _neutralize_collaborators(monkeypatch)
    # initialize_managers does `from src.memory_vector import MemoryVectorStore`
    # at call time, so patch it on the source module.
    monkeypatch.setattr(
        memory_vector_mod, "MemoryVectorStore",
        lambda *a, **k: _UnhealthyVectorStore(),
    )

    result = app_init.initialize_managers(str(tmp_path), rag_manager=None)

    mv = result["memory_vector"]
    assert mv is not None, "unhealthy MemoryVectorStore was discarded (reported as DISABLED, not DEGRADED/DOWN)"
    assert mv.healthy is False


def test_chromadb_health_reports_down_for_unhealthy_vector_store():
    # Pins the downstream taxonomy the fix feeds: a present-but-unhealthy vector
    # store (rag absent) is DOWN, not DISABLED; with a healthy rag it is DEGRADED;
    # only when both are absent is it DISABLED.
    store = _UnhealthyVectorStore()
    healthy_rag = MagicMock(healthy=True)

    assert sh.chromadb_health(None, None)["status"] == sh.DISABLED
    assert sh.chromadb_health(None, store)["status"] == sh.DOWN
    assert sh.chromadb_health(healthy_rag, store)["status"] == sh.DEGRADED
