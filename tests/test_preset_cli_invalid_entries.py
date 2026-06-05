from tests.helpers.cli_loader import load_script


def test_entry_or_fail_rejects_non_object_entries():
    cli = load_script("odysseus-preset")

    try:
        cli._entry_or_fail({"broken": "raw prompt"}, "broken")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected invalid preset entry to exit")


def test_entry_or_fail_returns_valid_entry():
    cli = load_script("odysseus-preset")

    assert cli._entry_or_fail({"ok": {"name": "ok"}}, "ok") == {"name": "ok"}
