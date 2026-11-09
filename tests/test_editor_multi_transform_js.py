import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/multi-transform.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{planLayerTransform,selectionBounds,transformedLayerGeometry,transformedSelectionBounds}} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_selection_bounds_cover_every_layer_and_preserve_relative_geometry_when_scaled():
    result = run_node(
        """
        const layers=[{id:'a',canvas:{width:100,height:50}},{id:'b',canvas:{width:20,height:20}}];
        const state={layerOffsets:new Map([['a',{x:0,y:0}],['b',{x:180,y:80}]])};
        const bounds=selectionBounds(state,layers);
        const target={width:400,height:200,rotation:0,flipH:false,flipV:false,centerX:300,centerY:200};
        const a=transformedLayerGeometry({width:100,height:50,offset:{x:0,y:0}},bounds,target);
        const b=transformedLayerGeometry({width:20,height:20,offset:{x:180,y:80}},bounds,target);
        console.log(JSON.stringify({bounds,a,b,frame:transformedSelectionBounds(bounds,target)}));
        """
    )
    assert result["bounds"] == {"x": 0, "y": 0, "width": 200, "height": 100, "centerX": 100, "centerY": 50}
    assert result["a"]["offset"] == {"x": 100, "y": 100}
    assert [result["a"]["width"], result["a"]["height"]] == [200, 100]
    assert result["b"]["offset"] == {"x": 460, "y": 260}
    assert [result["b"]["width"], result["b"]["height"]] == [40, 40]
    assert result["frame"] == {"x": 100, "y": 100, "width": 400, "height": 200, "centerX": 300, "centerY": 200}


def test_rotation_and_flip_transform_layer_centers_around_the_shared_pivot():
    result = run_node(
        """
        const bounds={x:0,y:0,width:200,height:100,centerX:100,centerY:50};
        const snapshot={width:100,height:50,offset:{x:0,y:0}};
        const target={width:400,height:200,rotation:90,flipH:true,flipV:false,centerX:300,centerY:200};
        console.log(JSON.stringify(transformedLayerGeometry(snapshot,bounds,target)));
        """
    )
    assert [result["width"], result["height"]] == [100, 200]
    assert round(result["centerX"], 6) == 350
    assert round(result["centerY"], 6) == 300
    assert result["offset"] == {"x": 300, "y": 200}
    assert result["signedScaleX"] == -2
    assert result["signedScaleY"] == 2


def test_transform_plan_counts_linked_masks_at_output_size_and_preserves_unlinked_surfaces():
    result = run_node(
        """
        const linked={canvas:{width:100,height:50}};
        const unlinked={canvas:{width:30,height:20}};
        const layer={id:'a',canvas:{width:100,height:50},masks:[linked,unlinked]};
        const snapshot={
          layer,width:100,height:50,offset:{x:0,y:0},
          masks:[
            {mask:linked,linked:true,canvas:linked.canvas},
            {mask:unlinked,linked:false,canvas:unlinked.canvas},
          ],
        };
        const state={
          layers:[layer,{id:'b',canvas:{width:10,height:10},masks:[]}],
          layerGroups:[{masks:[{canvas:{width:5,height:4}}]}],
          savedSelections:[{canvas:{width:8,height:6}}],
          wandMask:{width:7,height:3},
        };
        const bounds={x:0,y:0,width:100,height:50,centerX:50,centerY:25};
        const target={width:200,height:100,rotation:0,flipH:false,flipV:false,centerX:50,centerY:25};
        const plan=planLayerTransform(state,[snapshot],bounds,target,{maxDimension:1000,maxSurfacePixels:100000});
        console.log(JSON.stringify({ok:plan.ok,pixels:plan.surfacePixels,size:[plan.items[0].geometry.width,plan.items[0].geometry.height]}));
        """
    )
    # layer 20k + linked mask 20k + unlinked mask 600 + other layer 100
    # + group mask 20 + saved selection 48 + active selection 21
    assert result == {"ok": True, "pixels": 40789, "size": [200, 100]}


def test_transform_plan_rejects_dimension_and_total_surface_limits_before_allocation():
    result = run_node(
        """
        const layer={id:'a',canvas:{width:100,height:50},masks:[]};
        const snapshot={layer,width:100,height:50,offset:{x:0,y:0},masks:[]};
        const state={layers:[layer],layerGroups:[],savedSelections:[],wandMask:null};
        const bounds={x:0,y:0,width:100,height:50,centerX:50,centerY:25};
        const base={rotation:0,flipH:false,flipV:false,centerX:50,centerY:25};
        const dimension=planLayerTransform(state,[snapshot],bounds,{...base,width:1001,height:20},{maxDimension:1000,maxSurfacePixels:100000});
        const surface=planLayerTransform(state,[snapshot],bounds,{...base,width:500,height:300},{maxDimension:1000,maxSurfacePixels:100000});
        const numeric=planLayerTransform(state,[snapshot],bounds,{...base,width:Infinity,height:20},{maxDimension:1000,maxSurfacePixels:100000});
        console.log(JSON.stringify({dimension,surface,numeric}));
        """
    )
    assert result["dimension"]["ok"] is False
    assert "dimension limit" in result["dimension"]["reason"]
    assert result["surface"] == {
        "ok": False,
        "reason": "Transform would exceed the 300 megapixel surface budget.",
    }
    assert result["numeric"]["ok"] is False
    assert "finite positive" in result["numeric"]["reason"]
