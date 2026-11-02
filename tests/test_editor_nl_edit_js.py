import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / 'static/js/editor/nl-edit.js').as_uri()


def run_node(body):
    script = textwrap.dedent(f"""
        import {{ localPlan, createNaturalLanguageEditor, sanitizeSteps, ensureFormatCrop, ensureGenericAutoTone, isGenericIntent, formatCropRect, setStyleGuide, getStyleGuide, activeGuidance, fetchStyleGuide }} from {json.dumps(MODULE)};
        {body}
    """)
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_local_plan_covers_layer_transforms_fill_text_align_styles_extend():
    out = run_node("""
        const ops = (p, st) => localPlan(p, st || {}).steps.map(s => s.op + ':' + JSON.stringify(s.params));
        const cases = {
          rotateLayer: ops('rotate this layer 90 degrees'),
          rotateWhole: ops('rotate the whole image 90'),
          flipLayer: ops('flip this layer vertically'),
          flipWhole: ops('flip the whole canvas horizontal'),
          fill: ops('fill the white area with red'),
          fillRegion: ops('fill the sky with blue'),
          textBigger: ops('make the title bigger'),
          align: ops('align the logo to the centre'),
          dropShadow: ops('add a drop shadow'),
          stroke: ops('add a stroke to the text'),
          textAndShadow: ops('add large centred title text in white with a drop shadow'),
          textAndAlign: ops('add a subtitle under the title and align it to the centre'),
          withText: ops('turn it into a vintage travel poster with the text EXPLORE'),
          extend: ops('extend the canvas to 16:9', { width: 600, height: 600 }),
          copy: ops('copy the selection'),
          paste: ops('paste the copied selection'),
        };
        console.log(JSON.stringify(cases));
    """)
    assert out['rotateLayer'] == ['layer.rotate:{"degrees":90}']
    assert out['rotateWhole'] == ['document.rotate:{"degrees":90}']
    assert out['flipLayer'] == ['layer.flip:{"axis":"v"}']
    assert out['flipWhole'] == ['document.flip:{"axis":"h"}']
    assert out['fill'][0].startswith('fill.flood:') and '#ff0000' in out['fill'][0]
    assert any(s.startswith('selection.fill:') for s in out['fillRegion'])
    assert out['textBigger'] == ['text.edit:{"size":96}']
    assert out['align'] == ['layers.align:{"mode":"center"}']
    assert out['dropShadow'] == ['layers.addStyle:{"type":"drop-shadow","params":{}}']
    assert out['stroke'] == ['layers.addStyle:{"type":"stroke","params":{}}']
    assert out['textAndShadow'][0].startswith('text.add:')
    assert any(s.startswith('text.layout') for s in out['textAndShadow'])
    assert out['textAndShadow'][-1] == 'layers.addStyle:{"type":"drop-shadow","params":{}}'
    assert out['textAndAlign'][0] == 'text.add:{"text":"Subtitle","x":0,"y":0,"color":"#ffffff","size":64}'
    assert 'layers.align:{"mode":"center"}' in out['textAndAlign']
    assert out['withText'][0] == 'text.add:{"text":"EXPLORE","x":0,"y":0,"color":"#ffffff","size":64}'
    assert out['extend'] == ['document.extend:{"width":1067,"height":600,"anchor":"center"}']
    assert out['copy'] == ['selection.copy:{}']
    assert out['paste'] == ['selection.paste:{}']


