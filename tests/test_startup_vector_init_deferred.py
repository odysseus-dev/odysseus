"""Regression coverage for Docker startup not blocking on vector services."""

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def _source(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def test_app_does_not_initialize_rag_at_import_time():
    text = _source("app.py")
    rag_block = _between(text, "# ========= RAG (vector document RAG) =========", "# ========= IMPORT CONFIG =========")

    assert "rag_manager = None" in rag_block
    assert "rag_manager = get_rag_manager()" not in rag_block
    assert "eager_memory_vector=False" in text


def test_startup_warms_vector_services_in_background_thread():
    text = _source("app.py")
    startup = _between(text, "async def _startup_event():", "    # MCP servers can be slow")

    assert "asyncio.to_thread(get_rag_manager)" in startup
    assert "asyncio.to_thread(initialize_memory_vector_store" in startup
    assert "asyncio.create_task(_warmup_vector_services())" in startup


def test_deferred_vector_store_delegates_after_install():
    from src.app_initializer import DeferredVectorStore

    proxy = DeferredVectorStore()
    assert proxy.healthy is False

    class Store:
        healthy = True

        def add(self, memory_id, text):
            return f"{memory_id}:{text}"

    proxy.set_store(Store())

    assert proxy.healthy is True
    assert proxy.add("m1", "hello") == "m1:hello"
