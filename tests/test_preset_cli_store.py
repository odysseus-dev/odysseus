import pytest

from tests.helpers.cli_loader import load_script


def test_load_rejects_non_object_preset_store(tmp_path, capsys):
    cli = load_script("odysseus-preset")
    cli._PATH = tmp_path / "presets.json"
    cli._PATH.write_text("[]")

    with pytest.raises(SystemExit):
        cli._load()

    assert "expected an object" in capsys.readouterr().err
