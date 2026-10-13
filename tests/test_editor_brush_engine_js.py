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


def test_sampler_emits_even_spacing_and_flushes_endpoint():
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
    assert [round(item["x"]) for item in data] == [0, 5, 10, 15, 20, 23]
    assert data[-1]["pressure"] == 0.25


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
