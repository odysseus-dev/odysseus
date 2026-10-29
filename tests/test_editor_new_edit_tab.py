import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")


def test_new_edit_tab_asks_for_the_canvas_size_like_the_main_menu():
    block = EDITOR[EDITOR.index("plus.className = 'gallery-tab gallery-tab-new';"):]
    block = block[:block.index("extra.appendChild(plus);")]
    assert "plus.title = 'New edit tab';" in block
    assert "plus.setAttribute('aria-label', 'New edit tab');" in block
    # Same dialog as File > New Image instead of a silent 1024x1024 document.
    assert "await _promptCanvasSize({" in block
    assert "title: 'New edit'," in block
    assert "okLabel: 'Create'," in block
    assert "showPresets: true," in block
    assert "window.geEditorControl?.invoke('document.create', { width: size.w, height: size.h, name: 'Untitled' });" in block
    assert "document.create', {})" not in EDITOR


def test_closing_the_new_tab_keeps_the_other_documents_alive():
    assert "window.geEditorCloseActiveDocument = () => {" in EDITOR
    assert "if (_openDocuments.length <= 1) return false;" in EDITOR
    # The switch must not re-add the closed id or copy its pixels over the
    # surviving document.
    assert "let _suppressDocSync = false;" in EDITOR
    assert "if (_suppressDocSync || !state.editorOpen) return null;" in EDITOR
    assert "_suppressDocSync = true;" in EDITOR
    assert ".finally(() => { _suppressDocSync = false; });" in EDITOR
    # The primary Edit-tab close tries the single-document close first.
    assert "if (window.geEditorCloseActiveDocument?.()) return;" in GALLERY
