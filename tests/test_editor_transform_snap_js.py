import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/snap.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{computeTransformSnap}} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_rotated_frame_snaps_its_visual_edge_to_document_center():
    result = run_node(
        """
        const result=computeTransformSnap({
          centerX:104,centerY:80,width:100,height:40,rotation:45,
        }, {x:104,y:80}, {
          zoom:1,canvasW:300,canvasH:200,otherLayers:[],
        });
        console.log(JSON.stringify(result));
        """
    )
    # The rotated frame's rightmost visual edge starts about 3.6 px from x=150.
    assert abs(result["centerX"] - 100.5025253) < 0.00001
    assert result["centerY"] == 80
    assert result["guides"] == [{"vertical": True, "x": 150}]


def test_transform_snap_supports_guides_grid_and_other_layer_edges():
    result = run_node(
        """
        const frame={centerX:50,centerY:50,width:20,height:20,rotation:0};
        const common={zoom:1,canvasW:300,canvasH:200};
        const guide=computeTransformSnap(frame,{x:88,y:50},{
          ...common,verticalGuides:[100],otherLayers:[],
        });
        const layer=computeTransformSnap(frame,{x:191,y:50},{
          ...common,otherLayers:[{visible:true,id:'other',canvas:{width:30,height:20},offset:{x:200,y:40}}],
        });
        const grid=computeTransformSnap(frame,{x:106,y:50},{
          ...common,otherLayers:[],snapToGrid:true,gridSize:16,
        });
        console.log(JSON.stringify({guide,layer,grid}));
        """
    )
    assert result["guide"]["centerX"] == 90
    assert result["guide"]["guides"] == [{"vertical": True, "x": 100}]
    assert result["layer"]["centerX"] == 190
    assert result["layer"]["guides"] == [
        {"vertical": True, "x": 200},
        {"vertical": False, "y": 40},
    ]
    assert result["grid"]["centerX"] == 106
    assert result["grid"]["centerY"] == 48
    assert result["grid"]["guides"] == [
        {"vertical": True, "x": 96},
        {"vertical": False, "y": 48},
    ]
