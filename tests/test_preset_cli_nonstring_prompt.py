import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-preset"
    loader = importlib.machinery.SourceFileLoader("odysseus_preset_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_cmd_list_handles_non_string_system_prompt(monkeypatch, capsys):
    # presets.json is user/UI-written; a corrupt entry can have a non-string
    # system_prompt, and the old len(val.get("system_prompt") or "") then did
    # len(123) and crashed the whole listing.
    cli = _load_cli()
    monkeypatch.setattr(cli, "_load", lambda: {"p1": {"name": "P1", "system_prompt": 123}})
    cli.cmd_list(SimpleNamespace(pretty=False))
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["prompt_length"] == 0
