"""Crop tool options: aspect-ratio presets in the options bar.

The Crop options bar was empty and there was no persistent aspect control
(only a Shift-held snapshot during a drag). These tests pin the new
Free / Original / 1:1 / 4:5 / 16:9 preset section, its wiring, and the
crop tool's ratio lock.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "static/js/editor/build/controls.js"
RIGHT_PANEL = ROOT / "static/js/editor/build/right-panel.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
CROP = ROOT / "static/js/editor/tools/crop.js"
STATE = ROOT / "static/js/editor/state.js"
CSS = ROOT / "static/css/13-image-editor.css"


def _read(p):
    return p.read_text(encoding="utf-8")


def test_controls_has_crop_preset_section():
    src = _read(CONTROLS)
    assert 'id="ge-crop-section"' in src
    assert 'id="ge-crop-presets"' in src
    assert "cropAspectPreset = 'free'" in src
    for preset in ("free", "original", "1:1", "4:5", "16:9"):
        assert f'data-crop-preset="{preset}"' in src, preset
    # the active preset is reflected on the rendered button
    assert "cropAspectPreset === 'free' ? ' active'" in src


def test_state_and_right_panel_seed_preset():
    assert "cropAspectPreset: 'free'," in _read(STATE)
    rp = _read(RIGHT_PANEL)
    assert "cropAspectPreset: state.cropAspectPreset," in rp


def test_gallery_shows_section_and_wires_presets():
    src = _read(GALLERY)
    assert "cropSection.style.display = toolId === 'crop'" in src
    assert "'gradient', 'bucket', 'crop', 'eraser'" in src
    assert "applyCropPreset" in src
    # Wiring runs after the controls mount, so it resolves via getElementById.
    assert "document.getElementById('ge-crop-presets')" in src
    assert "state.cropAspectPreset = preset" in src
    assert "_showCropApply()" in src


def test_crop_tool_applies_preset_ratio_lock():
    src = _read(CROP)
    assert "state.cropAspectPreset" in src
    assert "presetRatio" in src
    assert "lockRatio" in src


def test_css_has_active_preset_style():
    assert ".ge-crop-preset.active" in _read(CSS)
