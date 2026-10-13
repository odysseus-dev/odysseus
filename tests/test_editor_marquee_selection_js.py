import json
import re
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASK_MODULE = (ROOT / "static/js/editor/selection-mask.js").as_uri()
TOOL_MODULE = (ROOT / "static/js/editor/tools/marquee.js").as_uri()
STATE_MODULE = (ROOT / "static/js/editor/state.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{
          createMarqueeMask, mergeSelectionMasks, normalizeSelectionRect,
          normalizeConstrainedSelectionRect,
          selectionMaskForLayer, selectionMaskToDocument,
          paintSelectionBoundary, selectionBoundaryPixels, selectionMaskBounds,
          selectionMaskContains, translateSelectionMask, transformSelectionMask,
        }} from {json.dumps(MASK_MODULE)};
        import {{ createMarqueeTool }} from {json.dumps(TOOL_MODULE)};
        import {{ state }} from {json.dumps(STATE_MODULE)};

        let nextId = 0;
        function canvas(width=0, height=0, tag='canvas') {{
          const value = {{width, height, tag, calls:[], data:new Uint8ClampedArray(Math.max(0, width * height * 4))}};
          const ctx = {{
            fillStyle:'', globalCompositeOperation:'source-over',
            beginPath() {{ value.calls.push(['begin']); }},
            ellipse(...args) {{ value.calls.push(['ellipse', ...args]); }},
            fill() {{ value.calls.push(['fill']); }},
            fillRect(...args) {{ value.calls.push(['fillRect', ...args]); }},
            drawImage(...args) {{ value.calls.push(['drawImage', this.globalCompositeOperation, args[0]?.tag, ...args.slice(1)]); }},
            save() {{ value.calls.push(['save']); }},
            restore() {{ value.calls.push(['restore']); }},
            translate(...args) {{ value.calls.push(['translate', ...args]); }},
            rotate(...args) {{ value.calls.push(['rotate', ...args]); }},
            scale(...args) {{ value.calls.push(['scale', ...args]); }},
            getImageData(x=0, y=0, w=value.width, h=value.height) {{
              if (x === 0 && y === 0 && w === value.width && h === value.height) return {{data:value.data}};
              const out = new Uint8ClampedArray(w * h * 4);
              for (let py=0; py<h; py++) for (let px=0; px<w; px++) {{
                const src = ((y + py) * value.width + x + px) * 4;
                out.set(value.data.slice(src, src + 4), (py * w + px) * 4);
              }}
              return {{data:out}};
            }},
            createImageData(w, h) {{ return {{data:new Uint8ClampedArray(w * h * 4), width:w, height:h}}; }},
            putImageData(image, x, y) {{ value.calls.push(['putImageData', x, y]); value.output = image.data; }},
          }};
          value.getContext = () => ctx;
          value.getBoundingClientRect = () => ({{left:0, top:0, width:value.width, height:value.height}});
          return value;
        }}
        globalThis.document = {{createElement:() => canvas(0, 0, `temp-${{++nextId}}`)}};
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


def test_marquee_geometry_and_shapes_are_document_bounded():
    result = run_node(
        """
        const reversed = normalizeSelectionRect({x:80,y:70},{x:20,y:-10},100,80,false);
        const square = normalizeSelectionRect({x:10,y:10},{x:70,y:35},100,80,true);
        const rect = createMarqueeMask(100,80,{x:5,y:6,w:20,h:30},'rectangle');
        const ellipse = createMarqueeMask(100,80,{x:10,y:20,w:40,h:20},'ellipse');
        console.log(JSON.stringify({reversed,square,rect:rect.calls,ellipse:ellipse.calls}));
        """
    )

    assert result["reversed"] == {"x": 20, "y": 0, "w": 60, "h": 70}
    assert result["square"] == {"x": 10, "y": 10, "w": 25, "h": 25}
    assert ["fillRect", 5, 6, 20, 30] in result["rect"]
    assert ["ellipse", 30, 30, 20, 10, 0, 0, 2 * 3.141592653589793] in result["ellipse"]


def test_marquee_fixed_ratio_and_size_geometry_are_exact_and_bounded():
    result = run_node(
        """
        const ratio = normalizeConstrainedSelectionRect(
          {x:10,y:10},{x:90,y:40},100,80,
          {mode:'ratio',ratioWidth:4,ratioHeight:3},
        );
        const reverse = normalizeConstrainedSelectionRect(
          {x:90,y:70},{x:20,y:20},100,80,
          {mode:'ratio',ratioWidth:16,ratioHeight:9},
        );
        const fixed = normalizeConstrainedSelectionRect(
          {x:85,y:70},{x:85,y:70},100,80,
          {mode:'size',fixedWidth:30,fixedHeight:25},
        );
        const oversized = normalizeConstrainedSelectionRect(
          {x:20,y:30},{x:20,y:30},100,80,
          {mode:'size',fixedWidth:300,fixedHeight:250},
        );
        console.log(JSON.stringify({ratio,reverse,fixed,oversized}));
        """
    )

    assert result["ratio"] == {"x": 10, "y": 10, "w": 80, "h": 60}
    assert result["reverse"]["x"] >= 0
    assert result["reverse"]["y"] >= 0
    assert round(result["reverse"]["w"] / result["reverse"]["h"], 5) == round(16 / 9, 5)
    assert result["fixed"] == {"x": 70, "y": 55, "w": 30, "h": 25}
    assert result["oversized"] == {"x": 0, "y": 0, "w": 100, "h": 80}


