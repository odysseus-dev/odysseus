import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / 'static/js/editor/editor-agent.js').as_uri()


def run_node(body):
    script = textwrap.dedent(f"""
        import {{ createEditorAgent, EDITOR_AGENT_VERSION, DEFAULT_AGENT_ENDPOINT }} from {json.dumps(MODULE)};
        {body}
    """)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_agent_run_posts_payload_streams_steps_and_collapses_history():
    out = run_node("""
        const calls = [];
        const steps = [];
        const posts = [];
        const control = { invoke(op, params) {
          calls.push(op);
          if (op === 'document.info') return { ok: true, result: { width: 800, height: 600, activeLayerId: 'layer-1' } };
          if (op === 'layers.list') return { ok: true, result: [{ id: 'layer-1', name: 'Bg' }] };
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AA' } };
          if (op === 'history.mark') return { ok: true, result: 5 };
          if (op === 'history.collapse') return { ok: true, result: 1 };
          return { ok: true, result: null };
        } };
        const fetchImpl = async (url, opts) => {
          posts.push({ url, body: JSON.parse(opts.body) });
          return { ok: true, status: 200, json: async () => ({
            ok: true, reply: 'done',
            tools_offered: ['plan_edit', 'run_editor_command', 'get_editor_screenshot', 'ai_edit_image', 'generate_image'],
            steps: [{ op: 'adjustments.apply', params: { type: 'brightness-contrast' } }],
          }) };
        };
        const agent = createEditorAgent({ control, endpoint: '/api/editor/agent', fetchImpl, getSession: () => 'sess-1' });
        const p = agent.run('brighten the face', { onStep: (e) => steps.push(e.op + ':' + e.ok) });
        agent.reportStep({ ok: true, ms: 12 }, { op: 'selection.face' });
        const out = await p;
        console.log(JSON.stringify({ posts, calls, steps, out, version: EDITOR_AGENT_VERSION, def: DEFAULT_AGENT_ENDPOINT }));
    """)
    body = out['posts'][0]['body']
    assert out['posts'][0]['url'] == '/api/editor/agent'
    assert body['prompt'] == 'brighten the face'
    assert body['session'] == 'sess-1' and body['editor_session'] == 'sess-1'
    assert body['state']['width'] == 800 and len(body['state']['layers']) == 1
    assert body['screenshot'].startswith('data:image/png')
    assert 'history.mark' in out['calls'] and 'history.collapse' in out['calls']
    assert out['steps'] == ['selection.face:true']
    assert out['out']['ok'] is True
    assert 'ai_edit_image' in out['out']['toolsOffered']
    assert out['out']['trace'][0]['op'] == 'selection.face'
    assert out['version'] == '1.0'
    assert out['def'] == '/api/editor/agent'


def test_agent_run_includes_the_selected_edit_model_in_the_payload():
    out = run_node("""
        const posts = [];
        const control = { invoke(op) {
          if (op === 'document.info') return { ok: true, result: { width: 800, height: 600, activeLayerId: 'layer-1' } };
          if (op === 'layers.list') return { ok: true, result: [{ id: 'layer-1', name: 'Bg' }] };
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AA' } };
          if (op === 'history.mark') return { ok: true, result: 5 };
          if (op === 'history.collapse') return { ok: true, result: 1 };
          return { ok: true, result: null };
        } };
        const fetchImpl = async (url, opts) => {
          posts.push({ url, body: JSON.parse(opts.body) });
          return { ok: true, status: 200, json: async () => ({ ok: true, reply: 'done', steps: [] }) };
        };
        const agent = createEditorAgent({
          control, endpoint: '/api/editor/agent', fetchImpl, getSession: () => 'sess-1',
          getModel: () => ({ endpoint: 'http://seneca:8188/v1', model: 'qwen-image-edit-2511-8bit', label: 'Qwen-Image-Edit-2511' }),
          getAgentModel: () => ({ agent: 'Qwen3.5-9B-MLX-8bit', endpoint: 'http://seneca:8097/v1', label: 'Qwen3.5-9B VL · local' }),
        });
        await agent.run('brighten the face', {});
        console.log(JSON.stringify({ body: posts[0].body }));
    """)
    body = out['body']
    assert body['model'] == 'qwen-image-edit-2511-8bit'
    assert body['endpoint'] == 'http://seneca:8188/v1'
    assert body['model_label'] == 'Qwen-Image-Edit-2511'
    assert body['agent_model'] == 'Qwen3.5-9B-MLX-8bit'
    assert body['agent_endpoint'] == 'http://seneca:8097/v1'
    assert body['agent_label'] == 'Qwen3.5-9B VL · local'


