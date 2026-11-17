import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/layer-groups.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{allLayerIdsInGroup,createGroupFromSelection,drawGroupedLayers,groupSiblingUnits,isLayerEffectivelyLocked,isLayerPixelLocked,isLayerPositionLocked,isLayerTransparencyLocked,layerHasAnyLock,normalizeLayerGroups,normalizeLayerLocks,reorderGroupAmongSiblings,ungroupLayers}} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_group_creation_makes_noncontiguous_selection_contiguous_and_ungroups_losslessly():
    result = run_node(
        """
        const layers=['a','middle','c','top'].map(id=>({id}));
        const state={layers,selectedLayerIds:['a','c'],activeLayerId:'c',selectionAnchorId:'a',layerGroups:[],nextLayerId:5};
        const group=createGroupFromSelection(state,'Pair');
        const grouped={order:state.layers.map(layer=>layer.id),group,active:state.activeLayerId,selected:state.selectedLayerIds};
        const removed=ungroupLayers(state,group.id);
        console.log(JSON.stringify({grouped,removed,groups:normalizeLayerGroups(state)}));
        """
    )
    assert result["grouped"]["order"] == ["middle", "a", "c", "top"]
    assert result["grouped"]["group"]["layerIds"] == ["a", "c"]
    assert result["grouped"]["active"] == "c"
    assert result["grouped"]["selected"] == ["a", "c"]
    assert result["removed"]["name"] == "Pair"
    assert result["groups"] == []


def test_group_compositor_isolates_member_blending_then_applies_group_opacity_once():
    result = run_node(
        """
        const operations=[];
        const makeCtx=tag=>({
          tag,_alpha:1,_gco:'source-over',
          clearRect(){operations.push([tag,'clear']);},
          drawImage(image,x=0,y=0){operations.push([tag,'draw',image.tag,x,y,this._alpha,this._gco]);},
          set globalAlpha(value){this._alpha=value;},get globalAlpha(){return this._alpha;},
          set globalCompositeOperation(value){this._gco=value;},get globalCompositeOperation(){return this._gco;},
        });
        let canvasIndex=0;
        globalThis.document={createElement:()=>{const tag=`group-canvas-${++canvasIndex}`;return {tag,width:0,height:0,getContext:()=>makeCtx(tag)}}};
        const target=makeCtx('target');
        const layers=[
          {id:'base',visible:true,opacity:1,blendMode:'source-over'},
          {id:'a',visible:true,opacity:.8,blendMode:'multiply'},
          {id:'b',visible:true,opacity:.6,blendMode:'screen'},
        ];
        const state={imgWidth:100,imgHeight:80,layers,layerOffsets:new Map(),layerGroups:[{id:'g',name:'Group',layerIds:['a','b'],visible:true,opacity:.5,blendMode:'overlay'}],groupCompositeCanvases:new Map()};
        drawGroupedLayers(target,state,layer=>({tag:layer.id}));
        console.log(JSON.stringify({operations,cached:[...state.groupCompositeCanvases.keys()]}));
        """
    )
    operations = result["operations"]
    assert ["target", "draw", "base", 0, 0, 1, "source-over"] in operations
    assert ["group-canvas-1", "draw", "a", 0, 0, 0.8, "multiply"] in operations
    assert ["group-canvas-1", "draw", "b", 0, 0, 0.6, "screen"] in operations
    assert ["target", "draw", "group-canvas-1", 0, 0, 0.5, "overlay"] in operations
    assert len([operation for operation in operations if operation[:2] == ["target", "draw"]]) == 2
    assert result["cached"] == ["g"]


