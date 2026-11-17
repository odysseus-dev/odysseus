from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_psd_module_has_layered_codec():
    src = read("static/js/editor/psd.js")
    for token in [
        "export function encodeLayeredPsd(",
        "export function decodeLayeredPsd(",
        "blendRgbaInto",
        "rgbaToPlanes",
    ]:
        assert token in src


def test_psd_module_reads_and_writes_psb_version_2():
    src = read("static/js/editor/psd.js")
    for token in [
        "version !== 1 && version !== 2",
        "const psb = version === 2;",
        "const longLen = psb ? 8 : 4;",
        "readSection(view, bytes, offset, psb)",
        "psb = false",
        "writeUint16(view, 4, psb ? 2 : 1)",
        "setBigUint64",
    ]:
        assert token in src


def test_gallery_uses_psb_for_large_documents():
    src = read("static/js/galleryEditor.js")
    assert "state.imgWidth > 30000 || state.imgHeight > 30000" in src
    assert "layers: descriptors, psb" in src
    assert "edited-image.psb" in src


def test_topbar_file_menu_exports_layered_psd():
    src = read("static/js/editor/build/topbar.js")
    assert 'data-file-action="export-psd"' in src


def test_wire_extra_menus_dispatches_export_psd():
    src = read("static/js/editor/wire-extra-menus.js")
    assert "action === 'export-psd'" in src
    assert "exportLayeredPsd" in src


def test_gallery_exports_layered_psd():
    src = read("static/js/galleryEditor.js")
    for token in [
        "_blendKeyFor",
        "_exportLayeredPsd",
        "_encodeLayeredPsd",
        "exportLayeredPsd: () => _exportLayeredPsd()",
    ]:
        assert token in src


def test_import_handles_layered_psd():
    src = read("static/js/editor/wire-import.js")
    for token in [
        "decodeLayeredPsd",
        "handleImportedLayers",
        "PSD_BLEND",
    ]:
        assert token in src
