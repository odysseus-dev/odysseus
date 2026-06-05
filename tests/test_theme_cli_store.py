import pytest

from tests.helpers.cli_loader import load_script


@pytest.mark.parametrize("payload", ["[]", '{"_users": []}'])
def test_load_prefs_rejects_non_object_user_store(tmp_path, capsys, payload):
    cli = load_script("odysseus-theme")
    cli._USER_PREFS_PATH = tmp_path / "user_prefs.json"
    cli._USER_PREFS_PATH.write_text(payload)

    with pytest.raises(SystemExit):
        cli._load_prefs()

    assert "is corrupt" in capsys.readouterr().err
