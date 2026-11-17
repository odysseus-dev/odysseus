import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = (ROOT / "static/js/editor/brush-engine.js").as_uri()
PRESETS = (ROOT / "static/js/editor/brush-presets.js").as_uri()


def run_node(body: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(body)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_sampler_emits_even_spacing_without_a_lone_endpoint_dab():
    data = run_node(
        f"""
        import {{ createBrushSampler }} from {json.dumps(ENGINE)};
        const samples = [];
        const sampler = createBrushSampler({{
          getDiameter: () => 20,
          getSpacing: () => 0.25,
          getSmoothing: () => 0,
          emit: sample => samples.push(sample),
        }});
        sampler.begin({{x:0,y:0,pressure:1}});
        sampler.add({{x:20,y:0,pressure:0.5}});
        sampler.end({{x:23,y:0,pressure:0.25}});
        console.log(JSON.stringify(samples));
        """
    )
    # The final point is within one spacing of the last dab, so it must not be
    # stamped as a detached dot; the last dab stays on the even-spacing grid.
    assert [round(item["x"]) for item in data] == [0, 5, 10, 15, 20]


def test_sampler_fills_a_long_final_gap_with_spaced_dabs():
    data = run_node(
        f"""
        import {{ createBrushSampler }} from {json.dumps(ENGINE)};
        const samples = [];
        const sampler = createBrushSampler({{
          getDiameter: () => 20,
          getSpacing: () => 0.25,
          getSmoothing: () => 0,
          emit: sample => samples.push(sample),
        }});
        sampler.begin({{x:0,y:0,pressure:1}});
        sampler.add({{x:20,y:0,pressure:1}});
        sampler.end({{x:37,y:0,pressure:1}});
        console.log(JSON.stringify(samples.map(sample => Math.round(sample.x))));
        """
    )
    # A far pointer-up point is reached with evenly spaced dabs (step ~5px),
    # never a single 17px-away dab that would appear as a dot.
    assert data == [0, 5, 10, 15, 20, 25, 30, 35]


def test_pressure_mapping_is_independent_and_mouse_falls_back_to_full():
    data = run_node(
        f"""
        import {{ normalisePressure, pressureValue }} from {json.dumps(ENGINE)};
        console.log(JSON.stringify({{
          mouse: normalisePressure(0),
          size: pressureValue(100, 0.25, true),
          opacityOff: pressureValue(0.8, 0.25, false),
        }}));
        """
    )
    assert data == {"mouse": 1, "size": 25, "opacityOff": 0.8}


def test_custom_presets_roundtrip_without_mutating_built_ins():
    data = run_node(
        f"""
        import {{ loadBrushPresets, saveCustomBrushPresets, captureBrushPreset, applyBrushPreset }} from {json.dumps(PRESETS)};
        const values = new Map();
        const storage = {{ getItem:key => values.get(key) || null, setItem:(key,value) => values.set(key,value) }};
        const state = {{brushSize:42,brushOpacity:80,brushFlow:60,brushSoftness:20,brushSpacing:14,brushSmoothing:45,brushBlendMode:'multiply'}};
        const preset = captureBrushPreset(state, 'Texture');
        saveCustomBrushPresets([preset], storage);
        const loaded = loadBrushPresets(storage);
        const target = {{}};
        applyBrushPreset(target, loaded.at(-1));
        console.log(JSON.stringify({{names:loaded.map(item => item.name), target}}));
        """
    )
    assert data["names"][:3] == ["Soft Round", "Hard Round", "Detail"]
    assert data["names"][-1] == "Texture"
    assert data["target"]["brushSize"] == 42
    assert data["target"]["brushBlendMode"] == "multiply"


def test_pixel_brush_preset_roundtrips_pixel_flag():
    data = run_node(
        f"""
        import {{ loadBrushPresets, captureBrushPreset, applyBrushPreset }} from {json.dumps(PRESETS)};
        const loaded = loadBrushPresets({{ getItem: () => null, setItem: () => {{}} }});
        const pixel = loaded.find(item => item.id === 'pixel-1px');
        const target = {{}};
        applyBrushPreset(target, pixel);
        const captured = captureBrushPreset(
          {{brushSize:1,brushOpacity:100,brushFlow:100,brushSoftness:0,brushSpacing:1,brushSmoothing:0,brushBlendMode:'source-over',brushPixel:true}},
          'Px',
        );
        console.log(JSON.stringify({{
          names: loaded.map(item => item.name),
          pixelSize: pixel && pixel.size,
          appliedPixel: target.brushPixel,
          capturedPixel: captured.pixel,
        }}));
        """
    )
    assert "Pixel" in data["names"]
    assert data["pixelSize"] == 1
    assert data["appliedPixel"] is True
    assert data["capturedPixel"] is True


def test_stroke_pipeline_has_pixel_stamping():
    source = (ROOT / "static/js/editor/stroke-pipeline.js").read_text()
    assert "function pixelDab(" in source
    assert "function pixelLine(" in source
    assert "state.brushPixel" in source
    assert "let pixelLast = null;" in source
    # Size-aware square stamping (integer, no anti-aliasing), snapped to a
    # grid whose cell is the brush size so consecutive dabs connect cleanly.
    assert "const pixelSize = Math.max(1, Math.round(diameter));" in source
    assert "const gx = Math.floor(x / s) * s;" in source
    assert "const gy = Math.floor(y / s) * s;" in source
    assert "ctx.fillRect(gx, gy, s, s);" in source
    # The connecting line walks whole grid cells, never half-cells.
    assert "let cx = Math.floor(Math.round(x0) / s);" in source
    assert "const cx1 = Math.floor(Math.round(x1) / s);" in source
    assert "ctx.fillRect(cx * s, cy * s, s, s);" in source
    state = (ROOT / "static/js/editor/state.js").read_text()
    assert "brushPixel: false" in state


def test_pixel_brush_does_not_force_a_grid_overlay():
    guides = (ROOT / "static/js/editor/precision-guides.js").read_text()
    # The Pixel tool must not paint a blue grid over the whole document (it
    # read as an unwanted blue tint); the grid follows View > Grid only.
    assert "state.gridVisible || pixelBrush" not in guides
    assert "122, 208, 255" not in guides
    assert "if (state.gridVisible) {" in guides


def test_pixel_brush_snaps_only_while_shift_is_held():
    source = (ROOT / "static/js/editor/stroke-pipeline.js").read_text()
    # Direction snap helper stays.
    assert "function snapDirection(dx, dy)" in source
    assert "Math.round(Math.atan2(dy, dx) / step) * step" in source
    assert "let pixelAnchor = null;" in source
    # A previous stroke's end is remembered so Shift+click connects from it.
    assert "let pixelClickAnchor = null;" in source
    assert "pixelAnchor = (sample && sample.shift && pixelClickAnchor)" in source
    assert "if (pixelLast) pixelClickAnchor = { x: pixelLast.x, y: pixelLast.y };" in source
    # Shift held => snap to the nearest axis/diagonal.
    assert "if (sample.shift) {" in source
    assert "const dir = snapDirection(ddx, ddy);" in source
    assert "pixelLine(target.ctx, pixelAnchor.x, pixelAnchor.y, end.x, end.y," in source
    # Freehand by default (no shift).
    assert "if (pixelLast) pixelLine(target.ctx, pixelLast.x, pixelLast.y, px, py," in source
    # The shift modifier has to reach the pipeline from the DOM event.
    stroke = (ROOT / "static/js/editor/tools/stroke.js").read_text()
    assert "shift: !!e?.shiftKey" in stroke