def test_local_plan_maps_common_prompts():
    out = run_node("""
        const cases = {
          grayscale: localPlan('make it black and white').steps.map(s => s.op + ':' + (s.params.type || s.params.id || '')),
          dramatic: localPlan('make the sky more dramatic and vivid').steps.map(s => s.op + ':' + (s.params.type || s.params.id || '')),
          title: localPlan('add a title Tokyo at the top in white').steps.map(s => s.op),
          titleText: localPlan('add a title Tokyo at the top in white').steps.find(s => s.op === 'text.add').params.text,
          blur: localPlan('blur the background').steps.map(s => s.op + ':' + (s.params.id || '')),
          blurSlight: localPlan('blur it slightly').steps[0].params.radius,
          soften: localPlan('soften it').steps.map(s => s.op),
          bokeh: localPlan('add a bokeh depth of field').steps[0].params.radius,
          empty: localPlan('do something nice').steps.length,
        };
        console.log(JSON.stringify(cases));
    """)
    assert 'adjustments.apply:black-white' in out['grayscale']
    assert any(s.startswith('selection.sky') for s in out['dramatic'])
    assert any(s.startswith('adjustments.apply') for s in out['dramatic'])
    assert 'text.add' in out['title']
    assert out['titleText'] == 'Tokyo'
    assert 'filters.applyMasked:blur-gaussian' in out['blur']
    assert out['blurSlight'] == 4
    assert 'retouch.skin' in out['soften']
    assert out['bokeh'] == 18
    assert out['empty'] >= 1


def test_sanitize_steps_drops_noops_and_guards_masked_ops():
    out = run_node("""
        const cases = {
          face: sanitizeSteps([
            { op: 'document.info' },
            { op: 'adjustments.applyMasked', params: { brightness: 20 } },
            { op: 'history.mark' },
            { op: 'export.png' },
          ], 'brighten the face').map(s => s.op),
          sky: sanitizeSteps([
            { op: 'selection.sky' },
            { op: 'adjustments.applyMasked', params: { contrast: 30 } },
          ], 'make the sky more dramatic').map(s => s.op),
          blur: sanitizeSteps([
            { op: 'filters.applyMasked', params: { id: 'blur-gaussian', invert: true } },
          ], 'blur the background behind the person').map(s => s.op),
        };
        console.log(JSON.stringify(cases));
    """)
    assert out['face'] == ['selection.face', 'adjustments.applyMasked']
    assert out['sky'] == ['selection.sky', 'adjustments.applyMasked']
    assert out['blur'] == ['selection.background', 'filters.applyMasked']


def test_ensure_format_crop_prepends_crop_for_platform_requests():
    out = run_node("""
        const square = ensureFormatCrop([{ op: 'adjustments.apply', params: {} }], 'make it instagram ready', 900, 600);
        const story = ensureFormatCrop([], 'make it a 9:16 story', 900, 600);
        const already = ensureFormatCrop([{ op: 'document.crop', params: { x: 1, y: 2, width: 3, height: 4 } }], 'instagram', 900, 600);
        const none = ensureFormatCrop([{ op: 'adjustments.apply', params: {} }], 'make it pop', 900, 600);
        console.log(JSON.stringify({ square, story, already, none }));
    """)
    assert out['square'][0]['op'] == 'document.crop'
    assert out['square'][0]['params'] == {'x': 150, 'y': 0, 'width': 600, 'height': 600}
    assert out['story'][0]['op'] == 'document.crop'
    assert out['story'][0]['params']['width'] == 338 and out['story'][0]['params']['height'] == 600
    assert out['already'][0]['params'] == {'x': 1, 'y': 2, 'width': 3, 'height': 4}
    assert out['none'][0]['op'] == 'adjustments.apply'


def test_format_crop_rect_prefers_explicit_dimensions_over_platform_name():
    out = run_node("""
        // "Instagram" alone means 1:1, but an explicit 1080x1350 must win (4:5).
        const explicit = formatCropRect('make a 1080x1350 Instagram post from this', 900, 600);
        const named = formatCropRect('make it instagram ready', 900, 600);
        console.log(JSON.stringify({ explicit, named }));
    """)
    assert out['explicit'] is not None
    # 4:5 of a 900x600 canvas: height-bound -> 480x600, centred at x=210.
    assert out['explicit'] == {'x': 210, 'y': 0, 'width': 480, 'height': 600}
    assert out['named'] == {'x': 150, 'y': 0, 'width': 600, 'height': 600}


