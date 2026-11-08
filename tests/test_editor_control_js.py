import json
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/editor-control.js").as_uri()


def run_node(body):
    script = textwrap.dedent(
        f"""
        import {{ createEditorControl, EDITOR_CONTROL_VERSION }} from {json.dumps(MODULE)};
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


def test_editor_control_version_and_listing():
    out = run_node(
        """
        const control = createEditorControl({
          handlers: {
            'layers.list': { run: () => [], description: 'List layers' },
            'layers.addRaster': () => ({ id: 'layer-1' }),
          },
        });
        console.log(JSON.stringify({
          version: EDITOR_CONTROL_VERSION,
          installed: control.installed,
          listed: control.list(),
          described: control.describe('layers.list'),
          missing: control.describe('nope'),
        }));
        """
    )
    assert out["version"] == "1.0"
    assert out["installed"] is True
    assert out["listed"] == [
        {"op": "layers.addRaster", "description": ""},
        {"op": "layers.list", "description": "List layers"},
    ]
    assert out["described"] == {"op": "layers.list", "description": "List layers"}
    assert out["missing"] is None


def test_editor_control_invoke_and_run():
    out = run_node(
        """
        const calls = [];
        const control = createEditorControl({
          getState: () => ({ imgWidth: 640, imgHeight: 480 }),
          handlers: {
            'document.info': (params, ctx) => {
              calls.push(['info', params, ctx.state]);
              return ctx.state;
            },
            'history.undo': () => 'undone',
            'boom': () => { throw new Error('nope'); },
          },
        });
        const info = await control.invoke('document.info', { a: 1 });
        const unknown = await control.invoke('does.not.exist');
        const failed = await control.invoke('boom');
        const run = await control.run([
          { op: 'document.info' },
          { op: 'history.undo' },
          { op: 'boom' },
          { op: 'history.undo' },
        ]);
        console.log(JSON.stringify({ info, unknown, failed, run, calls }));
        """
    )
    assert out["info"] == {
        "ok": True,
        "op": "document.info",
        "result": {"imgWidth": 640, "imgHeight": 480},
    }
    assert out["unknown"] == {
        "ok": False,
        "op": "does.not.exist",
        "error": "Unknown editor op: does.not.exist",
    }
    assert out["failed"] == {"ok": False, "op": "boom", "error": "nope"}
    # run() executes sequentially and stops at the first failure (so the
    # trailing history.undo is never reached).
    assert [r["op"] for r in out["run"]] == ["document.info", "history.undo", "boom"]
    assert out["run"][-1]["ok"] is False
    assert out["calls"][0][0] == "info"
    assert out["calls"][0][1] == {"a": 1}


def test_editor_control_handler_entry_shapes():
    out = run_node(
        """
        const control = createEditorControl({
          handlers: {
            plain: () => 'plain-ok',
            wrapped: { run: () => 'wrapped-ok', description: 'W' },
            ignored: null,
          },
        });
        console.log(JSON.stringify({
          plain: await control.invoke('plain'),
          wrapped: await control.invoke('wrapped'),
          hasIgnored: control.list().some((x) => x.op === 'ignored'),
        }));
        """
    )
    assert out["plain"] == {"ok": True, "op": "plain", "result": "plain-ok"}
    assert out["wrapped"] == {"ok": True, "op": "wrapped", "result": "wrapped-ok"}
    assert out["hasIgnored"] is False
