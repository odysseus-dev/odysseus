import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EFFECTS = (ROOT / "static" / "js" / "editor" / "effects.js").read_text()
WORKER = (ROOT / "static" / "js" / "editor" / "effects-worker.js").read_text()
TOPBAR = (ROOT / "static" / "js" / "editor" / "build" / "topbar.js").read_text()
WIRE = (ROOT / "static" / "js" / "editor" / "wire-topbar-menus.js").read_text()
GE = (ROOT / "static" / "js" / "galleryEditor.js").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_effect_types_include_the_new_smart_filters():
    line = next(l for l in EFFECTS.splitlines() if "const EFFECT_TYPES = new Set(" in l)
    for t in ("'motion-blur'", "'zoom-blur'", "'pixelate'"):
        assert t in line, f"{t} missing from EFFECT_TYPES"


def test_default_params_for_new_smart_filters():
    assert "if (type === 'motion-blur') return { distance: 20, angle: 0 };" in EFFECTS
    assert "if (type === 'zoom-blur') return { amount: 40 };" in EFFECTS
    assert "if (type === 'pixelate') return { size: 8 };" in EFFECTS


def test_normalize_clamps_new_smart_filters():
    assert "params.distance = Math.max(0, Math.min(200, Number(params.distance) || 0));" in EFFECTS
    assert "params.amount = Math.max(0, Math.min(100, Number(params.amount) || 0));" in EFFECTS
    assert "params.size = Math.max(1, Math.min(200, Math.round(Number(params.size) || 1)));" in EFFECTS


def test_labels_for_new_smart_filters():
    assert "'motion-blur': 'Motion Blur'," in EFFECTS
    assert "'zoom-blur': 'Zoom Blur'," in EFFECTS
    assert "pixelate: 'Pixelate'," in EFFECTS


def test_render_effects_implements_the_three_filters():
    assert "} else if (effect.type === 'motion-blur') {" in EFFECTS
    assert "const samples = Math.max(1, Math.min(64, Math.round(dist) || 1));" in EFFECTS
    assert "ctx.drawImage(current, dx * t, dy * t);" in EFFECTS
    assert "} else if (effect.type === 'zoom-blur') {" in EFFECTS
    assert "const samples = Math.max(1, Math.min(48, Math.round(amt * 48) || 1));" in EFFECTS
    assert "} else if (effect.type === 'pixelate') {" in EFFECTS
    assert "ctx.drawImage(tiny, 0, 0, next.width, next.height);" in EFFECTS


def test_effects_worker_mirrors_the_three_filters():
    assert "} else if (effect.type === 'motion-blur') {" in WORKER
    assert "} else if (effect.type === 'zoom-blur') {" in WORKER
    assert "} else if (effect.type === 'pixelate') {" in WORKER
    assert "const tiny = new OffscreenCanvas(sw, sh);" in WORKER


def test_add_retained_effect_handles_the_new_types():
    assert "if (type === 'motion-blur' || type === 'zoom-blur' || type === 'pixelate') {" in GE
    assert "const toParams = (values) => type === 'motion-blur'" in GE
    assert "_filterSliderPrompt('Retained ' + label" in GE
    assert "layer.effects.push(_normalizeEffect({ type, params: toParams(result) }));" in GE


def test_edit_retained_effect_handles_the_new_types():
    assert "if (type === 'motion-blur' || type === 'zoom-blur' || type === 'pixelate') {" in GE
    assert "effect.params = toParams(result);" in GE
    assert "{ ...effect, params: toParams(values) }" in GE


def test_filter_retained_menu_lists_the_new_items():
    assert 'data-filter-action="effect-motion-blur"' in TOPBAR
    assert 'data-filter-action="effect-zoom-blur"' in TOPBAR
    assert 'data-filter-action="effect-pixelate"' in TOPBAR


def test_wire_routes_the_new_menu_items():
    assert "else if (action === 'effect-motion-blur') addRetainedEffect?.('motion-blur');" in WIRE
    assert "else if (action === 'effect-zoom-blur') addRetainedEffect?.('zoom-blur');" in WIRE
    assert "else if (action === 'effect-pixelate') addRetainedEffect?.('pixelate');" in WIRE


def test_service_worker_cache_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
