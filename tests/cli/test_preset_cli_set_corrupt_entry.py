from types import SimpleNamespace

from tests.helpers.cli_loader import load_script


def _load_preset_cli():
    return load_script("odysseus-preset")


def test_set_replaces_corrupt_existing_entry(monkeypatch):
    cli = _load_preset_cli()
    saved = {}
    emitted = {}

    monkeypatch.setattr(cli, "_load", lambda: {"broken": "raw prompt"})
    monkeypatch.setattr(cli, "_save", lambda data: saved.update(data))
    monkeypatch.setattr(cli, "emit", lambda payload, _args: emitted.update(payload))

    args = SimpleNamespace(
        name="broken",
        prompt="new prompt",
        prompt_file=None,
        temperature=0.7,
        display_name=None,
    )

    cli.cmd_set(args)

    assert saved["broken"] == {
        "name": "broken",
        "system_prompt": "new prompt",
        "temperature": 0.7,
    }
    assert emitted["ok"] is True
