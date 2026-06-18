from pathlib import Path


def test_tour_hint_has_accessibility_and_keyboard_dismiss():
    src = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "js"
        / "tourHints.js"
    ).read_text(encoding="utf-8")

    assert "pop.setAttribute('role', 'dialog')" in src
    assert "pop.setAttribute('aria-live', 'polite')" in src
    assert "aria-label=\"Dismiss window snapping tip\"" in src
    assert "if (e.key === 'Escape') dismiss()" in src
    assert "dismissBtn.focus?.()" in src
