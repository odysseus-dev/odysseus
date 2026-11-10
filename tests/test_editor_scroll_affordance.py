from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static/js/editor/scroll-affordance.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
CSS = ROOT / "static/css/13-image-editor.css"


def test_scroll_affordance_module_shape():
    src = MODULE.read_text(encoding="utf-8")
    assert "export function attachScrollAffordance" in src
    # Mirrors document.js updateArrowVisibility: flat fade on the side with
    # nothing more to scroll to.
    assert "classList.toggle('is-at-left'" in src
    assert "classList.toggle('is-at-right'" in src
    assert "scrollLeft <= 1" in src
    assert "scrollWidth - el.clientWidth" in src
    # Re-attach is a no-op (guards against duplicate listeners across reopens).
    assert "__geScrollAffordance" in src


def test_gallery_attaches_affordance_to_both_rails():
    src = GALLERY.read_text(encoding="utf-8")
    assert "import { attachScrollAffordance } from './editor/scroll-affordance.js';" in src
    assert "attachScrollAffordance(container.querySelector('.ge-topbar-left'))" in src
    assert "attachScrollAffordance(container.querySelector('.ge-toolbar-scroll'))" in src


def test_mobile_css_fades_overflowing_rail_edges():
    css = CSS.read_text(encoding="utf-8")
    # Scoped to the phone breakpoint so desktop panels are untouched.
    idx = css.index("Audit #5")
    before = css[:idx]
    assert before.rfind("@media (max-width: 700px)") > 0
    assert ".ge-topbar-left.ge-scroll-fade," in css
    assert ".ge-toolbar-scroll.ge-scroll-fade {" in css
    assert "mask-image: linear-gradient(to right, transparent 0, #000 42px" in css
    assert ".ge-topbar-left.ge-scroll-fade.is-at-left.is-at-right," in css
    assert ".ge-toolbar-scroll.ge-scroll-fade.is-at-left.is-at-right" in css
