import re
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
EDITOR_JS = (REPO / "static/js/galleryEditor.js").read_text(encoding="utf-8")
CSS = (REPO / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (REPO / "static/sw.js").read_text(encoding="utf-8")


def test_mobile_footer_sits_flush_on_the_sheet():
    # The floating phone footer docks flush to the sheet top (footer bottom ==
    # sheet top, no 6px canvas gap that the overlapping shadows greyed into a
    # fake scrollbar).
    assert "Math.round(vh - r.top)" in EDITOR_JS
    assert "Math.round(vh - r.top + 6)" not in EDITOR_JS


def test_phone_menu_bar_compaction_rules_present():
    assert ".ge-topbar-left .ge-stacked-btn {" in CSS
    assert "font-size: 11px;" in CSS
    assert "padding: 0 6px !important;" in CSS
    # Compaction drops the Save action label only. The draft status is the
    # button's role="status" live region (its icon is aria-hidden), so it must
    # stay rendered; interaction-contract.spec.js checks it at 390px.
    assert ".ge-topbar.ge-topbar-compact #ge-save-menu-btn .ge-save-action-label { display: none !important; }" in CSS
    assert not re.search(r"ge-topbar-compact\s+#ge-draft-status\s*\{[^}]*display:\s*none", CSS)


def test_sw_cache_name_is_version_agnostic_pattern():
    m = re.search(r"CACHE_NAME\s*=\s*'([^']+)'", SW)
    assert m is not None
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
