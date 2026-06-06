from unittest.mock import MagicMock

import routes.memory_routes as memory_routes
from src.memory import MemoryManager


def test_memory_search_returns_only_callers_memories(monkeypatch, tmp_path):
    manager = MemoryManager(str(tmp_path))
    alice_memory = manager.add_entry("Project codename is Odyssey", owner="alice")
    bob_memory = manager.add_entry("Project codename is Odyssey", owner="bob")
    manager.save([alice_memory, bob_memory])

    monkeypatch.setattr(memory_routes, "get_current_user", lambda request: "bob")
    router = memory_routes.setup_memory_routes(manager, MagicMock())
    search = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/memory/search" and "POST" in route.methods
    )

    result = search(
        request=None,
        query="Project codename is Odyssey",
        session_id=None,
        category=None,
    )

    assert [memory["id"] for memory in result["memories"]] == [bob_memory["id"]]
