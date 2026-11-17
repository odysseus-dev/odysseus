from pathlib import Path
import re

from tests.helpers.stylesheets import app_css, stylesheet_cache_version


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_gallery_exposes_active_filter_organization():
    js = _read("static/js/gallery.js")

    assert "gallery-active-filter-pill" in js
    assert "gallery-clear-all-filters" in js
    assert 'data-clear="search"' in js
    assert 'data-clear="model"' in js


def test_gallery_cards_keep_badges_without_hover_inspect_label():
    js = _read("static/js/gallery.js")
    css = app_css()

    assert "gallery-card-overlay" in js
    assert "gallery-card-inspect" not in js
    assert "gallery-card-badge" in js
    assert ".gallery-card-overlay" in css
    assert ".gallery-card-badge" in css


def test_gallery_detail_has_richer_inspector_actions():
    js = _read("static/js/gallery.js")
    css = app_css()

    assert "gallery-detail-inspector-head" in js
    assert "gallery-detail-meta-grid" in js
    assert "gallery-detail-copy-prompt" in js
    assert "gallery-detail-reuse-prompt" in js
    assert "gallery-detail-source-link" in js
    assert ".gallery-detail-meta-grid" in css
    assert ".gallery-detail-mini-btn" in css


def test_gallery_static_cache_key_bumped():
    html = _read("static/index.html")
    app = _read("static/app.js")
    service_worker = _read("static/sw.js")

    assert stylesheet_cache_version()
    html_version = re.search(r"/static/js/gallery\.js\?v=([A-Za-z0-9_-]+)", html)
    app_version = re.search(r"gallery\.js\?v=([A-Za-z0-9_-]+)", app)
    sw_version = re.search(r"/static/js/gallery\.js\?v=([A-Za-z0-9_-]+)", service_worker)
    assert html_version and app_version and sw_version
    assert len({html_version.group(1), app_version.group(1), sw_version.group(1)}) == 1


def test_gallery_bulk_select_button_keeps_toolbar_alignment():
    js = _read("static/js/gallery.js")
    css = app_css()

    assert 'id="gallery-select-btn"' in js
    assert 'id="gallery-select-btn" title="Select for bulk actions" style="position:relative;top:0;"' in js
    assert 'class="memory-bulk-bar gallery-selection-bar hidden" id="gallery-bulk-bar"' in js
    assert 'id="gallery-bulk-delete"' in js
    assert "_bulkDelete(_selectedIds())" in js
    assert 'class="memory-bulk-bar gallery-selection-bar hidden" id="gallery-editor-drafts-bulk"' in js
    assert 'class="gallery-select-btn gallery-toolbar-action" id="gallery-editor-drafts-select"' in js
    assert "#gallery-select-btn { position: relative; top: 4px !important;" in css
    assert "#gallery-albums-search { top: -4px; }" in css
    assert "#gallery-albums-select-btn { top: -4px !important; }" in css


def test_gallery_selection_bars_share_style_and_escape_priority():
    gallery = _read("static/js/gallery.js")
    ui = _read("static/js/ui.js")
    css = app_css()

    assert gallery.count("memory-bulk-bar gallery-selection-bar hidden") == 3
    assert "window.__galleryCancelSelection = () =>" in gallery
    assert "if (_selectMode)" in gallery
    assert "if (_albumSelectMode)" in gallery
    assert "if (_draftsSelectMode)" in gallery
    assert "window.__galleryCancelSelection?.()" in ui
    for button_id in (
        "#gallery-bulk-delete",
        "#gallery-albums-bulk-delete",
        "#gallery-editor-drafts-bulk-delete",
    ):
        assert button_id in css
    assert "color: var(--color-error, #f44) !important;" in css


