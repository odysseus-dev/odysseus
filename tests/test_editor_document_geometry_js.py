import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/document-geometry.js").as_uri()


def run_node(body: str):
    source = textwrap.dedent(
        f"""
        import {{
          cropDocument, flipDocument, resizeCanvasDocument,
          resizeImageDocument, rotateDocument,
        }} from {json.dumps(MODULE)};

        let tempId = 0;
        function canvas(tag, width, height) {{
          const value = {{ tag, width, height, draws:[], clears:[] }};
          const ctx = {{
            imageSmoothingEnabled:false,
            imageSmoothingQuality:'low',
            font:'', textBaseline:'', textAlign:'left', fillStyle:'', strokeStyle:'', lineWidth:0,
            measureText(text) {{ return {{width:String(text).length * 8}}; }},
            fillText() {{}}, strokeText() {{}},
            drawImage(...args) {{ value.draws.push(args); }},
            clearRect(...args) {{ value.clears.push(args); }},
            save() {{}}, restore() {{}}, translate() {{}}, rotate() {{}}, scale() {{}}, setTransform() {{}},
          }};
          value.getContext = () => ctx;
          return value;
        }}
        globalThis.document = {{ createElement: () => canvas(`temp-${{++tempId}}`, 0, 0) }};

        function holder(id, tag, width, height, masks=[]) {{
          const c = canvas(tag, width, height);
          return {{ id, name:id, canvas:c, ctx:c.getContext('2d'), masks }};
        }}
        function mask(id, tag, width, height, mode, space) {{
          const c = canvas(tag, width, height);
          return {{ id, name:id, canvas:c, ctx:c.getContext('2d'), mode, space, visible:true }};
        }}
        function sourceDraw(holderValue) {{
          const sourceCanvas = holderValue?.canvas || holderValue;
          const rendered = sourceCanvas.draws.at(-1)?.[0];
          const args = rendered?.draws?.[0] || [];
          return {{ source:args[0]?.tag, args:args.slice(1) }};
        }}

        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_crop_updates_layer_and_document_mask_coordinate_spaces():
    result = run_node(
        """
        const layerMask = mask('mask-layer', 'layer-mask', 40, 30, 'layer', 'layer');
        const selectionMask = mask('mask-selection', 'selection-mask', 100, 80, 'selection', 'document');
        const layer = holder('layer-1', 'pixels', 40, 30, [layerMask, selectionMask]);
        layer.activeMaskId = layerMask.id;
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100, imgHeight:80, mainCanvas:main, mainCtx:main.getContext('2d'),
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id, {x:10,y:5}]]),
          lassoPoints:[{x:25,y:15},{x:45,y:15},{x:45,y:30},{x:25,y:30}],
          guides:{vertical:[10,25,75],horizontal:[5,20,60]},
          wandMask:null, wandLastSeed:null,
        };
        const crop = cropDocument(state, {x:20,y:10,w:50,h:40});
        console.log(JSON.stringify({
          crop, doc:[state.imgWidth,state.imgHeight], main:[main.width,main.height],
          layer:[layer.canvas.width,layer.canvas.height], offset:state.layerOffsets.get(layer.id),
          layerMask:[layerMask.canvas.width,layerMask.canvas.height],
          selectionMask:[selectionMask.canvas.width,selectionMask.canvas.height],
          layerDraw:sourceDraw(layer), layerMaskDraw:sourceDraw(layerMask),
          selectionMaskDraw:sourceDraw(selectionMask),
          lasso:state.lassoPoints, guides:state.guides, activeMask:state.maskCanvas.tag,
        }));
        """
    )

    assert result["crop"] == {"x": 20, "y": 10, "width": 50, "height": 40}
    assert result["doc"] == [50, 40]
    assert result["main"] == [50, 40]
    assert result["layer"] == [50, 40]
    assert result["offset"] == {"x": 0, "y": 0}
    assert result["layerMask"] == [50, 40]
    assert result["selectionMask"] == [50, 40]
    assert result["layerDraw"] == {"source": "pixels", "args": [-10, -5]}
    assert result["layerMaskDraw"] == {"source": "layer-mask", "args": [-10, -5]}
    assert result["selectionMaskDraw"] == {"source": "selection-mask", "args": [-20, -10]}
    assert result["lasso"] == [
        {"x": 5, "y": 5}, {"x": 25, "y": 5},
        {"x": 25, "y": 20}, {"x": 5, "y": 20},
    ]
    assert result["guides"] == {"vertical": [5], "horizontal": [10]}
    assert result["activeMask"] == "layer-mask"


def test_image_resize_resamples_layers_masks_offsets_and_selection():
    result = run_node(
        """
        const layerMask = mask('mask-layer', 'layer-mask', 40, 30, 'layer', 'layer');
        const selectionMask = mask('mask-selection', 'selection-mask', 100, 80, 'selection', 'document');
        const layer = holder('layer-1', 'pixels', 40, 30, [layerMask, selectionMask]);
        layer.activeMaskId = selectionMask.id;
        const main = canvas('main', 100, 80);
        const wand = canvas('wand', 40, 30);
        const state = {
          imgWidth:100, imgHeight:80, mainCanvas:main, mainCtx:main.getContext('2d'),
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id, {x:10,y:5}]]),
          lassoPoints:[{x:10,y:20},{x:30,y:20},{x:30,y:40}],
          guides:{vertical:[10,50],horizontal:[20,70]},
          wandMask:wand, wandLayerId:layer.id, wandLastSeed:{x:15,y:20,mode:'replace'},
        };
        const resized = resizeImageDocument(state, 200, 120);
        console.log(JSON.stringify({
          resized, doc:[state.imgWidth,state.imgHeight], layer:[layer.canvas.width,layer.canvas.height],
          layerMask:[layerMask.canvas.width,layerMask.canvas.height],
          selectionMask:[selectionMask.canvas.width,selectionMask.canvas.height],
          wand:[wand.width,wand.height], offset:state.layerOffsets.get(layer.id),
          lasso:state.lassoPoints, guides:state.guides,
          seed:state.wandLastSeed, activeMask:state.maskCanvas.tag,
        }));
        """
    )

    assert result["resized"] == {
        "width": 200, "height": 120, "scaleX": 2, "scaleY": 1.5
    }
    assert result["doc"] == [200, 120]
    assert result["layer"] == [80, 45]
    assert result["layerMask"] == [80, 45]
    assert result["selectionMask"] == [200, 120]
    assert result["wand"] == [80, 45]
    assert result["offset"] == {"x": 20, "y": 8}
    assert result["lasso"] == [
        {"x": 20, "y": 30}, {"x": 60, "y": 30}, {"x": 60, "y": 60}
    ]
    assert result["guides"] == {"vertical": [20, 100], "horizontal": [30, 105]}
    assert result["seed"] == {"x": 30, "y": 30, "mode": "replace"}
    assert result["activeMask"] == "selection-mask"


def test_canvas_resize_only_changes_document_space_masks():
    result = run_node(
        """
        const layerMask = mask('mask-layer', 'layer-mask', 40, 30, 'layer', 'layer');
        const selectionMask = mask('mask-selection', 'selection-mask', 100, 80, 'selection', 'document');
        const layer = holder('layer-1', 'pixels', 40, 30, [layerMask, selectionMask]);
        layer.activeMaskId = layerMask.id;
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100, imgHeight:80, mainCanvas:main, mainCtx:main.getContext('2d'),
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id, {x:10,y:5}]]),
          lassoPoints:[{x:10,y:10},{x:90,y:10},{x:90,y:70},{x:10,y:70}],
          guides:{vertical:[10,90],horizontal:[20,70]},
          wandMask:null, wandLastSeed:null,
        };
        resizeCanvasDocument(state, 60, 50);
        console.log(JSON.stringify({
          doc:[state.imgWidth,state.imgHeight], layer:[layer.canvas.width,layer.canvas.height],
          layerMask:[layerMask.canvas.width,layerMask.canvas.height],
          selectionMask:[selectionMask.canvas.width,selectionMask.canvas.height],
          offset:state.layerOffsets.get(layer.id), lasso:state.lassoPoints, guides:state.guides,
        }));
        """
    )

    assert result["doc"] == [60, 50]
    assert result["layer"] == [40, 30]
    assert result["layerMask"] == [40, 30]
    assert result["selectionMask"] == [60, 50]
    assert result["offset"] == {"x": 10, "y": 5}
    assert result["guides"] == {"vertical": [10], "horizontal": [20]}
    assert all(0 <= point["x"] <= 60 and 0 <= point["y"] <= 50 for point in result["lasso"])


def test_canvas_resize_anchor_shifts_layers_and_document_space_state():
    result = run_node(
        """
        const layerMask = mask('mask-layer', 'layer-mask', 40, 30, 'layer', 'layer');
        const documentMask = mask('mask-document', 'document-mask', 100, 80, 'selection', 'document');
        const layer = holder('layer-1', 'pixels', 40, 30, [layerMask, documentMask]);
        const main = canvas('main', 100, 80);
        const selection = canvas('selection', 100, 80);
        const state = {
          imgWidth:100, imgHeight:80, mainCanvas:main, mainCtx:main.getContext('2d'),
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id, {x:10,y:5}]]),
          lassoPoints:[{x:20,y:10},{x:40,y:10},{x:40,y:30}],
          guides:{vertical:[10,90],horizontal:[20,70]},
          savedSelections:[{canvas:selection}], wandMask:null, wandLastSeed:{x:30,y:25,mode:'replace'},
        };
        const resized = resizeCanvasDocument(state, 140, 120, {anchorX:0.5, anchorY:0.5});
        console.log(JSON.stringify({
          resized, offset:state.layerOffsets.get(layer.id),
          layerMask:[layerMask.canvas.width,layerMask.canvas.height], documentMask:[documentMask.canvas.width,documentMask.canvas.height],
          documentMaskDraw:sourceDraw(documentMask.canvas), selectionDraw:sourceDraw(selection),
          lasso:state.lassoPoints, guides:state.guides, seed:state.wandLastSeed,
        }));
        """
    )

    assert result["resized"] == {"width": 140, "height": 120}
    assert result["offset"] == {"x": 30, "y": 25}
    assert result["layerMask"] == [40, 30]
    assert result["documentMask"] == [140, 120]
    assert result["documentMaskDraw"]["args"] == [20, 20]
    assert result["selectionDraw"]["args"] == [20, 20]
    assert result["lasso"] == [{"x": 40, "y": 30}, {"x": 60, "y": 30}, {"x": 60, "y": 50}]
    assert result["guides"] == {"vertical": [30, 110], "horizontal": [40, 90]}
    assert result["seed"] == {"x": 50, "y": 45, "mode": "replace"}


def test_rotate_and_flip_keep_masks_offsets_and_selections_aligned():
    result = run_node(
        """
        const layerMask = mask('mask-layer', 'layer-mask', 40, 30, 'layer', 'layer');
        const selectionMask = mask('mask-selection', 'selection-mask', 100, 80, 'selection', 'document');
        const layer = holder('layer-1', 'pixels', 40, 30, [layerMask, selectionMask]);
        layer.activeMaskId = layerMask.id;
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100, imgHeight:80, mainCanvas:main, mainCtx:main.getContext('2d'),
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id, {x:10,y:5}]]),
          lassoPoints:[{x:20,y:10},{x:30,y:10},{x:30,y:20}],
          guides:{vertical:[20,70],horizontal:[10,60]},
          wandMask:canvas('wand', 40, 30), wandLayerId:layer.id,
          wandLastSeed:{x:20,y:10,mode:'replace'},
        };
        const rotated = rotateDocument(state, 90);
        const afterRotate = {
          result:rotated, layer:[layer.canvas.width,layer.canvas.height],
          layerMask:[layerMask.canvas.width,layerMask.canvas.height],
          selectionMask:[selectionMask.canvas.width,selectionMask.canvas.height],
          wand:[state.wandMask.width,state.wandMask.height],
          offset:state.layerOffsets.get(layer.id), lasso:state.lassoPoints,
          guides:state.guides, seed:state.wandLastSeed,
        };
        const flipped = flipDocument(state, 'h');
        console.log(JSON.stringify({
          afterRotate, flipped, offset:state.layerOffsets.get(layer.id),
          lasso:state.lassoPoints, guides:state.guides, seed:state.wandLastSeed,
          layerMask:[layerMask.canvas.width,layerMask.canvas.height],
          selectionMask:[selectionMask.canvas.width,selectionMask.canvas.height],
        }));
        """
    )

    rotated = result["afterRotate"]
    assert rotated["result"] == {"width": 80, "height": 100, "degrees": 90}
    assert rotated["layer"] == [30, 40]
    assert rotated["layerMask"] == [30, 40]
    assert rotated["selectionMask"] == [80, 100]
    assert rotated["wand"] == [30, 40]
    assert rotated["offset"] == {"x": 45, "y": 10}
    assert rotated["lasso"] == [
        {"x": 70, "y": 20}, {"x": 70, "y": 30}, {"x": 60, "y": 30}
    ]
    assert rotated["guides"] == {"vertical": [20, 70], "horizontal": [20, 70]}
    assert rotated["seed"] == {"x": 70, "y": 20, "mode": "replace"}
    assert result["flipped"] == {"width": 80, "height": 100, "axis": "h"}
    assert result["offset"] == {"x": 5, "y": 10}
    assert result["lasso"] == [
        {"x": 10, "y": 20}, {"x": 10, "y": 30}, {"x": 20, "y": 30}
    ]
    assert result["guides"] == {"vertical": [10, 60], "horizontal": [20, 70]}
    assert result["seed"] == {"x": 10, "y": 20, "mode": "replace"}
    assert result["layerMask"] == [30, 40]
    assert result["selectionMask"] == [80, 100]


def test_group_masks_follow_every_document_geometry_operation():
    result = run_node(
        """
        const groupMask = mask('group-mask', 'group-mask-source', 100, 80, 'group', 'document');
        const layer = holder('layer-1', 'pixels', 100, 80, []);
        const group = {id:'group-1',layerIds:[layer.id],masks:[groupMask],activeMaskId:groupMask.id};
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100,imgHeight:80,mainCanvas:main,mainCtx:main.getContext('2d'),
          layers:[layer],layerGroups:[group],activeGroupId:group.id,activeLayerId:layer.id,
          layerOffsets:new Map([[layer.id,{x:0,y:0}]]),lassoPoints:[],guides:{},wandMask:null,
        };
        cropDocument(state,{x:10,y:5,w:80,h:60});
        const crop={size:[groupMask.canvas.width,groupMask.canvas.height],draw:sourceDraw(groupMask),active:state.maskCanvas.tag};
        resizeImageDocument(state,160,120);
        const imageSize=[groupMask.canvas.width,groupMask.canvas.height];
        resizeCanvasDocument(state,150,110);
        const canvasSize=[groupMask.canvas.width,groupMask.canvas.height];
        rotateDocument(state,90);
        const rotateSize=[groupMask.canvas.width,groupMask.canvas.height];
        flipDocument(state,'h');
        console.log(JSON.stringify({crop,imageSize,canvasSize,rotateSize,flipSize:[groupMask.canvas.width,groupMask.canvas.height]}));
        """
    )
    assert result["crop"] == {
        "size": [80, 60],
        "draw": {"source": "group-mask-source", "args": [-10, -5]},
        "active": "group-mask-source",
    }
    assert result["imageSize"] == [160, 120]
    assert result["canvasSize"] == [150, 110]
    assert result["rotateSize"] == [110, 150]
    assert result["flipSize"] == [110, 150]


def test_placed_source_survives_document_resize_rotate_flip_and_crop():
    result = run_node(
        """
        const source = canvas('immutable-source', 40, 20);
        const layer = holder('placed', 'preview', 40, 20, []);
        layer.kind = 'placed';
        layer.placed = {sourceCanvas:source,sourceWidth:40,sourceHeight:20,sourceName:'source.png',matrix:[1,0,0,1,10,5]};
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100,imgHeight:80,mainCanvas:main,mainCtx:main.getContext('2d'),
          layers:[layer],layerGroups:[],activeLayerId:layer.id,
          layerOffsets:new Map([[layer.id,{x:10,y:5}]]),lassoPoints:[],guides:{},wandMask:null,
        };
        resizeImageDocument(state,200,160);
        const resized={matrix:[...layer.placed.matrix],size:[layer.canvas.width,layer.canvas.height],offset:state.layerOffsets.get(layer.id)};
        rotateDocument(state,90);
        const rotated={matrix:[...layer.placed.matrix],size:[layer.canvas.width,layer.canvas.height],offset:state.layerOffsets.get(layer.id)};
        flipDocument(state,'h');
        const flipped={matrix:[...layer.placed.matrix],size:[layer.canvas.width,layer.canvas.height],offset:state.layerOffsets.get(layer.id)};
        cropDocument(state,{x:10,y:20,w:100,h:100});
        console.log(JSON.stringify({resized,rotated,flipped,cropped:{matrix:layer.placed.matrix,size:[layer.canvas.width,layer.canvas.height],offset:state.layerOffsets.get(layer.id)},source:[layer.placed.sourceCanvas.tag,layer.placed.sourceCanvas.width,layer.placed.sourceCanvas.height]}));
        """
    )
    assert result["resized"] == {"matrix": [2, 0, 0, 2, 20, 10], "size": [80, 40], "offset": {"x": 20, "y": 10}}
    assert result["rotated"] == {"matrix": [0, 2, -2, 0, 150, 20], "size": [40, 80], "offset": {"x": 110, "y": 20}}
    assert result["flipped"] == {"matrix": [0, 2, 2, 0, 10, 20], "size": [40, 80], "offset": {"x": 10, "y": 20}}
    assert result["cropped"] == {"matrix": [0, 2, 2, 0, 0, 0], "size": [40, 80], "offset": {"x": 0, "y": 0}}
    assert result["source"] == ["immutable-source", 40, 20]


def test_saved_and_reselect_masks_follow_document_geometry():
    result = run_node(
        """
        const layer = holder('layer-1', 'pixels', 100, 80, []);
        const saved = canvas('saved-selection', 100, 80);
        const recent = canvas('last-selection', 100, 80);
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100,imgHeight:80,mainCanvas:main,mainCtx:main.getContext('2d'),
          layers:[layer],layerGroups:[],activeLayerId:layer.id,
          layerOffsets:new Map([[layer.id,{x:0,y:0}]]),lassoPoints:[],guides:{},wandMask:null,
          savedSelections:[{id:'selection-1',name:'Subject',canvas:saved}],
          lastSelection:{canvas:recent},
        };
        cropDocument(state,{x:10,y:5,w:80,h:60});
        const crop={saved:[saved.width,saved.height],recent:[recent.width,recent.height],savedDraw:sourceDraw({canvas:saved})};
        resizeImageDocument(state,160,120);
        resizeCanvasDocument(state,150,110);
        rotateDocument(state,90);
        flipDocument(state,'h');
        console.log(JSON.stringify({crop,saved:[saved.width,saved.height],recent:[recent.width,recent.height]}));
        """
    )
    assert result["crop"]["saved"] == [80, 60]
    assert result["crop"]["recent"] == [80, 60]
    assert result["crop"]["savedDraw"] == {"source": "saved-selection", "args": [-10, -5]}
    assert result["saved"] == [110, 150]
    assert result["recent"] == [110, 150]


def test_text_layer_stays_retained_through_crop_and_image_resize():
    result = run_node(
        """
        const layer = holder('layer-text', 'text-pixels', 80, 30, []);
        layer.kind = 'text';
        layer.text = {
          content:'Caption', fontFamily:'Arial', fontSize:20, color:'#fff',
          transform:{scaleX:1,scaleY:1,rotation:0,flipH:false,flipV:false},
        };
        const main = canvas('main', 100, 80);
        const state = {
          imgWidth:100, imgHeight:80, mainCanvas:main, mainCtx:main.getContext('2d'),
          layers:[layer], activeLayerId:layer.id, layerOffsets:new Map([[layer.id, {x:20,y:10}]]),
          lassoPoints:[], wandMask:null, wandLastSeed:null,
        };
        cropDocument(state, {x:10,y:5,w:70,h:50});
        const afterCrop = {
          kind:layer.kind, content:layer.text.content, offset:state.layerOffsets.get(layer.id),
          size:[layer.canvas.width,layer.canvas.height],
        };
        resizeImageDocument(state, 140, 100);
        console.log(JSON.stringify({
          afterCrop, kind:layer.kind, content:layer.text.content,
          offset:state.layerOffsets.get(layer.id), transform:layer.text.transform,
          size:[layer.canvas.width,layer.canvas.height],
        }));
        """
    )

    assert result["afterCrop"]["kind"] == "text"
    assert result["afterCrop"]["content"] == "Caption"
    assert result["afterCrop"]["offset"] == {"x": 10, "y": 5}
    assert result["kind"] == "text"
    assert result["content"] == "Caption"
    assert result["offset"] == {"x": 20, "y": 10}
    assert result["transform"]["scaleX"] == 2
    assert result["transform"]["scaleY"] == 2
    assert result["size"][0] > result["afterCrop"]["size"][0]


def test_image_menu_exposes_separate_canvas_and_image_size_commands():
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
    wiring = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text(encoding="utf-8")
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")

    assert 'data-image-action="canvas-size"' in topbar
    assert 'data-image-action="image-size"' in topbar
    assert "resizeCanvasDocument(state, newW, newH, options)" in wiring
    assert "resizeImageDocument(state, newW, newH, {" in wiring
    assert "_cropDocument(state, state.cropRect)" in editor
