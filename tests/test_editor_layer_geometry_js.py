import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GEOMETRY_MODULE = (ROOT / "static/js/editor/layer-geometry.js").as_uri()
MOVE_MODULE = (ROOT / "static/js/editor/tools/move.js").as_uri()
STATE_MODULE = (ROOT / "static/js/editor/state.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{
          createLayerGeometryController, normalizeLayerCoordinate,
          readLayerGeometry, setLayerPosition,
        }} from {json.dumps(GEOMETRY_MODULE)};
        import {{ createMoveTool }} from {json.dumps(MOVE_MODULE)};
        import {{ state }} from {json.dumps(STATE_MODULE)};
        globalThis.document = {{activeElement:null}};
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


def test_geometry_normalizes_coordinates_and_reports_layer_bounds():
    result = run_node(
        """
        const layer = {id:'photo',canvas:{width:640,height:480}};
        const editor = {layerOffsets:new Map([['photo',{x:12.6,y:-7.4}]])};
        const geometry = readLayerGeometry(editor,layer);
        const moved = setLayerPosition(editor,layer,'42.8','bad');
        console.log(JSON.stringify({
          geometry,moved,
          high:normalizeLayerCoordinate(99999999),
          low:normalizeLayerCoordinate(-99999999),
        }));
        """
    )
    assert result == {
        "geometry": {"x": 13, "y": -7, "width": 640, "height": 480},
        "moved": {"x": 43, "y": -7},
        "high": 1000000,
        "low": -1000000,
    }


def test_nudges_coalesce_history_and_transform_position_tracks_without_extra_snapshot():
    result = run_node(
        """
        const layer = {id:'photo',name:'Photo',canvas:{width:100,height:80},locked:false};
        Object.assign(state,{
          layers:[layer],activeLayerId:layer.id,
          layerOffsets:new Map([[layer.id,{x:0,y:0}]]),
          transformActive:false,transformLayer:null,transformOrigOffset:null,
        });
        const labels=[];
        const controller=createLayerGeometryController({
          activeLayer:()=>layer,saveState:label=>labels.push(label),composite:()=>{},
        });
        controller.nudge(1,0);
        controller.nudge(1,0);
        controller.endNudge();
        controller.nudge(0,10);
        controller.endNudge();
        state.transformActive=true;
        state.transformLayer=layer;
        state.transformOrigOffset={x:2,y:10};
        controller.nudge(5,-3);
        console.log(JSON.stringify({
          labels,offset:state.layerOffsets.get(layer.id),orig:state.transformOrigOffset,
        }));
        """
    )
    assert result == {
        "labels": ['Nudge "Photo"', 'Nudge "Photo"'],
        "offset": {"x": 7, "y": 7},
        "orig": {"x": 7, "y": 7},
    }


def test_move_drag_can_return_to_origin_and_transform_move_reuses_session_history():
    result = run_node(
        """
        const layer={id:'photo',name:'Photo',canvas:{width:50,height:40},visible:true};
        const canvas={width:200,height:200,getBoundingClientRect:()=>({left:0,top:0,width:200,height:200})};
        Object.assign(state,{
          mainCanvas:canvas,imgWidth:200,imgHeight:200,zoom:1,
          layers:[layer],activeLayerId:layer.id,layerOffsets:new Map([[layer.id,{x:0,y:0}]]),
          moving:false,transformActive:false,
        });
        const labels=[];
        const positions=[];
        const tool=createMoveTool({
          activeLayer:()=>layer,saveState:label=>labels.push(label),composite:()=>{},
          onPositionChange:(_layer,_before,next)=>positions.push(next),
        });
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},ctrlKey:false,metaKey:false});
        tool.begin(event(10,10));
        tool.drag(event(20,10));
        tool.drag(event(10,10));
        tool.end();
        state.transformActive=true;
        tool.begin(event(10,10));
        tool.drag(event(15,10));
        tool.end();
        console.log(JSON.stringify({labels,positions,offset:state.layerOffsets.get(layer.id)}));
        """
    )
    assert result["labels"] == ['Move "Photo"']
    assert result["positions"][:2] == [{"x": 10, "y": 0}, {"x": 0, "y": 0}]
    assert result["offset"] == {"x": 5, "y": 0}


