import sys
import types
from unittest.mock import MagicMock

from tests.helpers.cli_loader import load_script


def _load_cli(monkeypatch):
    svc = types.ModuleType("services.memory.memory")
    svc.MemoryManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.memory", svc)
    return load_script("odysseus-memory")


def test_memory_entries_skips_invalid_rows(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._memory_entries([
        {"id": "m1", "text": "ok"},
        "bad-row",
        None,
    ]) == [{"id": "m1", "text": "ok"}]