def test_selection_masks_combine_and_convert_between_spaces():
    result = run_node(
        """
        const current = canvas(100,80,'current');
        const incoming = canvas(100,80,'incoming');
        const subtract = mergeSelectionMasks(current,incoming,'subtract');
        const intersect = mergeSelectionMasks(current,incoming,'intersect');
        const layerMask = canvas(20,10,'layer-mask');
        const doc = selectionMaskToDocument(layerMask,'layer',{x:7,y:9},100,80);
        const layer = selectionMaskForLayer(doc,'document',{x:7,y:9},20,10);
        console.log(JSON.stringify({
          subtract:subtract.calls, intersect:intersect.calls,
          doc:doc.calls, layer:layer.calls,
        }));
        """
    )

    assert result["subtract"][-1][:3] == ["drawImage", "destination-out", "incoming"]
    assert result["intersect"][-1][:3] == ["drawImage", "destination-in", "incoming"]
    assert result["doc"][-1] == ["drawImage", "source-over", "layer-mask", 7, 9]
    assert result["layer"][-1] == ["drawImage", "source-over", "temp-3", -7, -9]


def test_marquee_tool_commits_document_space_selection():
    result = run_node(
        """
        const main = canvas(100,80,'main');
        const layer = {id:'layer-1'};
        Object.assign(state, {
          mainCanvas:main, imgWidth:100, imgHeight:80,
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id,{x:0,y:0}]]),
          tool:'marquee',
          wandMask:null, wandLayerId:null, wandMaskSpace:'layer', wandMode:'replace',
          marqueeShape:'ellipse', lassoPoints:[], lassoActive:false,
        });
        const labels = [];
        let composites = 0;
        const tool = createMarqueeTool({
          activeLayer:() => layer,
          saveState:label => labels.push(label),
          composite:() => composites++, drawOverlay:() => {}, syncSelectionUi:() => {},
        });
        tool.begin({clientX:10,clientY:20,shiftKey:false,altKey:false});
        tool.drag({clientX:50,clientY:60,shiftKey:false,preventDefault() {}});
        tool.end({});
        console.log(JSON.stringify({
          labels, composites, space:state.wandMaskSpace, layerId:state.wandLayerId,
          size:[state.wandMask.width,state.wandMask.height], calls:state.wandMask.calls,
        }));
        """
    )

    assert result["labels"] == ["Ellipse selection"]
    assert result["space"] == "document"
    assert result["layerId"] == "layer-1"
    assert result["size"] == [100, 80]
    assert any(call[0] == "ellipse" for call in result["calls"])


def test_selection_boundary_geometry_translation_and_painting():
    result = run_node(
        """
        const mask = canvas(5,4,'mask');
        for (const [x,y] of [[1,1],[2,1],[3,1],[1,2],[2,2],[3,2]]) {
          mask.data[(y * mask.width + x) * 4 + 3] = 255;
        }
        const pixels = selectionBoundaryPixels(mask);
        const output = canvas(5,4,'output');
        paintSelectionBoundary(output.getContext('2d'), pixels, 5, 4, 2);
        const moved = translateSelectionMask(mask, 2, -1, 5, 4);
        console.log(JSON.stringify({
          bounds:selectionMaskBounds(mask),
          inside:selectionMaskContains(mask,2,2),
          outside:selectionMaskContains(mask,0,0),
          pixels,
          opaque:[...output.output].filter((_, i) => i % 4 === 3 && output.output[i] === 255).length,
          moved:moved.calls,
        }));
        """
    )

    assert result["bounds"] == {"x": 1, "y": 1, "width": 3, "height": 2}
    assert result["inside"] is True
    assert result["outside"] is False
    assert len(result["pixels"]) == 6
    assert result["opaque"] == 6
    assert result["moved"][-1] == ["drawImage", "source-over", "mask", 2, -1]


def test_selection_transform_is_affine_and_keeps_source_immutable():
    result = run_node(
        """
        const mask = canvas(100,80,'mask');
        const transformed = transformSelectionMask(
          mask,
          {x:10,y:20,width:30,height:24},
          {centerX:60,centerY:35,width:45,height:36,rotation:90,flipH:true,flipV:false},
          100,
          80,
        );
        console.log(JSON.stringify({
          sourceCalls:mask.calls,
          size:[transformed.width,transformed.height],
          calls:transformed.calls,
        }));
        """
    )

    assert result["sourceCalls"] == []
    assert result["size"] == [100, 80]
    assert ["translate", 60, 35] in result["calls"]
    assert ["rotate", 3.141592653589793 / 2] in result["calls"]
    assert ["scale", -1, 1] in result["calls"]
    assert ["drawImage", "source-over", "mask", 10, 20, 30, 24, -22.5, -18, 45, 36] in result["calls"]


def test_marquee_is_wired_through_editor_and_offline_graph():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    toolbar = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
    controls = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")

    assert "{ id: 'marquee', label: 'Marquee'" in toolbar
    assert 'id="ge-marquee-section"' in controls
    assert "if (state.tool === 'marquee') return _marqueeTool.begin(e);" in editor
    assert "if (state.marqueeActive || state.selectionMoving) return _marqueeTool.drag(e);" in editor
    assert "if (state.marqueeActive || state.selectionMoving) return _marqueeTool.end(e);" in editor
    assert "space: state.wandMaskSpace || 'layer'" in editor
    assert "/static/js/editor/selection-mask.js" in service_worker
    assert "/static/js/editor/tools/marquee.js" in service_worker
    assert 'data-selection-action="transform"' in (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
    assert re.search(r"const CACHE_NAME = 'odysseus-v\d+-[A-Za-z0-9_-]+';", service_worker)
