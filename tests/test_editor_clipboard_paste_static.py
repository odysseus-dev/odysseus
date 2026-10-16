from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static/js/editor/clipboard-and-drop.js"


def test_editor_paste_owns_clipboard_images_while_editor_open():
    """Pasting an image with the editor open must not fall through to chat.

    The editor wires a window-capture ``paste`` listener. The global chat
    attach listener in ``static/app.js`` also listens on ``window`` (bubble)
    and stages any ``item.kind === 'file'`` clipboard entry into the chatbox.
    If the editor's listener bailed whenever the event target was *any*
    input/textarea/``[role=dialog]``, then a paste whose focus sat on the chat
    composer behind the editor modal went to the chatbox and never reached the
    canvas. The guard must only defer to editable fields that live INSIDE the
    editor container — and it must NOT include ``[role=dialog]``, because the
    editor itself sits inside the gallery modal's ``role=dialog`` content.
    """
    src = SRC.read_text(encoding="utf-8")
    assert "wireClipboardAndDrop" in src
    # The bail-out must be scoped to targets inside the editor.
    assert "const insideEditor = target && container && container.contains(target);" in src
    assert (
        "if (insideEditor && (target.isContentEditable || "
        "target.closest?.('input, textarea, select'))) return;"
    ) in src
    # ``[role="dialog"]`` must NOT appear in the paste guard (the editor lives
    # inside the gallery modal's role=dialog modal-content).
    assert "target.closest?.('input, textarea, select, [role=\"dialog\"]')" not in src
    # Capture-phase interception must preventDefault + stop the chat listener.
    assert "e.stopImmediatePropagation();" in src
    assert "e.preventDefault();" in src


def test_editor_ctrl_v_pastes_the_internal_selection_clipboard():
    """Ctrl/Cmd+V must paste the internal selection clipboard directly.

    On an insecure origin (the editor is served over http on a LAN IP)
    ``navigator.clipboard`` is unavailable, so copying a selection never reaches
    the OS clipboard and the native ``paste`` event carries stale ``text/plain``
    -> the internal-paste branch is skipped and Ctrl+V silently does nothing
    (the toolbar buttons worked because they bypass the event entirely).
    """
    src = SRC.read_text(encoding="utf-8")
    # Shared, hoisted paste helper (also used by the native paste listener).
    assert "const pasteAsLayer = (imgSource, label, offset = { x: 0, y: 0 }) => {" in src
    # Explicit keyboard handler for the internal clipboard.
    assert "window.addEventListener('keydown', (e) => {" in src
    assert "if (String(e.key || '').toLowerCase() !== 'v') return;" in src
    assert "if (!state.internalClipboard) return;" in src
    assert "pasteAsLayer(state.internalClipboard, 'Pasted Selection'" in src