def test_position_lock_blocks_drag_and_nudge_without_blocking_other_layer_locks():
    result = run_node(
        """
        const layer={id:'photo',name:'Photo',canvas:{width:50,height:40},visible:true,locks:{position:true}};
        const canvas={width:200,height:200,getBoundingClientRect:()=>({left:0,top:0,width:200,height:200})};
        Object.assign(state,{
          mainCanvas:canvas,imgWidth:200,imgHeight:200,zoom:1,
          layers:[layer],layerGroups:[],selectedLayerIds:[layer.id],activeLayerId:layer.id,
          layerOffsets:new Map([[layer.id,{x:0,y:0}]]),moving:false,transformActive:false,
        });
        const labels=[];
        const controller=createLayerGeometryController({activeLayer:()=>layer,saveState:label=>labels.push(label),composite:()=>{}});
        const tool=createMoveTool({activeLayer:()=>layer,saveState:label=>labels.push(label),composite:()=>{}});
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},ctrlKey:false,metaKey:false});
        controller.nudge(5,0);
        tool.begin(event(10,10));
        tool.drag(event(30,30));
        tool.end();
        const blocked={offset:state.layerOffsets.get(layer.id),labels:[...labels]};
        layer.locks={pixels:true,transparency:true,position:false};
        controller.nudge(5,0);
        console.log(JSON.stringify({blocked,allowed:state.layerOffsets.get(layer.id),labels}));
        """
    )
    assert result["blocked"] == {"offset": {"x": 0, "y": 0}, "labels": []}
    assert result["allowed"] == {"x": 5, "y": 0}
    assert result["labels"] == ['Nudge "Photo"']


def test_unlinked_mask_moves_independently_and_stays_fixed_when_parent_moves():
    result = run_node(
        """
        const mask={id:'mask',name:'Layer Mask',mode:'layer',linked:false,offset:{x:4,y:6}};
        const layer={id:'photo',name:'Photo',canvas:{width:50,height:40},visible:true,masks:[mask],activeMaskId:mask.id};
        const canvas={width:200,height:200,getBoundingClientRect:()=>({left:0,top:0,width:200,height:200})};
        Object.assign(state,{
          mainCanvas:canvas,imgWidth:200,imgHeight:200,zoom:1,
          layers:[layer],layerGroups:[],selectedLayerIds:[layer.id],activeLayerId:layer.id,
          layerOffsets:new Map([[layer.id,{x:20,y:30}]]),moving:false,transformActive:false,
          snapEnabled:false,snapToGrid:false,guides:{vertical:[],horizontal:[]},
        });
        const labels=[];
        const tool=createMoveTool({activeLayer:()=>layer,saveState:label=>labels.push(label),composite:()=>{}});
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},ctrlKey:false,metaKey:false});
        tool.begin(event(10,10));
        tool.drag(event(17,14));
        tool.end();
        const independent={layer:state.layerOffsets.get(layer.id),mask:{...mask.offset}};
        layer.activeMaskId=null;
        tool.begin(event(10,10));
        tool.drag(event(22,15));
        tool.end();
        console.log(JSON.stringify({labels,independent,layer:state.layerOffsets.get(layer.id),mask:mask.offset,
          documentMask:{x:state.layerOffsets.get(layer.id).x+mask.offset.x,y:state.layerOffsets.get(layer.id).y+mask.offset.y}}));
        """
    )
    assert result == {
        "labels": ['Move mask "Layer Mask"', 'Move "Photo"'],
        "independent": {"layer": {"x": 20, "y": 30}, "mask": {"x": 11, "y": 10}},
        "layer": {"x": 32, "y": 35},
        "mask": {"x": -1, "y": 5},
        "documentMask": {"x": 31, "y": 40},
    }


def test_geometry_controls_and_arrow_nudging_are_wired_offline():
    controls = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")

    for field_id in ("ge-layer-x", "ge-layer-y", "ge-layer-width", "ge-layer-height"):
        assert f'id="{field_id}"' in controls
    assert "e.key.startsWith('Arrow')" in keyboard
    assert "nudgeActiveLayer?.(...delta)" in keyboard
    assert "_layerGeometry.trackExternalMove" in editor
    assert "/static/js/editor/layer-geometry.js" in service_worker
