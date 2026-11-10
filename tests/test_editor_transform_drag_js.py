import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/tools/transform-drag.js").as_uri()
STATE_MODULE = (ROOT / "static/js/editor/state.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{createTransformDragTool}} from {json.dumps(MODULE)};
        import {{state}} from {json.dumps(STATE_MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_corner_drag_resizes_shared_frame_and_keeps_opposite_corner_anchored():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:40,height:20}};
        const canvas={width:300,height:200,style:{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:200})};
        Object.assign(state,{
          transformActive:true,transformLayer:layer,transformHandle:null,
          transformPendingW:100,transformPendingH:50,transformPendingRot:0,
          transformCenter:{x:50,y:25},transformBounds:{x:0,y:0,width:100,height:50},
          mainCanvas:canvas,layerOffsets:new Map([[layer.id,{x:30,y:15}]]),zoom:1,
        });
        let reapplied=0;
        const tool=createTransformDragTool({
          beginMove(){},composite(){},drawTransformHandles(){},reapplyTransform(){reapplied++;},
          getTransformHandle:()=> 'br',cursorForHandle:()=> 'nwse-resize',
        });
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},shiftKey:false});
        const began=tool.tryBegin(event(100,50));
        tool.tryContinue(event(120,60));
        const ended=tool.tryEnd();
        console.log(JSON.stringify({
          began,ended,reapplied,width:state.transformPendingW,height:state.transformPendingH,
          center:state.transformCenter,orig:[state.transformOrigW,state.transformOrigH],
        }));
        """
    )
    assert result == {
        "began": True,
        "ended": True,
        "reapplied": 1,
        "width": 120,
        "height": 60,
        "center": {"x": 60, "y": 30},
        "orig": [120, 60],
    }


def test_click_inside_rotated_frame_starts_transform_move_but_empty_aabb_corner_does_not():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:10,height:10}};
        Object.assign(state,{
          transformActive:true,transformLayer:layer,transformHandle:null,
          transformBounds:{x:20,y:30,width:150,height:90},transformCenter:{x:95,y:75},
          transformPendingW:150,transformPendingH:90,
          mainCanvas:{width:300,height:200,style:{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:200})},
          layerOffsets:new Map([[layer.id,{x:20,y:30}]]),zoom:1,
        });
        let containmentChecks=[];
        const tool=createTransformDragTool({
          composite(){},drawTransformHandles(){},reapplyTransform(){},
          getTransformHandle:()=>null,cursorForHandle:()=> 'default',
          pointInTransformFrame(x,y){
            containmentChecks.push([x,y]);
            return x === 80 && y === 70;
          },
        });
        const inside=tool.tryBegin({clientX:80,clientY:70,preventDefault(){}});
        const handle=state.transformHandle;
        tool.tryEnd();
        const outside=tool.tryBegin({clientX:190,clientY:150,preventDefault(){}});
        console.log(JSON.stringify({inside,outside,handle,containmentChecks}));
        """
    )
    assert result == {
        "inside": True,
        "outside": False,
        "handle": "move",
        "containmentChecks": [[80, 70], [190, 150]],
    }


def test_transform_move_uses_snap_result_and_clears_guides_on_end():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:100,height:50}};
        Object.assign(state,{
          transformActive:true,transformTarget:'layers',transformLayer:layer,transformHandle:null,
          transformPendingW:100,transformPendingH:50,transformPendingRot:30,
          transformCenter:{x:100,y:100},transformBounds:{x:44,y:53,width:112,height:94},
          mainCanvas:{width:300,height:300,style:{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:300})},
          layerOffsets:new Map([[layer.id,{x:50,y:75}]]),zoom:1,snapEnabled:true,
          activeSnapGuides:null,
        });
        let reapplied=0;
        const tool=createTransformDragTool({
          composite(){},drawTransformHandles(){},reapplyTransform(){reapplied++;},
          getTransformHandle:()=>null,cursorForHandle:()=> 'default',
          pointInTransformFrame:()=>true,
          snapTransformFrame(frame, center){
            return {centerX:150,centerY:125,guides:[{vertical:true,x:150}]};
          },
        });
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},ctrlKey:false,metaKey:false});
        tool.tryBegin(event(100,100));
        tool.tryContinue(event(147,123));
        const during={center:{...state.transformCenter},guides:state.activeSnapGuides,reapplied};
        tool.tryEnd();
        console.log(JSON.stringify({during,afterGuides:state.activeSnapGuides}));
        """
    )
    assert result == {
        "during": {
            "center": {"x": 150, "y": 125},
            "guides": [{"vertical": True, "x": 150}],
            "reapplied": 1,
        },
        "afterGuides": None,
    }


def test_rotated_edge_drag_resizes_on_the_frame_axis_and_keeps_opposite_edge_fixed():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:100,height:50}};
        Object.assign(state,{
          transformActive:true,transformLayer:layer,transformHandle:null,
          transformPendingW:100,transformPendingH:50,transformPendingRot:90,
          transformPendingFlipH:false,transformPendingFlipV:false,
          transformCenter:{x:100,y:100},transformBounds:{x:75,y:50,width:50,height:100},
          mainCanvas:{width:300,height:300,style:{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:300})},
          layerOffsets:new Map([[layer.id,{x:50,y:75}]]),zoom:1,
        });
        const tool=createTransformDragTool({
          beginMove(){},composite(){},drawTransformHandles(){},reapplyTransform(){},
          getTransformHandle:()=> 'r',cursorForHandle:()=> 'ns-resize',
        });
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},shiftKey:false,altKey:false});
        tool.tryBegin(event(100,150));
        tool.tryContinue(event(100,170));
        console.log(JSON.stringify({
          width:state.transformPendingW,
          height:state.transformPendingH,
          center:state.transformCenter,
        }));
        """
    )
    assert result == {
        "width": 120,
        "height": 50,
        "center": {"x": 100, "y": 110},
    }


