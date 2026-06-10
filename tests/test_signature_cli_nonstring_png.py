import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_cli(monkeypatch):
    sa = ModuleType("sqlalchemy"); sa.text = lambda q: q
    core = ModuleType("core"); core.__path__ = []
    db_mod = ModuleType("core.database"); db_mod.engine = object()
    monkeypatch.setitem(sys.modules, "sqlalchemy", sa)
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.database", db_mod)
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-signature"
    loader = importlib.machinery.SourceFileLoader("odysseus_signature_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_decode_png_data_handles_non_string(monkeypatch):
    # data_png arrives from a JSON payload; a non-string made `"," in raw` raise
    # TypeError. It should fall through to the normal "not a PNG" fail() path.
    cli = _load_cli(monkeypatch)
    with pytest.raises(SystemExit):
        cli._decode_png_data(123)
