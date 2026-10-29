import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / 'static/js/editor/editor-bridge.js').as_uri()


def run_node(body):
    script = textwrap.dedent(f"""
        import {{ startEditorBridge, BRIDGE_VERSION }} from {json.dumps(MODULE)};
        {body}
    """)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_bridge_runs_queued_command_and_posts_result():
    out = run_node("""
        const calls = [];
        const posts = [];
        class FakeES {
          constructor(url) { this.url = url; FakeES.last = this; }
          close() { this.closed = true; }
        }
        const control = { invoke(op, params) {
          calls.push(op);
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,ZZ' } };
          return { ok: true, result: 'r:' + op };
        } };
        const fetchImpl = async (url, opts) => { posts.push({ url, body: JSON.parse(opts.body) }); return { ok: true, json: async () => ({ ok: true }) }; };
        const bridge = startEditorBridge({ control, session: 's1', baseUrl: '/x', EventSourceImpl: FakeES, fetchImpl });
        FakeES.last.onmessage({ data: JSON.stringify({ id: 'c1', op: 'history.undo', params: {} }) });
        await new Promise(r => setTimeout(r, 25));
        console.log(JSON.stringify({ esUrl: FakeES.last.url, calls, posts, session: bridge.session, version: BRIDGE_VERSION }));
    """)
    assert out['esUrl'] == '/x/api/editor/bridge/events?session=s1'
    assert 'history.undo' in out['calls']
    assert 'screenshot' in out['calls']
    assert out['posts'][0]['url'] == '/x/api/editor/bridge/result'
    assert out['posts'][0]['body']['id'] == 'c1'
    assert out['posts'][0]['body']['ok'] is True
    assert out['posts'][0]['body']['screenshot'].startswith('data:image/png')
    assert out['session'] == 's1'
    assert out['version'] == '1.0'


def test_bridge_reports_op_failure_and_stop_closes_stream():
    out = run_node("""
        const posts = [];
        class FakeES {
          constructor(url) { this.url = url; FakeES.last = this; }
          close() { this.closed = true; }
        }
        const control = { invoke(op) {
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,ZZ' } };
          return { ok: false, error: 'nope' };
        } };
        const fetchImpl = async (url, opts) => { posts.push(JSON.parse(opts.body)); return { ok: true, json: async () => ({}) }; };
        const bridge = startEditorBridge({ control, session: 'default', EventSourceImpl: FakeES, fetchImpl });
        FakeES.last.onmessage({ data: JSON.stringify({ id: 'x', op: 'layers.delete', params: { id: 'layer-9' } }) });
        await new Promise(r => setTimeout(r, 25));
        bridge.stop();
        console.log(JSON.stringify({ body: posts[0], closed: !!FakeES.last.closed }));
    """)
    assert out['body']['ok'] is False
    assert out['body']['error'] == 'nope'
    assert out['body']['result'] is None
    assert out['closed'] is True


def test_bridge_module_is_precached_for_offline_use():
    sw = (ROOT / 'static/sw.js').read_text(encoding='utf-8')
    assert "'/static/js/editor/editor-bridge.js'" in sw
