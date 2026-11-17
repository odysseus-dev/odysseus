import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/layer-selection.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{
          normalizeLayerSelection, selectedLayers, selectAllLayers,
          selectLayerRange, selectOnlyLayer, toggleLayerSelection,
        }} from {json.dumps(MODULE)};
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


def test_layer_selection_supports_single_toggle_range_and_select_all():
    result = run_node(
        """
        const layers=['one','two','three','four'].map(id=>({id,locked:id==='three'}));
        const state={layers,activeLayerId:'two',selectedLayerIds:[],selectionAnchorId:null};
        selectOnlyLayer(state,'two');
        const single=[...state.selectedLayerIds];
        toggleLayerSelection(state,'four');
        const toggled={ids:[...state.selectedLayerIds],active:state.activeLayerId};
        selectLayerRange(state,'one');
        const ranged={ids:[...state.selectedLayerIds],active:state.activeLayerId};
        const unlocked=selectedLayers(state,{unlockedOnly:true}).map(layer=>layer.id);
        selectAllLayers(state);
        console.log(JSON.stringify({single,toggled,ranged,unlocked,all:state.selectedLayerIds}));
        """
    )
    assert result == {
        "single": ["two"],
        "toggled": {"ids": ["two", "four"], "active": "four"},
        "ranged": {"ids": ["one", "two", "three", "four"], "active": "one"},
        "unlocked": ["one", "two", "four"],
        "all": ["one", "two", "three", "four"],
    }


def test_layer_selection_discards_stale_ids_and_keeps_active_layer_selected():
    result = run_node(
        """
        const state={
          layers:[{id:'one'},{id:'two'}],activeLayerId:'two',
          selectedLayerIds:['missing','one'],selectionAnchorId:'missing',
        };
        const ids=normalizeLayerSelection(state);
        toggleLayerSelection(state,'two');
        console.log(JSON.stringify({ids,selected:state.selectedLayerIds,active:state.activeLayerId,anchor:state.selectionAnchorId}));
        """
    )
    assert result == {
        "ids": ["two"],
        "selected": ["two"],
        "active": "two",
        "anchor": "two",
    }
