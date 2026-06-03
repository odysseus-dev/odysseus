import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_signature_cli(monkeypatch):
    sqlalchemy_mod = ModuleType("sqlalchemy")
    sqlalchemy_mod.text = lambda value: value
    core_mod = ModuleType("core")
    database_mod = ModuleType("core.database")
    database_mod.engine = object()
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy_mod)
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.database", database_mod)

    path = Path(__file__).resolve().parent.parent / "scripts" / "odysseus-signature"
    loader = importlib.machinery.SourceFileLoader("odysseus_signature_cli_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_decode_png_data_accepts_data_url(monkeypatch):
    cli = _load_signature_cli(monkeypatch)

    png = b"\x89PNG\r\n\x1a\nrest"
    assert cli._decode_png_data("data:image/png;base64,iVBORw0KGgpyZXN0") == png


def test_decode_png_data_rejects_invalid_base64(monkeypatch):
    cli = _load_signature_cli(monkeypatch)

    try:
        cli._decode_png_data("not valid!!!")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected invalid base64 to exit")


def test_decode_png_data_rejects_non_png_bytes(monkeypatch):
    cli = _load_signature_cli(monkeypatch)

    try:
        cli._decode_png_data("aGVsbG8=")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected non-PNG bytes to exit")
