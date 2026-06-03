import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.Document = MagicMock()
    db.DocumentVersion = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    path = ROOT / "scripts" / "odysseus-docs"
    loader = importlib.machinery.SourceFileLoader("odysseus_docs_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_text_len_ignores_non_string_values(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._text_len("hello") == 5
    assert cli._text_len(None) == 0
    assert cli._text_len({"bad": "row"}) == 0
