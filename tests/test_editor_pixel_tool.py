"""Pixel is its own tool, not a brush preset ("brush type").

The 1px hard-square brush used to be exposed as the built-in `pixel-1px`
preset, listed under the flyout's "Brushes" heading. It is now a real tool
(`pixel`) in the brush slot's flyout, next to Brush and Eraser, and the pixel
preset is hidden from the preset list/dropdown.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


TOOLBAR = _read("static/js/editor/build/toolbar.js")
STROKE = _read("static/js/editor/tools/stroke.js")
PIPELINE = _read("static/js/editor/stroke-pipeline.js")
EDITOR = _read("static/js/galleryEditor.js")
SLIDERS = _read("static/js/editor/stroke-tool-sliders.js")
SW = _read("static/sw.js")


def test_pixel_is_a_tool_with_an_icon():
    assert "pixel: { label: 'Pixel'," in TOOLBAR
    # it sits next to brush + eraser in the flyout slot
    assert "['brush', 'eraser', 'pixel']" in TOOLBAR


def test_pixel_is_not_listed_as_a_brush_preset():
    # the flyout's Brushes list skips pixel presets...
    assert "loadBrushPresets().filter((preset) => !preset.pixel)" in TOOLBAR
    # ...and so does the options-strip preset dropdown.
    assert "const list = presets.filter(preset => !preset.pixel);" in SLIDERS
    assert "renderPresets(presets.some(item => item.id === savedPresetId && !item.pixel) ? savedPresetId : '');" in SLIDERS


def test_pixel_stroke_is_handled_by_the_shared_pipeline():
    assert "'pixel'" in STROKE.split("const STROKE_TOOLS")[1].split("];")[0]
    assert "if (tool === 'pixel') return 'Pixel stroke';" in STROKE
    assert "'pixel'" in PIPELINE.split("const PAINT_TOOLS")[1].split("];")[0]
    assert "if ((tool === 'pixel' || (state.brushPixel && (tool === 'brush' || tool === 'eraser'))) && !target.paintingMask) {" in PIPELINE


def test_editor_registers_the_pixel_tool_everywhere():
    assert "'brush', 'pixel', 'eraser'" in EDITOR  # _pixelPaintTools
    assert "const needsBrush = ['brush', 'pixel', 'eraser', 'clone', 'heal', 'smudge', 'dodge', 'burn'].includes(toolId);" in EDITOR
    assert "const strokeTool = (toolId === 'brush' || toolId === 'pixel' || toolId === 'dodge' || toolId === 'burn') ? 'brush'" in EDITOR
    assert "'brush', 'pixel', 'gradient', 'bucket'" in EDITOR  # hasToolControls
    assert "'brush', 'pixel', 'eraser', 'inpaint', 'lasso'" in EDITOR  # useCircle
    # selecting the Pixel tool forces the pixel flag; brush/eraser drop it
    assert "if (toolId === 'pixel') state.brushPixel = true;" in EDITOR
    assert "else if (toolId === 'brush' || toolId === 'eraser') state.brushPixel = false;" in EDITOR


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
