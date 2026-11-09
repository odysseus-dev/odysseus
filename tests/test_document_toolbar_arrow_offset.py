from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]


def test_toolbar_arrows_have_real_flex_slots_outside_icon_scroller():
    script = document_source()
    styles = app_css()

    leading = script.index('class="md-toolbar-leading-controls"')
    left_arrow = script.index('id="md-scroll-left"')
    items = script.index('id="md-toolbar-items"')
    right_arrow = script.index('id="md-scroll-right"')
    assert leading < left_arrow < items < right_arrow
    assert "has-left-scroll-arrow" in script
    assert "has-right-scroll-arrow" in script
    assert ".md-toolbar-leading-controls" in styles
    assert "position: static" in styles
    assert "flex: 0 0 28px" in styles
