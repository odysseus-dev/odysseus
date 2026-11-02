import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/composite-helpers.js").as_uri()
STROKE_MODULE = (ROOT / "static/js/editor/stroke-pipeline.js").as_uri()


def test_true_layer_masks_clip_in_document_coordinates():
    script = textwrap.dedent(
        f"""
        import {{ renderWithLayerMasks }} from {json.dumps(MODULE)};
        const operations = [];
        const ctx = {{
          _gco:'source-over',
          set globalCompositeOperation(value) {{ this._gco=value; operations.push(['gco', value]); }},
          get globalCompositeOperation() {{ return this._gco; }},
          drawImage(...args) {{ operations.push(['draw', ...args.map(x => x?.tag || x)]); }},
        }};
        globalThis.document = {{ createElement: () => ({{ width:0, height:0, tag:'out', getContext:() => ctx }}) }};
        const source = {{ width:20, height:10, tag:'source' }};
        const selection = {{ mode:'selection', visible:true, canvas:{{tag:'selection'}} }};
        const hidden = {{ mode:'layer', visible:false, canvas:{{tag:'hidden'}} }};
        const layerMask = {{ mode:'layer', space:'layer', visible:true, offset:{{x:3,y:-2}}, canvas:{{tag:'layer-mask'}} }};
        const out = renderWithLayerMasks(source, {{masks:[selection, hidden, layerMask]}}, {{x:7,y:9}});
        console.log(JSON.stringify({{width:out.width,height:out.height,operations}}));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    data = json.loads(result.stdout)

    assert (data["width"], data["height"]) == (20, 10)
    assert ["draw", "source", 0, 0] in data["operations"]
    assert ["gco", "destination-in"] in data["operations"]
    assert ["draw", "layer-mask", 3, -2] in data["operations"]
    assert not any("selection" in op or "hidden" in op for op in data["operations"])


def test_layer_panel_mutations_are_history_backed():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text(encoding="utf-8")

    assert "`${layer.visible ? 'Hide' : 'Show'} layer" in panel
    assert "for (const item of targets) item.visible = visible" in panel
    assert "`Change opacity of \"${layer.name}\"`" in panel
    assert "const targets = rowTargets(layer)" in panel
    assert "for (const item of targets) item.opacity = nextOpacity" in panel
    assert "saveState(`Rename layer" in panel
    assert "openLayerLockMenu(layer, lockBtn)" in panel
    assert "item.locks[option.key] = next" in panel
    assert "saveState(`${adj.visible ? 'Hide' : 'Show'}" in panel
    assert "saveState(`Change ${adjLayerLabel(adj.type)} opacity`)" in panel
    assert "saveState(`${mk.visible ? 'Hide' : 'Show'} mask" in panel
    assert "ge-mask-link-btn" in panel
    assert "mk.linked = !linked" in panel
    assert "`Change blend mode of \"${active.name}\"`" in panel
    assert "for (const layer of targets) layer.blendMode" in panel
    before_assignment = panel.index("saveState('Reorder layers')")
    assignment = panel.index("state.layers = newLayers", before_assignment)
    assert before_assignment < assignment


def test_preview_and_flatten_share_true_mask_renderer():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")

    assert "return _renderWithLayerMasks(" in editor
    assert "state.layerOffsets.get(layer.id) || { x: 0, y: 0 }" in editor
    assert "function _drawDocumentLayers(ctx, shouldContinue = () => true)" in editor
    assert "_drawDocumentLayers(documentCanvas.getContext('2d'), render.isCurrent);" in editor
    assert "_drawDocumentLayers(ctx);" in editor
    assert "if (mk.mode === 'layer') continue;" in editor
    assert "const selectionMasks = (parent?.masks || []).filter(m => m.mode !== 'layer')" in editor


def test_brush_uses_independently_positioned_layer_mask_origin():
    script = textwrap.dedent(
        f"""
        import {{ createStrokePipeline }} from {json.dumps(STROKE_MODULE)};
        import {{ state }} from {(ROOT / "static/js/editor/state.js").as_uri()!r};
        const operations = [];
        const ctx = {{
          save() {{}}, restore() {{}}, beginPath() {{}},
          createRadialGradient() {{ return {{ addColorStop() {{}} }}; }},
          arc(x, y, radius) {{ operations.push(['stamp', x, y, radius]); }},
          fill() {{}},
        }};
        const layer = {{ id:'layer-1', canvas:{{width:100,height:80}}, ctx, locks:{{}} }};
        const mask = {{
          id:'mask-1', mode:'layer', linked:false, offset:{{x:7,y:-3}},
          canvas:{{width:100,height:80}}, ctx,
        }};
        state.layerOffsets = new Map([['layer-1', {{x:10,y:20}}]]);
        state.tool = 'brush';
        state.brushSize = 12;
        state.brushOpacity = 100;
        state.brushFlow = 100;
        state.brushSoftness = 0;
        state.color = '#000000';
        state.lastX = 50;
        state.lastY = 60;
        const pipeline = createStrokePipeline({{
          activeLayer: () => layer,
          getActiveMaskLayer: () => mask,
          composite: () => {{}},
        }});
        pipeline.beginStroke({{x:50,y:60,pressure:1}}, 'brush');
        pipeline.strokeTo({{x:60,y:70,pressure:1}});
        console.log(JSON.stringify({{operations}}));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    data = json.loads(result.stdout)

    assert ["stamp", 33, 43, 6] in data["operations"]
    assert any(op[0] == "stamp" and op[1] <= 43 and op[2] <= 53 for op in data["operations"])
