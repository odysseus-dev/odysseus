import sys
from types import ModuleType

from tests.helpers.cli_loader import load_script


def _load_signature_cli(monkeypatch):
    sqlalchemy_mod = ModuleType("sqlalchemy")
    sqlalchemy_mod.text = lambda value: value
    core_mod = ModuleType("core")
    database_mod = ModuleType("core.database")
    database_mod.engine = object()
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy_mod)
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.database", database_mod)
    return load_script("odysseus-signature")


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
