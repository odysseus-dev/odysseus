import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/direct-manipulation-session.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{createDirectManipulationSession}} from {json.dumps(MODULE)};
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


def test_session_captures_one_pointer_and_releases_it_on_commit():
    result = run_node(
        """
        const calls=[];
        const target={
          setPointerCapture:id=>calls.push(['capture',id]),
          releasePointerCapture:id=>calls.push(['release',id]),
        };
        const session=createDirectManipulationSession({getContext:()=>({documentId:'a'})});
        session.begin({pointerId:7},{value:1},{captureTarget:target});
        const wrong=session.update({pointerId:8},()=>calls.push(['wrong']));
        const right=session.update({pointerId:7},data=>calls.push(['update',data.value]));
        const committed=session.commit({pointerId:7},data=>calls.push(['commit',data.value]));
        console.log(JSON.stringify({wrong,right,committed,active:!!session.active,calls}));
        """
    )
    assert result == {
        "wrong": False,
        "right": True,
        "committed": True,
        "active": False,
        "calls": [["capture", 7], ["update", 1], ["release", 7], ["commit", 1]],
    }


def test_stale_context_cancels_without_running_update():
    result = run_node(
        """
        let documentId='a';
        const calls=[];
        const session=createDirectManipulationSession({
          getContext:()=>({documentId}),
          isContextCurrent:context=>context.documentId===documentId,
          onCancel:(data,reason)=>calls.push(['cancel',data.value,reason]),
        });
        session.begin({}, {value:3});
        documentId='b';
        const updated=session.update({},()=>calls.push(['update']));
        console.log(JSON.stringify({updated,active:!!session.active,calls}));
        """
    )
    assert result == {
        "updated": False,
        "active": False,
        "calls": [["cancel", 3, "stale-context"]],
    }
