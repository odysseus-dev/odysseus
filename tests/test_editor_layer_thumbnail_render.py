from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_hover_thumbnail_uses_rendered_layer_output():
    source = (ROOT / "static/js/galleryEditor.js").read_text()
    start = source.index("function _showLayerThumb")
    end = source.index("function _hideLayerThumb", start)
    block = source[start:end]

    assert "const preview = _renderLayerOutput(layer) || layer.canvas;" in block
    assert "const lw = preview.width, lh = preview.height;" in block
    assert "ctx.drawImage(preview, 0, 0, tw, th);" in block
    assert "c.setAttribute('role', 'img');" in block
    assert "c.setAttribute('aria-label', `${layer.name || 'Layer'} preview`);" in block


def test_inline_layer_rows_share_the_common_thumbnail_renderer():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text()

    assert "const thumb = createInlineThumbnail(() => renderLayer?.(layer) || layer.canvas, `${layer.name} preview`);" in panel
    assert "thumb._refreshPreview = draw;" in panel
    assert "thumbCtx.fillStyle = ((x / tile + y / tile) & 1)" not in panel


def test_inline_thumbnails_are_exposed_as_labeled_images():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text()

    assert "thumb.setAttribute('role', 'img');" in panel
    assert "thumb.setAttribute('aria-label', title);" in panel


def test_layer_rows_support_keyboard_selection_without_stealing_control_keys():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text()

    assert "item.tabIndex = 0;" in panel
    assert "item.setAttribute('aria-pressed'" in panel
    assert "e.target !== item || (e.key !== 'Enter' && e.key !== ' ')" in panel


def test_group_rows_support_keyboard_selection():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text()

    assert "row.tabIndex = 0;" in panel
    assert "row.setAttribute('aria-label', `${group.name} group`);" in panel
    assert "event.target !== row || (event.key !== 'Enter' && event.key !== ' ')" in panel


def test_keyboard_focus_is_visible_for_layer_and_group_rows():
    styles = app_css()

    assert ".ge-layer-item:focus-visible," in styles
    assert ".ge-layer-group-row:focus-visible" in styles
    assert "outline: 1px solid var(--accent, var(--red));" in styles


def test_layer_subrows_are_keyboard_reachable():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text()

    assert "sub.tabIndex = 0;" in panel
    assert "sub.setAttribute('aria-label', `${mk.name || (mk.mode === 'layer' ? 'Layer Mask' : 'AI Mask')} mask`);" in panel
    assert "const activateMask = () => {" in panel
