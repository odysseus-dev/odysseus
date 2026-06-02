"""Regression: logs CLI _resolve must tolerate a non-string name.

`_resolve` did `name in p.name` and `p.name == name`; a non-string `name`
(e.g. None) raised TypeError once any *.log file existed. Non-strings now
return None (no match).
"""
import importlib.machinery
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    loader = importlib.machinery.SourceFileLoader("odysseus_logs_cli", str(ROOT / "scripts" / "odysseus-logs"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_non_string_name_returns_none():
    cli = _load()
    assert cli._resolve(None) is None
    assert cli._resolve(123) is None
