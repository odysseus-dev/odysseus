import io

import pytest

from tests.helpers.cli_loader import load_script


def test_state_set_rejects_non_object_json(tmp_path, monkeypatch, capsys):
    cli = load_script("odysseus-cookbook")
    cli._STATE_PATH = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("[]"))

    with pytest.raises(SystemExit):
        cli.cmd_state_set(type("Args", (), {})())

    assert "expected a JSON object" in capsys.readouterr().err
    assert not cli._STATE_PATH.exists()
