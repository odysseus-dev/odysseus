import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    personal_docs = types.ModuleType("src.personal_docs")
    personal_docs.PersonalDocsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "src.personal_docs", personal_docs)
    path = ROOT / "scripts" / "odysseus-personal"
    loader = importlib.machinery.SourceFileLoader("odysseus_personal_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_file_rows_skips_invalid_rows(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._file_rows([
        {"name": "notes.txt", "path": "/tmp/notes.txt"},
        "bad-row",
        None,
    ]) == [{"name": "notes.txt", "path": "/tmp/notes.txt"}]
