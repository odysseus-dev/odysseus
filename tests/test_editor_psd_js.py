from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


PSD = read("static/js/editor/psd.js")
EXPORT = read("static/js/editor/export-dialog.js")
IMPORT = read("static/js/editor/wire-import.js")
SW = read("static/sw.js")


def test_psd_module_exports():
    assert "export function exportPsd(" in PSD
    assert "export function parsePsd(" in PSD
    assert "export function packBits(" in PSD
    assert "export function unpackBits(" in PSD
    assert "8BPS" in PSD
    assert "const psb = version === 2;" in PSD


def test_export_dialog_offers_psd():
    assert "from './psd.js';" in EXPORT
    assert "exportPsd(" in EXPORT
    assert "psd:" in EXPORT
    assert 'data-format="psd"' in EXPORT


def test_import_handles_psd():
    assert "from './psd.js';" in IMPORT
    assert "parsePsd(" in IMPORT
    assert ".psd" in IMPORT


def test_psd_module_is_precached():
    assert "/static/js/editor/psd.js" in SW