def test_execute_runs_steps_and_collapses_history():
    out = run_node("""
        const calls = [];
        const control = {
          invoke(op, params) {
            calls.push(op);
            if (op === 'document.info') return { ok: true, result: { width: 100, height: 100 } };
            if (op === 'history.mark') return { ok: true, result: 7 };
            if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AAAA' } };
            if (op === 'history.collapse') return { ok: true, result: 1 };
            return { ok: true, result: true };
          },
        };
        const nl = createNaturalLanguageEditor({ control, planUrl: null, fetchImpl: null });
        const done = await nl.execute('make it black and white');
        console.log(JSON.stringify({ calls, ok: done.ok, planner: done.planner, traceLen: done.trace.length, shot: done.trace[0].screenshot }));
    """)
    assert out['ok'] is True
    assert out['planner'] == 'local'
    assert out['traceLen'] >= 1
    assert out['shot'].startswith('data:image/png')
    assert 'history.mark' in out['calls']
    assert 'history.collapse' in out['calls']
    assert 'screenshot' in out['calls']


def test_execute_stops_at_first_failure():
    out = run_node("""
        const control = {
          invoke(op, params) {
            if (op === 'document.info') return { ok: true, result: {} };
            if (op === 'history.mark') return { ok: true, result: 0 };
            if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AAAA' } };
            if (op === 'history.collapse') return { ok: true, result: 0 };
            if (op === 'adjustments.apply') return { ok: false, error: 'boom' };
            return { ok: true, result: true };
          },
        };
        const nl = createNaturalLanguageEditor({ control, planUrl: null, fetchImpl: null });
        const done = await nl.execute('warm and bright');
        console.log(JSON.stringify({ ok: done.ok, failed: done.failed, traceLen: done.trace.length }));
    """)
    assert out['ok'] is False
    assert out['failed']['error'] == 'boom'
    assert out['traceLen'] == 1


def test_execute_prefers_server_planner_when_available():
    out = run_node("""
        const control = { invoke(op) {
          if (op === 'document.info') return { ok: true, result: {} };
          if (op === 'history.mark') return { ok: true, result: 0 };
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AAAA' } };
          if (op === 'history.collapse') return { ok: true, result: 0 };
          return { ok: true, result: true };
        } };
        const fetchImpl = async (url, opts) => {
          const body = JSON.parse(opts.body);
          if (body.mode === 'check') return { ok: true, json: async () => ({ reply: 'done', done: true, steps: [] }) };
          return { ok: true, json: async () => ({ reply: 'server plan', planner: 'llm', steps: [{ op: 'selection.selectAll' }] }) };
        };
        const nl = createNaturalLanguageEditor({ control, planUrl: '/api/editor/plan', fetchImpl });
        const done = await nl.execute('anything at all goes here');
        console.log(JSON.stringify({ planner: done.planner, reply: done.reply, op: done.steps[0].op, ran: done.trace[0].op }));
    """)
    assert out['planner'] == 'llm'
    assert out['reply'] == 'server plan'
    assert out['op'] == 'selection.selectAll'
    assert out['ran'] == 'selection.selectAll'


