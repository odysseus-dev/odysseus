from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "static/css/13-image-editor.css"


def test_editor_landing_is_scrollable_at_every_width():
    """"Saved projects" list must not clip the launch controls off-screen.

    The editor landing centres its content vertically. With many saved
    projects (worst in list view) the content is taller than the sheet, and
    without an overflow context the flex centring pushes the "Image Editor"
    heading, the New/Browse buttons and the grid/list toggle ABOVE the modal,
    where #gallery-editor-container's ``overflow: hidden`` makes them
    unreachable. The base rule only gained ``overflow-y: auto`` inside the
    ``max-width: 620px`` media query, so wider windows still got stuck.
    """
    src = CSS.read_text(encoding="utf-8")
    scoped = (
        '.gallery-modal-content:has(#gallery-editor-container[style*="flex"]) '
        ".gallery-editor-landing {"
    )
    assert scoped in src, "scoped editor-landing override missing"
    block = src.split(scoped, 1)[1].split("}", 1)[0]
    assert "justify-content: flex-start;" in block, (
        "landing must not use justify-content: center at all widths - the "
        "centred content is clipped and cannot be scrolled to"
    )
    assert "overflow-y: auto;" in block
    # Auto-margin spacers keep the short (no/few drafts) case centred while
    # collapsing to 0 when the content overflows, so the top stays reachable.
    assert ".gallery-editor-landing::before" in src
    assert ".gallery-editor-landing::after" in src
    spacer = src.split(".gallery-editor-landing::before", 1)[1].split("}", 1)[0]
    assert "margin: auto;" in spacer