def test_group_thumbnail_cache_is_cleared_when_the_group_is_hidden_or_removed():
    result = run_node(
        """
        const makeCtx=()=>({clearRect(){},drawImage(){},globalAlpha:1,globalCompositeOperation:'source-over'});
        globalThis.document={createElement:()=>({width:0,height:0,getContext:()=>makeCtx()})};
        const state={imgWidth:10,imgHeight:10,layers:[{id:'a',visible:true}],layerOffsets:new Map(),groupCompositeCanvases:new Map([['stale',{width:10,height:10}]]),layerGroups:[{id:'g',layerIds:['a'],visible:false,opacity:1}]};
        drawGroupedLayers(makeCtx(),state,layer=>layer);
        console.log(JSON.stringify([...state.groupCompositeCanvases.keys()]));
        """
    )
    assert result == []


def test_nested_groups_preserve_children_lock_descendants_and_ungroup_one_level():
    result = run_node(
        """
        const layers=['a','b','c'].map(id=>({id,locked:false}));
        const state={layers,selectedLayerIds:['a','b'],layerGroups:[],nextLayerId:10};
        const inner=createGroupFromSelection(state,'Inner');
        state.selectedLayerIds=['a','b','c'];
        const outer=createGroupFromSelection(state,'Outer');
        outer.locked=true;
        const nested={groups:state.layerGroups.map(group=>({...group})),ids:allLayerIdsInGroup(state,outer),locked:isLayerEffectivelyLocked(state,layers[0])};
        const removed=ungroupLayers(state,outer.id);
        console.log(JSON.stringify({nested,removed,groups:state.layerGroups,selection:state.selectedLayerIds}));
        """
    )
    assert result["nested"]["ids"] == ["a", "b", "c"]
    inner, outer = result["nested"]["groups"]
    assert inner["parentId"] == outer["id"]
    assert outer["layerIds"] == ["c"]
    assert result["nested"]["locked"] is True
    assert result["groups"][0]["parentId"] is None
    assert result["selection"] == ["a", "b", "c"]


def test_nested_group_compositor_uses_distinct_isolation_surfaces():
    result = run_node(
        """
        const operations=[];
        const makeCtx=tag=>({tag,_alpha:1,_gco:'source-over',clearRect(){},drawImage(image){operations.push([tag,'draw',image.tag,this._alpha,this._gco]);},set globalAlpha(v){this._alpha=v},get globalAlpha(){return this._alpha},set globalCompositeOperation(v){this._gco=v},get globalCompositeOperation(){return this._gco}});
        let index=0;
        globalThis.document={createElement:()=>{const tag=`surface-${++index}`;return {tag,width:0,height:0,getContext:()=>makeCtx(tag)}}};
        const layers=[{id:'a',visible:true,opacity:1},{id:'b',visible:true,opacity:1}];
        const state={imgWidth:10,imgHeight:10,layers,layerOffsets:new Map(),layerGroups:[
          {id:'outer',layerIds:['b'],opacity:.5,blendMode:'multiply'},
          {id:'inner',parentId:'outer',layerIds:['a'],opacity:.4,blendMode:'screen'},
        ]};
        drawGroupedLayers(makeCtx('target'),state,layer=>({tag:layer.id}));
        console.log(JSON.stringify(operations));
        """
    )
    assert ["surface-2", "draw", "a", 1, "source-over"] in result
    assert ["surface-1", "draw", "surface-2", 0.4, "screen"] in result
    assert ["surface-1", "draw", "b", 1, "source-over"] in result
    assert ["target", "draw", "surface-1", 0.5, "multiply"] in result


