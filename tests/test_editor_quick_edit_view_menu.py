from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"


def test_view_menu_block_drives_the_real_menu_items():
    src = EDITOR.read_text(encoding="utf-8")
    start = src.index("// --- View menu (Photoshop View")
    block = src[start:src.index("// No deterministic local op matched", start)]
    # Drives the real View-menu items so the overlay refresh + persistence run.
    assert 'document.querySelector(`#ge-view-menu [data-view-action="${action}"]`)' in block
    for action in ("clear-guides", "snap-grid", "snap", "grid", "rulers"):
        assert f"'{action}'" in block
    assert "state.rulersVisible" in block
    assert "state.gridVisible" in block
    assert "state.snapEnabled" in block
    assert "state.snapToGrid" in block


def test_view_menu_requires_an_explicit_view_verb():
    src = EDITOR.read_text(encoding="utf-8")
    start = src.index("// --- View menu (Photoshop View")
    block = src[start:src.index("// No deterministic local op matched", start)]
    assert "if (/\\b(rulers?|grid|guides?|snap(?:ping)?)\\b/.test(p)) {" in block
    assert "const viewOff = /\\b(hide|turn\\s+off|switch\\s+off|disable|remove|clear)\\b/.test(p);" in block
    assert "const viewToggle = /\\btoggle\\b/.test(p);" in block
    assert "const viewOn = /\\b(show|display|reveal|turn\\s+on|switch\\s+on|enable)\\b/.test(p);" in block
    # A bare noun with no verb must not flip a toggle.
    assert "if (viewOn || viewOff || viewToggle) {" in block


def test_view_menu_status_messages():
    src = EDITOR.read_text(encoding="utf-8")
    start = src.index("// --- View menu (Photoshop View")
    block = src[start:src.index("// No deterministic local op matched", start)]
    assert "_setAiCommandStatus('Cleared the guides.', 'done');" in block
    assert "`Snapping to the grid ${want ? 'is on' : 'is off'}.`" in block
    assert "`Snapping ${want ? 'is on' : 'is off'}.`" in block
    assert "`${label} ${want ? 'shown' : 'hidden'}.`" in block


def test_view_menu_block_sits_after_clipboard_and_before_the_fallthrough():
    src = EDITOR.read_text(encoding="utf-8")
    paste = src.index("await _runQuickControlOp('selection.paste', {});")
    view = src.index("// --- View menu (Photoshop View")
    fallthrough = src.index("// No deterministic local op matched")
    assert paste < view < fallthrough
