from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "static/js/gallery.js"


def test_editor_landing_has_an_import_button_next_to_new_canvas():
    """The editor landing must offer a direct "Import" action.

    It sits in the same action row as "New canvas..." / "Browse gallery" and,
    on file pick, opens the editor on the chosen raster image (``openEditor``
    takes an image data URL as its first argument).
    """
    src = GALLERY.read_text(encoding="utf-8")
    assert 'id="gallery-editor-import"' in src, "landing Import button missing"
    # Button order: New canvas → Import → Browse gallery.
    new_at = src.index('id="gallery-editor-new"')
    import_at = src.index('id="gallery-editor-import"')
    pick_at = src.index('id="gallery-editor-pick"')
    assert new_at < import_at < pick_at, "Import must sit next to New canvas"
    # Wiring opens the editor on the selected file's data URL.
    assert "getElementById('gallery-editor-import')?.addEventListener('click'" in src
    assert "readAsDataURL(file)" in src
    assert "await openEditor(reader.result, null, null, name)" in src
