from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLBAR = (ROOT / "static/js/editor/build/toolbar.js").read_text()


def test_tool_flyout_is_positioned_relative_to_its_containing_block():
    # The flyout is position:fixed, but the gallery modal has a transform that
    # makes it the containing block, so the inline left/top must subtract that
    # block's origin or the popup opens far from its button.
    assert "const cb = flyout.offsetParent;" in TOOLBAR
    assert "const cbX = cbRect.left + (cb ? cb.clientLeft : 0);" in TOOLBAR
    assert "const cbY = cbRect.top + (cb ? cb.clientTop : 0);" in TOOLBAR
    assert "flyout.style.left = (r.right + 4 - cbX) + 'px';" in TOOLBAR
    assert "flyout.style.top = (Math.max(r.top, 4) - cbY) + 'px';" in TOOLBAR
