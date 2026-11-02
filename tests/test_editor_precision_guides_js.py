import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRECISION_MODULE = (ROOT / "static/js/editor/precision-guides.js").as_uri()
SNAP_MODULE = (ROOT / "static/js/editor/snap.js").as_uri()
MOVE_MODULE = (ROOT / "static/js/editor/tools/move.js").as_uri()
STATE_MODULE = (ROOT / "static/js/editor/state.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{ chooseRulerStep, pointerToDocument }} from {json.dumps(PRECISION_MODULE)};
        import {{ computeSnap }} from {json.dumps(SNAP_MODULE)};
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


def test_ruler_step_and_pointer_mapping_scale_with_viewport():
    result = run_node(
        """
        console.log(JSON.stringify({
          steps:[chooseRulerStep(1),chooseRulerStep(0.25),chooseRulerStep(4)],
          point:pointerToDocument(110,70,{left:10,top:20,width:200,height:100},400,200),
        }));
        """
    )
    assert result == {
        "steps": [50, 200, 20],
        "point": {"x": 200, "y": 100},
    }


def test_snap_targets_explicit_guides_and_grid():
    result = run_node(
        """
        const layer={id:'active',canvas:{width:20,height:10}};
        const guides=computeSnap(layer,43,47,{
          zoom:1,canvasW:300,canvasH:200,otherLayers:[],
          verticalGuides:[50],horizontalGuides:[52],
        });
        const grid=computeSnap(layer,34,74,{
          zoom:1,canvasW:300,canvasH:200,otherLayers:[],snapToGrid:true,gridSize:20,
        });
        console.log(JSON.stringify({guides,grid}));
        """
    )
    assert result["guides"]["x"] == 40
    assert result["guides"]["y"] == 47
    assert {"vertical": True, "x": 50} in result["guides"]["guides"]
    assert {"vertical": False, "y": 52} in result["guides"]["guides"]
    assert result["grid"]["x"] == 30
    assert result["grid"]["y"] == 75


def test_move_modifier_inverts_persistent_snap_preference():
    result = run_node(
        """
        const layer={id:'photo',name:'Photo',canvas:{width:10,height:10},visible:true};
        const canvas={width:200,height:200,getBoundingClientRect:()=>({left:0,top:0,width:200,height:200})};
        Object.assign(state,{
          mainCanvas:canvas,imgWidth:200,imgHeight:200,zoom:1,
          layers:[layer],activeLayerId:layer.id,guides:{vertical:[37],horizontal:[]},
          gridSize:20,snapToGrid:false,moving:false,transformActive:false,
        });
        const event=(x,ctrl=false)=>({clientX:x,clientY:30,preventDefault(){},ctrlKey:ctrl,metaKey:false});
        function drag(enabled,ctrl) {
          state.snapEnabled=enabled;
          state.layerOffsets=new Map([[layer.id,{x:0,y:30}]]);
          const tool=createMoveTool({activeLayer:()=>layer,saveState:()=>{},composite:()=>{}});
          tool.begin(event(0,ctrl));
          tool.drag(event(34,ctrl));
          tool.end();
          return state.layerOffsets.get(layer.id).x;
        }
        console.log(JSON.stringify({defaultOn:drag(true,false),invertedOff:drag(true,true),temporaryOn:drag(false,true)}));
        """
    )
    assert result == {"defaultOn": 32, "invertedOff": 34, "temporaryOn": 32}


def test_precision_controls_are_wired_and_cached_offline():
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")

    for action in ("rulers", "grid", "snap", "snap-grid", "clear-guides"):
        assert f'data-view-action="{action}"' in topbar
    assert "saveState: _saveState" in editor
    assert "schedulePersist: _schedulePersist" in editor
    assert "_precisionGuides?.drawDocumentOverlay" in editor
    assert "/static/js/editor/precision-guides.js" in service_worker
    assert "/static/js/editor/wire-view-menu.js" in service_worker


def test_history_snapshots_include_and_restore_guides():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "vertical: [...(state.guides?.vertical || [])]" in editor
    assert "vertical: [...(snap.guides?.vertical || [])]" in editor
    assert "saveState?.('Add guide')" in (
        ROOT / "static/js/editor/precision-guides.js"
    ).read_text(encoding="utf-8")
