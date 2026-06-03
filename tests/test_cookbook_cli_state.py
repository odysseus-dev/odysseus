import importlib.machinery
import importlib.util
import io
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-cookbook"
    loader = importlib.machinery.SourceFileLoader("odysseus_cookbook_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_state_set_rejects_non_object_json(tmp_path, monkeypatch, capsys):
    cli = _load_cli()
    cli._STATE_PATH = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("[]"))

    with pytest.raises(SystemExit):
        cli.cmd_state_set(type("Args", (), {})())

    assert "expected a JSON object" in capsys.readouterr().err
    assert not cli._STATE_PATH.exists()
