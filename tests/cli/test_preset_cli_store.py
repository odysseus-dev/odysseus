import pytest
from types import SimpleNamespace

from tests.helpers.cli_loader import load_script


def test_load_rejects_non_object_preset_store(tmp_path, capsys):
    cli = load_script("odysseus-preset")
    cli._PATH = tmp_path / "presets.json"
    cli._PATH.write_text("[]")

    with pytest.raises(SystemExit):
        cli._load()

    assert "expected an object" in capsys.readouterr().err


def test_list_ignores_non_string_prompt_length(monkeypatch):
    cli = load_script("odysseus-preset")
    seen = []
    monkeypatch.setattr(cli, "_load", lambda: {"bad": {"system_prompt": ["nope"]}})
    monkeypatch.setattr(cli, "emit", lambda payload, args: seen.append(payload))

    cli.cmd_list(SimpleNamespace(pretty=False))

    assert seen == [[{
        "id": "bad",
        "name": "bad",
        "temperature": None,
        "prompt_length": 0,
    }]]
