"""Quick Edit must route Motion / Zoom blur to the real Filter-menu blur variants.

Photoshop's Filter > Blur menu has Gaussian / Motion / Radial (Zoom) blur. The
Quick Edit deterministic fast path only handled Gaussian (masked) blur, so
"motion blur" / "zoom blur" fell through to a generic blur. These tests lock in
the deterministic routing to the Filter-menu blur actions.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
TOPBAR = (ROOT / "static" / "js" / "editor" / "build" / "topbar.js").read_text(encoding="utf-8")
MENUS = (ROOT / "static" / "js" / "editor" / "wire-topbar-menus.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_motion_blur_branch_routes_to_the_filter_menu():
    assert "// Motion / zoom blur → the real Filter-menu blur variants (deterministic)." in EDITOR
    assert r"if (/\b(?:motion|directional|panning)\s*blur\b/.test(p)) {" in EDITOR
    assert "_runQuickControlOp('filters.apply', { id: 'motion-blur' });" in EDITOR
    assert "_setAiCommandStatus('Applied a motion blur.', 'done');" in EDITOR


def test_zoom_blur_branch_routes_to_the_filter_menu():
    assert r"if (/\b(?:zoom|radial)\s*blur\b/.test(p)) {" in EDITOR
    assert "_runQuickControlOp('filters.apply', { id: 'zoom-blur' });" in EDITOR
    assert "_setAiCommandStatus('Applied a zoom blur.', 'done');" in EDITOR


def test_blur_variants_precede_the_generic_gaussian_blur():
    motion = EDITOR.index(r"if (/\b(?:motion|directional|panning)\s*blur\b/.test(p)) {")
    zoom = EDITOR.index(r"if (/\b(?:zoom|radial)\s*blur\b/.test(p)) {")
    generic = EDITOR.index("// Blur / bokeh / depth-of-field")
    assert motion < zoom < generic


def test_filter_alias_map_resolves_the_blur_variant_ids():
    assert "'motion-blur': 'blur-motion'" in EDITOR
    assert "'zoom-blur': 'blur-zoom'" in EDITOR


def test_filter_menu_has_the_blur_variant_actions():
    assert 'data-filter-action="blur-motion"' in TOPBAR
    assert 'data-filter-action="blur-zoom"' in TOPBAR
    assert "'blur-motion'" in MENUS
    assert "'blur-zoom'" in MENUS


def test_coverage_doc_records_the_blur_variants():
    row = [ln for ln in COVERAGE.splitlines() if "motion blur" in ln.lower() or "zoom blur" in ln.lower()]
    assert row, "expected a coverage row mentioning motion/zoom blur"
