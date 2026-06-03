import importlib.machinery
import importlib.util
from pathlib import Path


def _load_preset_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-preset"
    loader = importlib.machinery.SourceFileLoader("odysseus_preset_invalid_entries", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_entry_or_fail_rejects_non_object_entries():
    cli = _load_preset_cli()

    try:
        cli._entry_or_fail({"broken": "raw prompt"}, "broken")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected invalid preset entry to exit")


def test_entry_or_fail_returns_valid_entry():
    cli = _load_preset_cli()

    assert cli._entry_or_fail({"ok": {"name": "ok"}}, "ok") == {"name": "ok"}
