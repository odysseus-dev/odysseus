import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-theme"
    loader = importlib.machinery.SourceFileLoader("odysseus_theme_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize("payload", ["[]", '{"_users": []}'])
def test_load_prefs_rejects_non_object_user_store(tmp_path, capsys, payload):
    cli = _load_cli()
    cli._USER_PREFS_PATH = tmp_path / "user_prefs.json"
    cli._USER_PREFS_PATH.write_text(payload)

    with pytest.raises(SystemExit):
        cli._load_prefs()

    assert "is corrupt" in capsys.readouterr().err
