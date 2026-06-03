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
    db.ScheduledTask = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    path = ROOT / "scripts" / "odysseus-webhook"
    loader = importlib.machinery.SourceFileLoader("odysseus_webhook_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_mask_token_handles_short_values(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._mask_token("") == ""
    assert cli._mask_token("short") == "***"
    assert cli._mask_token("abcdef1234567890") == "abcdef…7890"
    assert cli._mask_token("short", reveal=True) == "short"
