import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/text-layer.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{ normalizeTextData, rasterizeTextLayer, renderTextLayer }} from {json.dumps(MODULE)};
        let id = 0;
        function canvas(tag='canvas') {{
          const value = {{tag, width:0, height:0, draws:[], fills:[], strokes:[]}};
          const ctx = {{
            font:'', textBaseline:'', textAlign:'left', fillStyle:'', strokeStyle:'',
            lineWidth:0, lineJoin:'', imageSmoothingEnabled:false, imageSmoothingQuality:'low',
            measureText(text) {{ return {{width:String(text).length * 10}}; }},
            fillText(...args) {{ value.fills.push(args); }},
            strokeText(...args) {{ value.strokes.push(args); }},
            drawImage(...args) {{ value.draws.push(args.map(item => item?.tag || item)); }},
            clearRect() {{}}, translate() {{}}, rotate() {{}}, scale() {{}},
          }};
          value.getContext = () => ctx;
          return value;
        }}
        globalThis.document = {{createElement:() => canvas(`canvas-${{++id}}`)}};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_text_renderer_keeps_editable_metadata_and_builds_raster_cache():
    result = run_node(
        """
        const target = canvas('target');
        const layer = {
          canvas:target, ctx:target.getContext('2d'),
          text:{
            content:'Hello\\nWorld', fontFamily:'Georgia', fontSize:20,
            fontWeight:'700', fontStyle:'italic', align:'center', color:'#ff0000',
            strokeColor:'#000000', strokeWidth:2, lineHeight:1.5,
            transform:{scaleX:2,scaleY:1.5,rotation:15,flipH:true,flipV:false},
          },
        };
        renderTextLayer(layer);
        console.log(JSON.stringify({
          kind:layer.kind, content:layer.text.content, font:layer.text.fontFamily,
          transform:layer.text.transform, size:[layer.canvas.width,layer.canvas.height],
          finalDraw:layer.canvas.draws.at(-1),
        }));
        """
    )

    assert result["kind"] == "text"
    assert result["content"] == "Hello\nWorld"
    assert result["font"] == "Georgia"
    assert result["transform"] == {
        "scaleX": 2, "scaleY": 1.5, "rotation": 15, "flipH": True, "flipV": False
    }
    assert result["size"][0] > 100
    assert result["size"][1] > 80
    assert result["finalDraw"][1:] == [0, 0]


def test_text_data_normalization_bounds_invalid_values():
    result = run_node(
        """
        console.log(JSON.stringify(normalizeTextData({
          content:42, fontSize:-5, align:'diagonal', lineHeight:99,
          transform:{scaleX:0,scaleY:'bad',rotation:'bad',flipV:1},
        })));
        """
    )

    assert result["content"] == "42"
    assert result["fontSize"] == 1
    assert result["align"] == "left"
    assert result["lineHeight"] == 5
    assert result["letterSpacing"] == 0
    assert result["frameWidth"] == 320
    assert result["frameHeight"] == 0
    assert result["verticalAlign"] == "top"
    assert result["transform"]["scaleX"] == 0.01
    assert result["transform"]["scaleY"] == 1
    assert result["transform"]["rotation"] == 0
    assert result["transform"]["flipV"] is True


def test_rasterize_text_layer_clears_retained_metadata_only_for_text():
    result = run_node(
        """
        const text = {kind:'text', text:{content:'Still editable'}};
        const raster = {kind:'raster', text:null};
        console.log(JSON.stringify({
          changed:rasterizeTextLayer(text), text,
          rasterChanged:rasterizeTextLayer(raster), raster,
        }));
        """
    )

    assert result["changed"] is True
    assert result["text"] == {"kind": "raster", "text": None}
    assert result["rasterChanged"] is False
    assert result["raster"] == {"kind": "raster", "text": None}


def test_text_tool_is_retained_across_editor_subsystems():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    toolbar = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
    shortcuts = (ROOT / "static/js/editor/tool-shortcuts.js").read_text(encoding="utf-8")
    controls = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
    codec = (ROOT / "static/js/editor/document-codec.js").read_text(encoding="utf-8")
    geometry = (ROOT / "static/js/editor/document-geometry.js").read_text(encoding="utf-8")
    transform = (ROOT / "static/js/editor/tools/transform-session.js").read_text(encoding="utf-8")
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text(encoding="utf-8")
    merge = (ROOT / "static/js/editor/wire-merge-buttons.js").read_text(encoding="utf-8")

    assert "{ id: 'text', label: 'Text'" in toolbar
    assert "t.key = TOOL_SHORTCUTS[t.id]" in toolbar
    assert "text: 'T'" in shortcuts
    assert 'id="ge-text-section"' in controls
    assert "if (state.tool === 'text') return _placeText(e);" in editor
    assert "kind: l.kind || 'raster'" in editor
    assert "text: _cloneDocumentValue(l.text, null)" in editor
    assert "kind: layer.kind || 'raster'" in codec
    assert "text: cloneDocumentValue(layer.text, null)" in codec
    assert "scaleTextLayer(layer, scaleX, scaleY)" in geometry
    assert "rotateTextLayer(layer, normalized)" in geometry
    assert "renderTextLayer(layer)" in transform
    assert 'id="ge-text-rasterize"' in controls
    assert "layer.kind = 'raster'" in editor
    assert "layer.text = null" in editor
    assert "rasterizeTextLayer(layer);" in panel
    assert "rasterizeTextLayer(layer);" in merge
    assert "_rasterizeTextLayer(layer)" in editor
    assert 'id="ge-text-letter-spacing"' in controls
    assert 'id="ge-text-frame-width"' in controls
    assert 'id="ge-text-frame-height"' in controls
    assert 'id="ge-text-vertical-align"' in controls
    assert 'id="ge-text-auto-width"' in controls
    assert 'autoWidth: !!document.getElementById(\'ge-text-auto-width\')?.checked' in editor
    assert "createTextEditOverlay" in editor


def test_text_auto_width_control_does_not_share_move_tool_selector():
    controls = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
    assert 'class="ge-text-auto-width-option"' in controls
    assert 'class="ge-auto-select-option ge-text-auto-width-option"' not in controls
