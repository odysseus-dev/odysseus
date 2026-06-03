"""odysseus-research list/search must not crash sorting on a missing started_at.

The records are sorted by `r.get("started_at") or ""`. Normal records store a
float epoch; a record missing started_at yields "" (str). In Python 3 sorting
a mix of float and str raises TypeError, crashing the whole list/search
command instead of just ordering the records. The key must be type-stable.
"""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-research"
    loader = importlib.machinery.SourceFileLoader("odysseus_research_cli_sort", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _seed(d):
    (d / "a.json").write_text(json.dumps({"query": "has-time", "started_at": 1000.0}))
    (d / "b.json").write_text(json.dumps({"query": "no-time"}))  # _summarize -> started_at ""


def test_list_does_not_crash_on_missing_started_at(tmp_path, monkeypatch):
    cli = _load_cli(); cli._DATA_DIR = tmp_path
    _seed(tmp_path)
    emitted = []
    monkeypatch.setattr(cli, "emit", lambda value, args: emitted.append(value))
    cli.cmd_list(SimpleNamespace(status=None, limit=50))  # must not raise
    assert len(emitted[0]) == 2
    # the record with a real timestamp sorts first (desc)
    assert emitted[0][0]["query"] == "has-time"


def test_search_does_not_crash_on_missing_started_at(tmp_path, monkeypatch):
    cli = _load_cli(); cli._DATA_DIR = tmp_path
    _seed(tmp_path)
    emitted = []
    monkeypatch.setattr(cli, "emit", lambda value, args: emitted.append(value))
    cli.cmd_search(SimpleNamespace(query="time", limit=50))  # must not raise
    assert isinstance(emitted[0], list)
