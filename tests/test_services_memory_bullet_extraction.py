"""services/memory extract_memory_from_chat must handle bullet lines.

The regex r\'^[-*\u2022]|\\d+\\.\\s*(.*)\' parses as (^[-*\u2022]) OR (\\d+\\.\\s*(.*)):
the capturing group only exists in the numbered-list branch, so a plain
bullet line matches with group(1) is None and .strip() raises
AttributeError, aborting extraction for the whole message. src/memory.py
was already fixed to r\'^(?:[-*\u2022]|\\d+\\.)\\s*(.*)\' but the services copy
diverged, and routes/memory_routes.py imports the services copy.
"""

import importlib.util
import sys
import types

import pytest


@pytest.fixture
def memory_manager(tmp_path, monkeypatch):
    """Load services.memory.memory from its file path so the heavy
    services/__init__.py is never imported."""
    pkg = types.ModuleType("services")
    pkg.__path__ = []
    sub = types.ModuleType("services.memory")
    sub.__path__ = []
    monkeypatch.setitem(sys.modules, "services", pkg)
    monkeypatch.setitem(sys.modules, "services.memory", sub)
    name = "services.memory.memory"
    monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location(name, "services/memory/memory.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod.MemoryManager(str(tmp_path))


def test_bullet_lines_are_extracted_not_crashed(memory_manager):
    out = memory_manager.extract_memory_from_chat(
        [
            {
                "role": "assistant",
                "content": "- User likes coffee\n* Prefers tea\n\u2022 Lives in Porto",
            }
        ]
    )
    texts = [m["text"] for m in out]
    assert "User likes coffee" in texts
    assert "Prefers tea" in texts
    assert "Lives in Porto" in texts


def test_numbered_lines_still_extracted(memory_manager):
    out = memory_manager.extract_memory_from_chat(
        [{"role": "assistant", "content": "1. Wakes at 6am\n2. Works remotely"}]
    )
    texts = [m["text"] for m in out]
    assert "Wakes at 6am" in texts
    assert "Works remotely" in texts
