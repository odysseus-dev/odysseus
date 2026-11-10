from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "static/js/galleryEditor.js"
HISTORY = ROOT / "static/js/editor/history-panel.js"
STUDIO = ROOT / "static/js/editor/panels/studio-panels.js"


def test_docked_history_panel_has_no_open_in_window_button():
    # The docked History panel already shows the timeline inline, so the
    # redundant "Open in window" button was removed (the floating window is
    # still reachable from the topbar clock button #ge-history-btn).
    src = STUDIO.read_text(encoding="utf-8")
    assert "ge-history-popout" not in src
    assert "Open in window" not in src
    assert "ge-dock-history-list" in src


def test_docked_history_group_is_height_bounded():
    src = GALLERY.read_text(encoding="utf-8")
    # The History group is bounded so its list scrolls internally instead of
    # ballooning to the full content height (which pushed it past the dock).
    assert "group: 'History', groupSize: 240" in src


def test_history_reveals_current_without_scrolling_the_dock():
    src = HISTORY.read_text(encoding="utf-8")
    # Scroll only the list, or its dock panels scroller — never the whole dock.
    assert "list.scrollHeight > list.clientHeight + 1" in src
    assert "list.closest('.ge-dock-panels')" in src
    assert "scroller.scrollTop" in src
