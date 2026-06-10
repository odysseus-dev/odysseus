import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


def _load_cli(monkeypatch, doc):
    core = ModuleType("core"); core.__path__ = []
    db_mod = ModuleType("core.database")
    fake_db = MagicMock()
    fake_db.get.return_value = doc
    db_mod.SessionLocal = lambda: fake_db
    db_mod.Document = object
    db_mod.DocumentVersion = object
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.database", db_mod)
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-docs"
    loader = importlib.machinery.SourceFileLoader("odysseus_docs_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_cmd_export_raw_handles_non_string_content(monkeypatch, capsys):
    # current_content is a DB text column; a non-string value (corruption /
    # raw SQL) made `sys.stdout.write(content)` and `content.endswith` raise.
    doc = SimpleNamespace(id="d1", title="T", current_content=123, version_count=1)
    cli = _load_cli(monkeypatch, doc)
    cli.cmd_export(SimpleNamespace(id="d1", version=None, raw=True, pretty=False))
    assert capsys.readouterr().out == "\n"
