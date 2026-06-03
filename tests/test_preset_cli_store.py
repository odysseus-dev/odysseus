import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-preset"
    loader = importlib.machinery.SourceFileLoader("odysseus_preset_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_load_rejects_non_object_preset_store(tmp_path, capsys):
    cli = _load_cli()
    cli._PATH = tmp_path / "presets.json"
    cli._PATH.write_text("[]")

    with pytest.raises(SystemExit):
        cli._load()

    assert "expected an object" in capsys.readouterr().err