def test_resize_crossing_anchor_flips_and_alt_scales_from_center():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:100,height:50}};
        const canvas={width:300,height:200,style:{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:200})};
        const makeTool=()=>createTransformDragTool({
          beginMove(){},composite(){},drawTransformHandles(){},reapplyTransform(){},
          getTransformHandle:()=> 'r',cursorForHandle:()=> 'ew-resize',
        });
        const event=(x,y,altKey=false)=>({clientX:x,clientY:y,preventDefault(){},shiftKey:false,altKey});

        Object.assign(state,{
          transformActive:true,transformLayer:layer,transformHandle:null,
          transformPendingW:100,transformPendingH:50,transformPendingRot:0,
          transformPendingFlipH:false,transformPendingFlipV:false,
          transformCenter:{x:50,y:25},transformBounds:{x:0,y:0,width:100,height:50},
          mainCanvas:canvas,layerOffsets:new Map([[layer.id,{x:0,y:0}]]),zoom:1,
        });
        let tool=makeTool();
        tool.tryBegin(event(100,25));
        tool.tryContinue(event(-20,25));
        const crossed={width:state.transformPendingW,center:{...state.transformCenter},flipH:state.transformPendingFlipH};
        tool.tryEnd();

        Object.assign(state,{
          transformHandle:null,transformPendingW:100,transformPendingH:50,
          transformPendingFlipH:false,transformPendingFlipV:false,transformCenter:{x:50,y:25},
        });
        tool=makeTool();
        tool.tryBegin(event(100,25,true));
        tool.tryContinue(event(120,25,true));
        const centered={width:state.transformPendingW,center:{...state.transformCenter},flipH:state.transformPendingFlipH};
        console.log(JSON.stringify({crossed,centered}));
        """
    )
    assert result == {
        "crossed": {"width": 20, "center": {"x": -10, "y": 25}, "flipH": True},
        "centered": {"width": 140, "center": {"x": 50, "y": 25}, "flipH": False},
    }


def test_shift_corner_resize_preserves_starting_aspect_ratio():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:100,height:50}};
        Object.assign(state,{
          transformActive:true,transformLayer:layer,transformHandle:null,
          transformPendingW:100,transformPendingH:50,transformPendingRot:0,
          transformPendingFlipH:false,transformPendingFlipV:false,
          transformCenter:{x:50,y:25},transformBounds:{x:0,y:0,width:100,height:50},
          mainCanvas:{width:300,height:200,style:{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:200})},
          layerOffsets:new Map([[layer.id,{x:0,y:0}]]),zoom:1,
        });
        const tool=createTransformDragTool({
          beginMove(){},composite(){},drawTransformHandles(){},reapplyTransform(){},
          getTransformHandle:()=> 'br',cursorForHandle:()=> 'nwse-resize',
        });
        const event=(x,y)=>({clientX:x,clientY:y,preventDefault(){},shiftKey:true,altKey:false});
        tool.tryBegin(event(100,50));
        tool.tryContinue(event(120,55));
        console.log(JSON.stringify({
          width:state.transformPendingW,
          height:state.transformPendingH,
          center:state.transformCenter,
        }));
        """
    )
    assert result == {
        "width": 120,
        "height": 60,
        "center": {"x": 60, "y": 30},
    }
