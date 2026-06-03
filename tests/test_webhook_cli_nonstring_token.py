import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock


def _load_cli(monkeypatch):
    core = ModuleType("core"); core.__path__ = []
    db_mod = ModuleType("core.database")
    db_mod.SessionLocal = MagicMock()
    db_mod.ScheduledTask = object
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.database", db_mod)
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-webhook"
    loader = importlib.machinery.SourceFileLoader("odysseus_webhook_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_mask_token_handles_non_string(monkeypatch):
    # webhook_token is a DB column; a non-string value made len(token) raise.
    cli = _load_cli(monkeypatch)
    assert cli._mask_token(123456789012) == ""


def test_mask_token_still_masks_real_token(monkeypatch):
    cli = _load_cli(monkeypatch)
    assert cli._mask_token("abcdefghijklmnop") == "abcdef…mnop"
