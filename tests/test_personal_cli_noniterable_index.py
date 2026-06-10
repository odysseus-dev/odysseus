import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def _load_cli(monkeypatch):
    svc = ModuleType("src.personal_docs")
    svc.PersonalDocsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "src.personal_docs", svc)
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-personal"
    loader = importlib.machinery.SourceFileLoader("odysseus_personal_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_file_rows_handles_non_iterable_index(monkeypatch):
    # PersonalDocsManager.index is loaded from a JSON file; a corrupt file (e.g.
    # a bare number) yields a non-iterable, and `for f in files` raised
    # TypeError before the isinstance filter could run.
    cli = _load_cli(monkeypatch)
    assert cli._file_rows(42) == []
    assert cli._file_rows([{"name": "x"}, "junk"]) == [{"name": "x"}]
