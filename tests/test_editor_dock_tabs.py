from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCK = ROOT / "static/js/editor/build/dock.js"


def test_dock_recomputes_tab_overflow_after_layout():
    src = DOCK.read_text(encoding="utf-8")
    # The dock is built before it is attached (width 0), so a one-shot check at
    # build time could never see the real column width. A ResizeObserver plus a
    # post-layout frame keeps the overflow chevron in sync.
    assert "new ResizeObserver" in src
    assert "ro.observe(dock)" in src
    assert "requestAnimationFrame(() => groupEls.forEach(el => syncTabsOverflow(el)))" in src


def test_dock_overflow_chevron_tracks_scroll_width():
    src = DOCK.read_text(encoding="utf-8")
    assert "const overflowing = tabsEl.scrollWidth - tabsEl.clientWidth > 1;" in src
    assert "moreEl.hidden = !(overflowing || typeof onCloseGroup === 'function');" in src


def test_dock_hides_partial_tabs_instead_of_clipping_labels():
    src = DOCK.read_text(encoding="utf-8")
    # Reset then hide trailing tabs so no label is clipped mid-word
    # (Photoshop-style tab overflow).
    assert "items.forEach(item => { item.style.display = ''; });" in src
    assert "item.style.display = 'none';" in src
    # The active tab is always kept visible so the open panel keeps its anchor.
    assert "const activeItem = active ? active.closest('.ge-dock-tab-item') : null;" in src
    assert "if (item === activeItem || (!cutting && used + w <= limit + 0.5))" in src
