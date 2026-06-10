"""cmd_add (scripts/odysseus-memory) must tolerate a non-dict row in the
existing store. Every other command funnels load_all() through
`_memory_entries()` (which drops non-dicts), but cmd_add iterated the raw
list in its dedup check: `any(e.get("id") == ... for e in all_entries)`
crashed with AttributeError on a corrupt/hand-edited memory.json row that
is not a dict. The isinstance check short-circuits before `.get`.
"""
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    svc = types.ModuleType("services.memory.memory")
    svc.MemoryManager = MagicMock()
    monkeypatch.setitem(sys.modules, "services.memory.memory", svc)
    path = ROOT / "scripts" / "odysseus-memory"
    loader = importlib.machinery.SourceFileLoader("odysseus_memory_cli_add", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_cmd_add_tolerates_non_dict_existing_row(monkeypatch):
    cli = _load_cli(monkeypatch)
    cli._mgr = MagicMock()
    cli._mgr.add_entry.return_value = {"id": "m2", "text": "new"}
    cli._mgr.load_all.return_value = [
        {"id": "m1", "text": "existing"},
        "corrupt-row",
        None,
    ]
    emitted = []
    monkeypatch.setattr(cli, "emit", lambda value, args: emitted.append(value))

    cli.cmd_add(SimpleNamespace(text="new", category="fact", owner=None))

    assert emitted == [{"id": "m2", "text": "new"}]
    cli._mgr.save.assert_called_once()
