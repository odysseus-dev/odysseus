"""Raster data images are images, while data links and other MIME types stay blocked."""
import pytest

from tests.test_markdown_rendering_js import _run_markdown_case, node_available


@pytest.mark.parametrize("mime", ["png", "jpeg", "jpg", "gif", "webp"])
def test_markdown_raster_data_image_has_one_source_and_no_data_navigation(node_available, mime):
    uri = f"data:image/{mime};base64,AA=="
    html = _run_markdown_case(f"Before ![Owned raster]({uri}) after.")
    assert html.count('class="chat-inline-image"') == 1
    assert f'src="{uri}"' in html
    assert html.count(uri) == 1  # No data navigation or base64 fallback caption.
    assert 'href="data:' not in html
    assert 'alt="Owned raster"' in html
    assert 'class="chat-image-fallback">Owned raster</span>' in html


@pytest.mark.parametrize("uri", [
    "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    "data:text/html;base64,PGI+eDwvYj4=",
    "data:image/png,unencoded",
    "data:image/png;charset=utf-8;base64,AA==",
    "data:image/png;base64,AA==%22onerror=alert",
    "javascript:alert(1)",
])
def test_markdown_non_raster_or_non_base64_data_is_still_plain_text(node_available, uri):
    html = _run_markdown_case(f"![Owned rejected image]({uri})")
    assert '<img' not in html
    assert 'Owned rejected image' in html


def test_raster_data_link_stays_non_navigable(node_available):
    html = _run_markdown_case('[Owned link](data:image/png;base64,AA==)')
    assert '<a' not in html
    assert 'Owned link' in html
