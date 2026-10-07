import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/layer-clipping.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{canToggleLayerClipping,clippingBaseForLayer,drawLayerStack,normalizeLayerClipping}} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_clipping_topology_stays_inside_group_scope_and_repairs_invalid_flags():
    result = run_node(
        """
        const layers=['root','group-base','group-clip','after'].map(id=>({id,clipped:id!=='root'}));
        const state={layers,layerGroups:[{id:'g',layerIds:['group-base','group-clip']}]};
        const before={
          groupBase:clippingBaseForLayer(state,'group-clip')?.id || null,
          afterBase:clippingBaseForLayer(state,'after')?.id || null,
          canRoot:canToggleLayerClipping(state,'root'),
        };
        const cleared=normalizeLayerClipping(state);
        console.log(JSON.stringify({before,cleared,clipped:layers.map(layer=>layer.clipped)}));
        """
    )
    assert result["before"] == {"groupBase": None, "afterBase": None, "canRoot": False}
    assert result["cleared"] == ["group-base", "after"]
    assert result["clipped"] == [False, False, True, False]


def test_clipped_layer_is_masked_by_base_alpha_before_its_opacity_and_blend():
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
        const scratch={tag:'clip-scratch',width:0,height:0,getContext:()=>scratchCtx};
        const scratchCtx=makeCtx('clip-scratch');
        globalThis.document={createElement:()=>scratch};
        const target=makeCtx('target');
        const layers=[
          {id:'base',visible:true,opacity:.4,blendMode:'source-over',clipped:false},
          {id:'color',visible:true,opacity:.7,blendMode:'multiply',clipped:true},
        ];
        const state={imgWidth:100,imgHeight:80,layers,layerGroups:[],layerOffsets:new Map([['base',{x:3,y:4}],['color',{x:8,y:9}]])};
        drawLayerStack(target,state,layers,layer=>({tag:layer.id}));
        console.log(JSON.stringify(operations));
        """
    )
    assert ["target", "draw", "base", 3, 4, 0.4, "source-over"] in result
    assert ["clip-scratch", "draw", "color", 8, 9, 1, "source-over"] in result
    assert ["clip-scratch", "draw", "base", 3, 4, 1, "destination-in"] in result
    assert ["target", "draw", "clip-scratch", 0, 0, 0.7, "multiply"] in result
