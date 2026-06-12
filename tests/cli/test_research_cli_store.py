import json
from types import SimpleNamespace

from tests.helpers.cli_loader import load_script


def _load_cli():
    return load_script("odysseus-research")


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