def test_execute_self_check_reverts_before_corrected_plan():
    out = run_node("""
        let stack = 0, undoCount = 0;
        const applied = [];
        const control = { invoke(op, params) {
          if (op === 'document.info') return { ok: true, result: {} };
          if (op === 'history.mark') return { ok: true, result: stack };
          if (op === 'history.collapse') return { ok: true, result: 0 };
          if (op === 'screenshot') return { ok: true, result: { dataUrl: 'data:image/png;base64,AAAA' } };
          if (op === 'history.undo') { undoCount++; stack = Math.max(0, stack - 1); return { ok: true, result: true }; }
          stack++;
          applied.push(op + ':' + JSON.stringify(params || {}));
          return { ok: true, result: true };
        } };
        let n = 0;
        const fetchImpl = async (url, opts) => {
          const body = JSON.parse(opts.body);
          if (!body.mode) return { ok: true, json: async () => ({ reply: '', planner: 'llm', steps: [{ op: 'adjustments.apply', params: { type: 'black-white' } }] }) };
          n++;
          if (n === 1) return { ok: true, json: async () => ({ reply: 'retry', steps: [{ op: 'adjustments.apply', params: { type: 'brightness-contrast' } }] }) };
          return { ok: true, json: async () => ({ reply: 'done', done: true, steps: [] }) };
        };
        const nl = createNaturalLanguageEditor({ control, planUrl: '/api/editor/plan', fetchImpl });
        const done = await nl.execute('make it black and white');
        console.log(JSON.stringify({ applied, undoCount, traceLen: done.trace.length, ok: done.ok, steps: done.steps.length }));
    """)
    assert out['undoCount'] >= 1
    assert out['applied'].count('adjustments.apply:{"type":"black-white"}') == 1
    assert out['applied'].count('adjustments.apply:{"type":"brightness-contrast"}') == 1
    assert out['applied'][-1] == 'adjustments.apply:{"type":"brightness-contrast"}'
    assert out['ok'] is True


def test_generic_intent_helpers():
    out = run_node("""
        const generic = ['edit this photo', 'make it pretty', 'make it pop', 'make the colors nicer', 'clean this up', 'fix the lighting', 'make it look professional'].map(p => [p, isGenericIntent(p)]);
        const notGeneric = ['remove the person on the left', 'make the sky more dramatic', 'add a title WILDERNESS'].map(p => [p, isGenericIntent(p)]);
        const auto = ensureGenericAutoTone([{ op: 'adjustments.apply', params: { type: 'vibrance' } }], 'make it pretty');
        const notAuto = ensureGenericAutoTone([{ op: 'adjustments.apply', params: {} }], 'remove the person');
        const crop = formatCropRect('make it instagram ready', 900, 600);
        console.log(JSON.stringify({ generic, notGeneric, auto, notAuto, crop }));
    """)
    assert all(flag for _, flag in out['generic']), out['generic']
    assert not any(flag for _, flag in out['notGeneric']), out['notGeneric']
    assert out['auto'][0]['op'] == 'photo.autoTone'
    assert out['notAuto'][0]['op'] == 'adjustments.apply'
    assert out['crop'] == {'x': 150, 'y': 0, 'width': 600, 'height': 600}


def test_style_guide_drives_fallback_planner():
    out = run_node("""
        const fakeFetch = async (url) => ({
          ok: true,
          json: async () => ({
            version: 1,
            guidance: 'GUIDE-TEXT-MARKER',
            intents: [
              { id: 'clean', keywords: ['clean this up'], steps: [{ op: 'photo.autoTone', params: {} }, { op: 'adjustments.apply', params: { type: 'vibrance', params: { vibrance: 18 } } }], reply: 'Cleaned.' },
            ],
          }),
        });
        const before = activeGuidance().includes('GUIDE-TEXT-MARKER');
        const fetched = await fetchStyleGuide('/api/editor/style-guide', fakeFetch);
        const after = activeGuidance() === 'GUIDE-TEXT-MARKER';
        const planned = localPlan('clean this up', {});
        const unmatched = localPlan('make the sky more dramatic', {});
        console.log(JSON.stringify({ before, after, version: fetched && fetched.version, planned, unmatchedOps: unmatched.steps.map(s => s.op) }));
    """)
    assert out['before'] is False            # no guide yet -> built-in instruction
    assert out['after'] is True              # guidance replaced by the served guide
    assert out['version'] == 1
    assert out['planned']['matched'] == 'clean'
    assert out['planned']['steps'][0]['op'] == 'photo.autoTone'
    assert out['planned']['reply'] == 'Cleaned.'
    assert 'selection.sky' in out['unmatchedOps']  # falls back to the built-in regex planner
