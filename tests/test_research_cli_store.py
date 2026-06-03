import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-research"
    loader = importlib.machinery.SourceFileLoader("odysseus_research_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_list_skips_non_object_research_records(tmp_path, monkeypatch):
    cli = _load_cli()
    cli._DATA_DIR = tmp_path
    (tmp_path / "good.json").write_text(json.dumps({"query": "hello", "status": "complete"}))
    (tmp_path / "list.json").write_text("[]")
    (tmp_path / "broken.json").write_text("{")

    emitted = []
    monkeypatch.setattr(cli, "emit", lambda value, args: emitted.append(value))

    cli.cmd_list(SimpleNamespace(status=None, limit=50))

    assert emitted == [[{
        "id": "good",
        "query": "hello",
        "category": "",
        "status": "complete",
        "started_at": "",
        "completed_at": "",
        "sources": 0,
        "stats": {},
    }]]
