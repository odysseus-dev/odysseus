"""Regression coverage for Clone / Healing sample-source selection."""

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLONE_MODULE = (ROOT / "static/js/editor/tools/clone.js").as_uri()
PIPELINE_MODULE = (ROOT / "static/js/editor/stroke-pipeline.js").as_uri()
STATE_MODULE = (ROOT / "static/js/editor/state.js").as_uri()


def run_node(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(source)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_clone_can_snapshot_the_rendered_composite_surface():
    result = run_node(f"""
        import {{ createCloneTool }} from {json.dumps(CLONE_MODULE)};
        import {{ state }} from {json.dumps(STATE_MODULE)};
        const draws = [];
        const sourceDraws = [];
        const sourceLayer = {{ id:'layer-1', canvas:{{width:10,height:10}} }};
        const composite = {{ tag:'composite', width:20, height:20 }};
        const snap = {{ width:0, height:0, getContext:() => ({{ drawImage: source => draws.push(source.tag) }}) }};
        globalThis.document = {{ createElement: () => snap }};
        state.mainCanvas = {{ width:20, height:20, getBoundingClientRect:() => ({{left:0,top:0,width:20,height:20}}) }};
        state.layers = [sourceLayer];
        state.activeLayerId = 'layer-1';
        state.layerOffsets = new Map();
        state.cloneSampleMode = 'composite';
        state.cloneSourceX = null;
        state.cloneSourceY = null;
        state.cloneSourceSnapshot = null;
        state.tool = 'clone';
        const tool = createCloneTool({{
          activeLayer: () => sourceLayer,
          saveState: () => {{}},
          beginStroke: () => {{}},
          showToast: () => {{}},
          getSampleCanvas: () => composite,
        }});
        tool.begin({{type:'mousedown', clientX:4, clientY:5, altKey:true}});
        tool.begin({{type:'mousedown', clientX:10, clientY:10, altKey:false, pressure:1}});
        console.log(JSON.stringify({{draws, width:snap.width, height:snap.height}}));
    """)
    assert result == {"draws": ["composite"], "width": 20, "height": 20}


def test_clone_stamp_converts_document_source_to_active_layer_coordinates():
    result = run_node(f"""
        import {{ createStrokePipeline }} from {json.dumps(PIPELINE_MODULE)};
        import {{ state }} from {json.dumps(STATE_MODULE)};
        const draws = [];
        const sourceDraws = [];
        const ctx = {{
          save() {{}}, restore() {{}},
          drawImage(...args) {{ draws.push(args); }},
          createRadialGradient: () => ({{ addColorStop() {{}} }}),
          fillRect() {{}},
        }};
        const snapshot = {{ width:100, height:100 }};
        globalThis.document = {{ createElement: () => ({{
          width:0, height:0,
          getContext: () => ({{
            drawImage(...args) {{ sourceDraws.push(args); }},
            createRadialGradient: () => ({{ addColorStop() {{}} }}),
            fillRect() {{}},
          }}),
        }}) }};
        const layer = {{ id:'layer-1', canvas:{{width:100,height:100}}, ctx, locks:{{}} }};
        state.layers = [layer];
        state.layerOffsets = new Map([['layer-1', {{x:20,y:30}}]]);
        state.cloneSourceSnapshot = snapshot;
        state.cloneSourceX = 50;
        state.cloneSourceY = 60;
        state.cloneSourceOffsetX = 20;
        state.cloneSourceOffsetY = 30;
        state.cloneStrokeStartX = 70;
        state.cloneStrokeStartY = 80;
        state.tool = 'clone';
        state.brushSize = 10;
        state.cloneOpacity = 100;
        state.cloneFlow = 100;
        state.cloneSoftness = 0;
        state.pressureSize = false;
        state.pressureOpacity = false;
        state.pressureFlow = false;
        const pipeline = createStrokePipeline({{
          activeLayer: () => layer,
          getActiveMaskLayer: () => null,
          composite: () => {{}},
        }});
        pipeline.beginStroke({{x:70,y:80,pressure:1}}, 'heal');
        console.log(JSON.stringify({{draws, sourceDraws}}));
    """)
    assert result["draws"]
    # The source sample starts at document (50, 60), then is converted to
    # local layer coordinates by subtracting the layer's (20, 30) offset.
    assert result["sourceDraws"][0][1:5] == [25, 25, 10, 10]
