from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
LAYOUT_CSS = (ROOT / "static" / "css" / "_layout.css").read_text(encoding="utf-8")
SIDEBAR_JS = (ROOT / "static" / "js" / "sidebar-layout.js").read_text(encoding="utf-8")


def test_android_drawer_has_hamburger_before_brand():
    toggle_pos = INDEX_HTML.index('id="sidebar-toggle-btn"')
    brand_pos = INDEX_HTML.index('id="sidebar-brand-btn"')

    assert toggle_pos < brand_pos
    assert 'aria-label="Close navigation menu"' in INDEX_HTML
    assert "html.android-webview .sidebar-hamburger" in LAYOUT_CSS
    assert "display: flex !important;" in LAYOUT_CSS


def test_mobile_drawer_uses_one_breakpoint_for_backdrop_and_outside_click():
    assert "const isMobileSidebarViewport = () => window.innerWidth < 768 || isTouchLandscape();" in SIDEBAR_JS
    assert "if (!isMobileSidebarViewport()) return; // desktop keeps sidebar open" in SIDEBAR_JS
    assert "if (!isMobileSidebarViewport()) { mobileBackdrop.classList.remove('visible'); return; }" in SIDEBAR_JS
    assert "window.syncRailSide = syncRailSide;\n  updateMobileBackdrop();" in SIDEBAR_JS
    assert "@media (max-width: 768px) {\n      #sidebar-backdrop { display:block !important; }\n    }" in LAYOUT_CSS
    assert "html.android-webview #sidebar-backdrop { display:block !important; }" in LAYOUT_CSS