def test_group_mask_is_applied_inside_group_isolation_and_blocks_ungroup():
    result = run_node(
        """
        const operations=[];
        const makeCtx=tag=>({tag,_alpha:1,_gco:'source-over',clearRect(){},drawImage(image){operations.push([tag,'draw',image.tag,this._alpha,this._gco]);},set globalAlpha(v){this._alpha=v},get globalAlpha(){return this._alpha},set globalCompositeOperation(v){this._gco=v},get globalCompositeOperation(){return this._gco}});
        globalThis.document={createElement:()=>({tag:'surface',width:0,height:0,getContext:()=>makeCtx('surface')})};
        const layer={id:'a',visible:true,opacity:1};
        const mask={id:'m',visible:true,canvas:{tag:'mask'}};
        const state={imgWidth:10,imgHeight:10,layers:[layer],layerOffsets:new Map(),layerGroups:[{id:'g',layerIds:['a'],masks:[mask]}]};
        drawGroupedLayers(makeCtx('target'),state,item=>({tag:item.id}));
        console.log(JSON.stringify({operations,ungrouped:ungroupLayers(state,'g'),groups:state.layerGroups.length}));
        """
    )
    assert ["surface", "draw", "mask", 1, "destination-in"] in result["operations"]
    assert result["ungrouped"] is None
    assert result["groups"] == 1


def test_group_compositor_stops_before_drawing_when_render_generation_is_invalidated():
    result = run_node(
        """
        const operations=[];
        const ctx={
          clearRect(){operations.push('clear');},
          drawImage(){operations.push('draw');},
          globalAlpha:1,
          globalCompositeOperation:'source-over',
        };
        const state={imgWidth:10,imgHeight:10,layers:[{id:'a',visible:true}],layerOffsets:new Map(),layerGroups:[]};
        drawGroupedLayers(ctx,state,layer=>({tag:layer.id}),null,null,()=>false);
        console.log(JSON.stringify(operations));
        """
    )
    assert result == ["clear"]


def test_group_reorder_moves_complete_nested_subtrees_only_among_siblings():
    result = run_node(
        """
        const layers=['root-bottom','a','b','loose','root-top'].map(id=>({id}));
        const state={layers,layerGroups:[
          {id:'outer',layerIds:['loose']},
          {id:'inner',parentId:'outer',layerIds:['a','b']},
        ]};
        const before=groupSiblingUnits(state,'outer');
        const nestedMove=reorderGroupAmongSiblings(state,'inner',1);
        const afterNested=state.layers.map(layer=>layer.id);
        const rootMove=reorderGroupAmongSiblings(state,'outer',2);
        console.log(JSON.stringify({before,nestedMove,afterNested,rootMove,afterRoot:state.layers.map(layer=>layer.id),groups:state.layerGroups}));
        """
    )
    assert [(unit["type"], unit["id"]) for unit in result["before"]] == [
        ("group", "inner"), ("layer", "loose")
    ]
    assert result["nestedMove"]["changed"] is True
    assert result["afterNested"] == ["root-bottom", "loose", "a", "b", "root-top"]
    assert result["rootMove"]["changed"] is True
    assert result["afterRoot"] == ["root-bottom", "root-top", "loose", "a", "b"]
    assert next(group for group in result["groups"] if group["id"] == "inner")["parentId"] == "outer"


def test_independent_layer_locks_normalize_and_group_lock_overrides_every_type():
    result = run_node(
        """
        const layer={id:'photo',locks:{pixels:1,transparency:false,position:true}};
        const state={layers:[layer],layerGroups:[]};
        const own={
          normalized:normalizeLayerLocks(layer.locks),
          any:layerHasAnyLock(layer),
          pixels:isLayerPixelLocked(state,layer),
          transparency:isLayerTransparencyLocked(state,layer),
          position:isLayerPositionLocked(state,layer),
        };
        state.layerGroups=[{id:'g',layerIds:['photo'],locked:true}];
        const inherited={
          pixels:isLayerPixelLocked(state,layer),
          transparency:isLayerTransparencyLocked(state,layer),
          position:isLayerPositionLocked(state,layer),
        };
        console.log(JSON.stringify({own,inherited,invalid:normalizeLayerLocks('bad')}));
        """
    )
    assert result["own"] == {
        "normalized": {"pixels": True, "transparency": False, "position": True},
        "any": True,
        "pixels": True,
        "transparency": False,
        "position": True,
    }
    assert result["inherited"] == {"pixels": True, "transparency": True, "position": True}
    assert result["invalid"] == {"pixels": False, "transparency": False, "position": False}
