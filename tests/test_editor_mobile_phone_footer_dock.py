import re
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
EDITOR_JS = (REPO / "static/js/galleryEditor.js").read_text(encoding="utf-8")
CSS = (REPO / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (REPO / "static/sw.js").read_text(encoding="utf-8")


def test_footer_docks_to_the_sheet_top():
    # Footer bottom == sheet top (vh - sheetTop), no seam.
    assert "footer.style.bottom = Math.max(0, Math.round(vh - r.top)) + 'px';" in EDITOR_JS
    assert "document.body.classList.add('ge-footer-docked');" in EDITOR_JS


def test_canvas_region_sized_to_meet_the_footer():
    # The canvas region is resized so its bottom meets the footer top.
    assert "const desiredBottom = Math.round(r.top - footerH);" in EDITOR_JS
    assert "area.style.height = want ? want + 'px' : '';" in EDITOR_JS
    assert "state._footerDocked = true;" in EDITOR_JS


def test_fit_zoom_drops_pad_when_docked():
    # When docked the fit uses no padding so the image fills the region flush.
    assert "const pad = state._footerDocked ? 0 : 20;" in EDITOR_JS


def test_css_squares_the_docked_footer_and_hides_the_grab():
    assert ".ge-editor-footer.ge-footer-floating {" in CSS
    assert "border-radius: 0;" in CSS
    assert "border-bottom: 0;" in CSS
    assert "body.ge-footer-docked .ge-right-panel .ge-layers-grab {" in CSS
    assert "display: none !important;" in CSS


def test_sw_cache_name_is_version_agnostic_pattern():
    m = re.search(r"CACHE_NAME\s*=\s*'([^']+)'", SW)
    assert m is not None
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
