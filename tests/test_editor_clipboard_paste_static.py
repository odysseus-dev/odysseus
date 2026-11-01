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
    # The bail-out must be scoped to targets inside the editor, and must not
    # throw for a paste whose target is not a Node (e.g. dispatched at window).
    assert "const insideEditor = target instanceof Node && !!container && container.contains(target);" in src
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
    """Ctrl/Cmd+V must not shadow a newer OS-clipboard image.

    Intercepting the keystroke (preventDefault) bypasses the browser's paste
    event and always re-pasted ``state.internalClipboard``, so a screenshot
    taken outside the gallery could never be pasted. The key handler may only
    fall back to the internal copy when the paste event delivered no image
    (insecure origin: copy never reaches the OS clipboard).
    """
    src = SRC.read_text(encoding="utf-8")
    # Shared, hoisted paste helper (also used by the native paste listener).
    assert "const pasteAsLayer = (imgSource, label, offset = { x: 0, y: 0 }) => {" in src
    # The paste listener must prefer real clipboard images.
    assert "const imageItem = Array.from(e.clipboardData?.items || [])" in src
    # Deferred keyboard fallback for the internal clipboard.
    assert "window.addEventListener('keydown', (e) => {" in src
    assert "if (String(e.key || '').toLowerCase() !== 'v') return;" in src
    assert "pasteKeyPending = true;" in src
    assert "if (!pasteKeyPending) return;" in src
    assert "pasteAsLayer(state.internalClipboard, 'Pasted Selection'" in src
    # The key handler must NOT swallow the keystroke any more.
    keydown = src[src.index("// ── Ctrl/Cmd+V ──"):src.index("// ── Drag-and-drop ──")]
    assert "e.preventDefault();" not in keydown
    assert "e.stopImmediatePropagation();" not in keydown