def test_editor_saved_projects_support_grid_and_list_views():
    gallery = _read("static/js/gallery.js")
    css = app_css()

    assert 'id="gallery-editor-drafts-view" role="group"' in gallery
    assert 'data-view="grid"' in gallery
    assert 'data-view="list"' in gallery
    assert "gallery-drafts-view" in gallery
    assert "grid.classList.toggle('list-view', _draftsView === 'list')" in gallery
    assert ".gallery-editor-drafts-grid.list-view" in css
    assert ".gallery-drafts-view-toggle" in css
    toggle_rule = css[css.index(".gallery-drafts-view-toggle {"):][:260]
    assert "position: relative;" in toggle_rule
    assert "top: 0;" in toggle_rule
    icon_rule = css[css.index(".gallery-drafts-view-toggle button svg {"):][:150]
    assert "display: block;" in icon_rule
    assert "position: static;" in icon_rule
    assert "transform: none;" in icon_rule
    active_rule = css[css.index(".gallery-drafts-view-toggle button.active {"):][:180]
    assert "height: 22px;" in active_rule
    assert "gallery-editor-heading" in gallery
    assert ">Browse gallery</button>" in gallery
    assert ">Browse photos</button>" not in gallery
    assert "Pick template" in gallery
    assert "gallery-editor-template-option" in gallery
    assert ".gallery-editor-template-option svg { color: var(--accent, var(--red)); }" in css
    assert "const maxW = 30;" in gallery
    assert "const maxH = 18;" in gallery
    assert ".gallery-editor-template-option:nth-child(n + 5)" in css
    assert "max-width: 180px;" in css
    assert "@media (max-width: 360px)" in css
    assert ".gallery-editor-drafts-search" in css
    assert ".gallery-modal-content:has(#gallery-editor-container[style*=\"flex\"]) > .modal-body" in css
    assert "saved-project list cannot push the launch controls below the viewport" in css
    assert "gallery-editor-template-select" not in gallery
    assert "gallery-editor-draft-delete-trash" in gallery
    assert ".gallery-editor-drafts-grid.list-view .gallery-editor-draft-delete-trash" in css
    trash_rule = css[css.index(".gallery-editor-drafts-grid.list-view .gallery-editor-draft-delete-trash"):][:220]
    assert "width: 18px;" in trash_rule
    assert "height: 18px;" in trash_rule
    assert "top: -2px;" in trash_rule


def test_empty_albums_show_action_tiles_instead_of_no_albums_message():
    gallery = _read("static/js/gallery.js")
    css = app_css()

    assert "function _albumActionTiles()" in gallery
    assert 'id="gallery-albums-new"' in gallery
    assert 'id="gallery-albums-upload"' in gallery
    assert gallery.count('class="gallery-card gallery-card-upload gallery-album-action-tile"') == 2
    assert '<div class="gallery-card-upload-label">Upload</div>' in gallery
    assert "No albums yet." not in gallery
    assert 'wrap.innerHTML = `<div class="gallery-albums-grid">${_albumActionTiles()}</div>`;' in gallery
    assert ".gallery-album-action-tile" in css


def test_gallery_body_uses_available_window_height_without_grid_crop():
    css = app_css()

    gallery_section = css.index("/* ── Gallery (image library) ── */")
    modal_start = css.index(".gallery-modal-content {", gallery_section)
    modal_rule = css[modal_start:modal_start + 180]
    grid_rule = css[css.index(".gallery-grid {"):][:300]
    assert "align-content: start;" in grid_rule
    assert "max-height: calc(92vh - 165px);" in grid_rule
    assert "max-height: 60vh;" not in grid_rule


def test_gallery_detail_container_drops_grid_margin_so_body_does_not_scroll():
    # With a photo open on desktop, the modal body, the images container and
    # the detail view are each `height: 100%`. The container's inline 2px top
    # margin (grid-view spacing) would then overflow the body by 2px, which
    # shows a second, outer scrollbar next to the inspector's own.
    js = _read("static/js/gallery.js")
    css = app_css()

    assert 'id="gallery-images-container" style="margin-top:2px"' in js
    rule = '.gallery-images-container:has(> #gallery-detail[style*="flex"]) {'
    idx = css.index(rule)
    assert "margin-top: 0 !important;" in css[idx:idx + 120]


def test_gallery_photo_search_is_not_vertically_offset():
    css = app_css()

    search_idx = css.index(".gallery-search-wrap {")
    assert "top: 0;" in css[search_idx:search_idx + 180]
    input_idx = css.index(".gallery-search {")
    assert "top: 4px;" in css[input_idx:input_idx + 180]
    count_idx = css.index("#gallery-bulk-count,")
    assert "#gallery-editor-drafts-bulk-count" in css[count_idx:count_idx + 140]
    assert "top: -1px !important;" in css[count_idx:count_idx + 140]
    drafts_search_idx = css.index(".gallery-editor-drafts-search {")
    assert "max-width: none;" in css[drafts_search_idx:drafts_search_idx + 220]
    select_idx = css.index(".gallery-model-filter,")
    assert "top: 4px;" in css[select_idx:select_idx + 360]
