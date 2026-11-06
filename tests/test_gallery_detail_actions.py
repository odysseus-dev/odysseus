from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gallery_detail_download_is_primary_and_chat_is_in_menu() -> None:
    source = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    header_start = source.index('<div class="gallery-detail-header">')
    menu_start = source.index('<div class="gallery-detail-menu dropdown"', header_start)
    menu_end = source.index('</div>', menu_start)
    header = source[header_start:menu_start]
    menu = source[menu_start:menu_end]

    assert 'id="gallery-download-btn"' in header
    assert 'id="gallery-chat-photo-btn"' not in header
    assert 'id="gallery-chat-photo-btn"' in menu
    assert 'id="gallery-download-btn"' not in menu
