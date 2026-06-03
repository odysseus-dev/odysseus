"""`odysseus-research list --status` must offer the value the writer stores.

Completed research runs are persisted with status "done" (research_handler).
The CLI offered choices ["complete", "running", "cancelled", "error"] and
filtered `status != args.status`, so `--status complete` never matched any
record and there was no way to list completed runs at all. The choice must be
"done".
"""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-research"
    loader = importlib.machinery.SourceFileLoader("odysseus_research_cli_status", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_done_is_a_valid_status_choice():
    cli = _load_cli()
    parser = cli._build_parser()
    ns = parser.parse_args(["list", "--status", "done"])
    assert ns.status == "done"


def test_complete_is_no_longer_offered():
    cli = _load_cli()
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["list", "--status", "complete"])


def test_filter_returns_completed_runs(tmp_path, monkeypatch):
    cli = _load_cli(); cli._DATA_DIR = tmp_path
    (tmp_path / "r1.json").write_text(json.dumps({"query": "q1", "status": "done"}))
    (tmp_path / "r2.json").write_text(json.dumps({"query": "q2", "status": "running"}))
    emitted = []
    monkeypatch.setattr(cli, "emit", lambda value, args: emitted.append(value))
    cli.cmd_list(SimpleNamespace(status="done", limit=50))
    ids = [r["id"] for r in emitted[0]]
    assert ids == ["r1"]  # only the completed run
