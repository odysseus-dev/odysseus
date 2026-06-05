import importlib.machinery
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_preset_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-preset"
    loader = importlib.machinery.SourceFileLoader("odysseus_preset_set_corrupt", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
