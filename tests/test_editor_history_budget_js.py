import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/history-budget.js").as_uri()


def run_node(script: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_history_budget_drops_oldest_snapshots_by_bytes_and_count():
    script = textwrap.dedent(
        f"""
        import {{ trimHistoryStack }} from {json.dumps(MODULE)};
        const byBytes = [{{id:1,_bytes:80}},{{id:2,_bytes:80}},{{id:3,_bytes:80}}];
        const bytes = trimHistoryStack(byBytes, 10, 170);
        const byCount = [{{id:1,_bytes:1}},{{id:2,_bytes:1}},{{id:3,_bytes:1}}];
        trimHistoryStack(byCount, 2, 100);
        console.log(JSON.stringify({{byBytes:byBytes.map(x=>x.id),bytes,byCount:byCount.map(x=>x.id)}}));
        """
    )
    assert run_node(script) == {"byBytes": [2, 3], "bytes": 160, "byCount": [2, 3]}


def test_history_budget_keeps_latest_oversized_snapshot():
    script = textwrap.dedent(
        f"""
        import {{ trimHistoryStack }} from {json.dumps(MODULE)};
        const stack = [{{id:1,_bytes:20}},{{id:2,_bytes:500}}];
        const bytes = trimHistoryStack(stack, 10, 100);
        console.log(JSON.stringify({{ids:stack.map(x=>x.id),bytes}}));
        """
    )
    assert run_node(script) == {"ids": [2], "bytes": 500}


def test_moves_share_pixels_but_keep_independent_offsets_and_changed_pixels():
    script = textwrap.dedent(
        f"""
        import {{ shareSnapshotPixels, trimHistoryStack }} from {json.dumps(MODULE)};
        const make=(x, pixel=7)=>({{layers:[{{id:'a',offset:{{x,y:0}},imageData:{{width:10,height:10,data:new Uint8ClampedArray(400).fill(pixel)}}}}]}});
        const stack=[];
        for(let x=0;x<20;x++)stack.push(shareSnapshotPixels(make(x),stack.at(-1)));
        const bytes=trimHistoryStack(stack,30,800);
        const changed=shareSnapshotPixels(make(20,8),stack.at(-1));
        console.log(JSON.stringify({{count:stack.length,bytes,first:stack[0].layers[0].offset.x,last:stack.at(-1).layers[0].offset.x,shared:stack[0].layers[0].imageData===stack.at(-1).layers[0].imageData,changed:changed.layers[0].imageData!==stack.at(-1).layers[0].imageData}}));
        """
    )
    assert run_node(script) == {"count": 20, "bytes": 400, "first": 0, "last": 19, "shared": True, "changed": True}


def test_history_budget_counts_saved_selections_and_group_masks():
    script = textwrap.dedent(
        f"""
        import {{ snapshotByteSize }} from {json.dumps(MODULE)};
        const imageData=bytes=>({{data:{{byteLength:bytes}}}});
        const snapshot={{
          wand:{{imageData:imageData(10)}},
          lastSelection:{{imageData:imageData(20)}},
          savedSelections:[{{imageData:imageData(30)}},{{imageData:imageData(40)}}],
          layerGroups:[{{masks:[{{imageData:imageData(50)}}]}}],
          layers:[{{imageData:imageData(60),masks:[{{imageData:imageData(70)}}]}}],
        }};
        console.log(JSON.stringify(snapshotByteSize(snapshot)));
        """
    )
    assert run_node(script) == 280