def test_agent_run_is_unavailable_when_endpoint_missing():
    out = run_node("""
        const control = { invoke(op) {
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AA' } };
          if (op === 'history.mark') return { ok: true, result: 0 };
          return { ok: true, result: {} };
        } };
        const fetchImpl = async () => ({ ok: false, status: 404, json: async () => ({}) });
        const agent = createEditorAgent({ control, fetchImpl });
        const out = await agent.run('make it moody');
        console.log(JSON.stringify({ out }));
    """)
    assert out['out']['unavailable'] is True
    assert out['out']['ok'] is False


def test_agent_run_rolls_back_partial_edits_when_provider_missing():
    out = run_node("""
        let stack = 0;
        const undos = [];
        let collapses = 0;
        let agentRef = null;
        const control = { invoke(op) {
          if (op === 'document.info') return { ok: true, result: { width: 900, height: 600 } };
          if (op === 'layers.list') return { ok: true, result: [] };
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AA' } };
          if (op === 'history.mark') return { ok: true, result: stack };
          if (op === 'history.undo') { undos.push(1); if (stack > 0) stack--; return { ok: true, result: true }; }
          if (op === 'history.collapse') { collapses++; return { ok: true, result: 1 }; }
          return { ok: true, result: null };
        } };
        const fetchImpl = async () => {
          // Simulate the agent running two substitute ops through the bridge.
          stack = 2;
          agentRef.reportStep({ ok: true }, { op: 'selection.rectangle' });
          agentRef.reportStep({ ok: true }, { op: 'adjustments.apply' });
          return { ok: false, status: 200, json: async () => ({
            ok: false, needs_provider: true,
            reply: 'Removing objects needs an image generation provider.',
          }) };
        };
        agentRef = createEditorAgent({ control, fetchImpl, getSession: () => 's' });
        const out = await agentRef.run('remove the person on the left');
        console.log(JSON.stringify({ out, undos: undos.length, collapses, stack }));
    """)
    assert out['out']['blocked'] is True
    assert out['out']['needsProvider'] is True
    assert out['out']['ok'] is False
    assert out['out']['rolledBack'] == 2
    assert out['undos'] == 2
    assert out['collapses'] == 0
    assert out['stack'] == 0
    assert 'image generation provider' in out['out']['message'].lower()


def test_agent_module_is_precached_for_offline_use():
    sw = (ROOT / 'static/sw.js').read_text(encoding='utf-8')
    assert "'/static/js/editor/editor-agent.js'" in sw


def test_agent_has_skin_teeth_and_formatcrop_guards():
    src = (ROOT / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    assert "control.invoke('retouch.skin'" in src
    assert "control.invoke('retouch.teeth'" in src
    assert 'formatCropRect(prompt' in src
    assert 'isGenericIntent' in src
    assert 'dataUrlDelta' in src


def test_agent_skips_format_crop_for_extend_intent():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    assert 'extendIntent' in src
    assert 'extend|expand' in src
    assert 'alreadyFormatted ? null : formatCropRect' in src


def test_agent_suppresses_autotone_for_portrait_intents():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    # Portrait/teeth runs must not lead with a whole-image Auto Tone (it lightens
    # and whitens the face) unless the photo is genuinely under/over-exposed.
    assert 'dataUrlExposure' in src
    assert 'portraitIntent' in src
    assert 'teethIntent' in src
    assert 'portrait-no-auto-tone' in src
    assert 'restoreInvoke' in src
    assert 'exposure.dark > 0.32' in src



def test_agent_does_not_strengthen_a_clarification():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    # A zero-step plan (the planner asked a question) must skip every post-guard,
    # especially the imperceptible-edit tone strengthen, and be returned blocked.
    assert 'const asked = !blocked && planSteps.length === 0' in src
    assert '} else if (!blocked && !asked) {' in src
    assert 'if (blocked || asked) {' in src


def test_agent_strengthen_guard_only_for_generic_intents():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'static/js/editor/editor-agent.js').read_text(encoding='utf-8')
    # Structural/action prompts must not get a spurious vibrance/brightness tweak.
    assert 'if (beforeShot && generic) {' in src
