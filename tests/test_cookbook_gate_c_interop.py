"""Execute connected JS functions against typed backend metadata; no model processes."""
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / 'static/js/cookbookServe.js'
NODE = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/\r\n/g, '\n');
const pick = name => {
  const start = source.indexOf('async function ' + name + '(');
  return source.slice(start, source.indexOf('\n}\n', start) + 3);
};
const calls = [], events = [], confirmations = [], toasts = [];
const context = {
  _envState: {hostPlatform:'windows'},
  _ensureManagedRuntime: async () => ({exe:'fixture-llama-server.exe', cpuOnly:true}),
  _fetchCookbookWithTimeout: async (url, options, timeout) => {
    calls.push({url, body:JSON.parse(options.body), timeout});
    let data = input.plan;
    if (url.endsWith('/start')) data = {ok:true,session_id:'fixture-sid',endpoint_id:'fixture-endpoint'};
    if (url.endsWith('/wait-ready')) data = input.ready;
    return {ok:!input.planFailed, json:async () => data};
  },
  uiModule: {showToast: text => toasts.push(text)},
  CustomEvent: class {constructor(type, init){this.type=type;this.detail=init.detail;}},
  window: {
    styledConfirm: async text => {confirmations.push(text);return input.confirm;},
    modelsModule: {refreshModels:async (...args) => calls.push({refresh:args})},
    sessionModule: {updateModelPicker:() => calls.push({picker:true})},
    dispatchEvent: event => events.push(event),
  },
};
vm.createContext(context);
vm.runInContext(pick('_followManagedReadiness')+'\n'+pick('_launchManagedLlama'), context);
(async () => {
 if (input.followOnly) await context._followManagedReadiness('fixture-sid','fixture-endpoint','Friendly label');
 else await context._launchManagedLlama('Friendly label','local', {path:'F:/fixture',is_local_dir:true},
   {backend:'llamacpp',llama_mode:'cpu',gguf_file:'fixture.gguf',ctx:512,port:18101});
 await new Promise(resolve => setImmediate(resolve));
 process.stdout.write(JSON.stringify({calls,events,confirmations,toasts}));
})().catch(error => {console.error(error);process.exitCode=1;});
"""


def run_js(payload):
    result = subprocess.run(['node', '-e', NODE, str(SOURCE)], input=json.dumps(payload),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.asyncio
async def test_real_typed_ready_payload_reaches_picker_with_30_second_bound(monkeypatch, tmp_path):
    from src import model_serving_control as control, model_serving_status as status

    class Controller:
        def __init__(self, *args):
            pass

        def recorded_plan(self, sid):
            return {'engine':'llamacpp', 'base_url':'http://127.0.0.1:18101'}

        def _state(self):
            return {'tasks':[{'sessionId':'fixture-sid', 'status':'running'}]}

    async def metadata_peer(*args):
        return {'readiness':'ready', 'models':['fixture-runtime-id'], 'loaded_models':[]}

    monkeypatch.setattr(control, 'LocalServingController', Controller)
    monkeypatch.setattr(status, 'task_process_identity', lambda *args:
                        {'pid':123, 'process_ownership':'owned'})
    monkeypatch.setattr(status, 'probe_local_runtime', metadata_peer)
    ready = await status.wait_for_managed_runtime(tmp_path/'state.json', tmp_path, 'fixture-sid', 30)
    result = run_js({'followOnly':True, 'ready':ready})
    event = result['events'][0]['detail']
    assert event == {'endpointId':'fixture-endpoint','sessionId':'fixture-sid',
                     'ready':True,'models':['fixture-runtime-id']}
    request = next(c for c in result['calls'] if c.get('url'))
    assert request['body']['timeout_seconds'] == 30
    assert request['timeout'] <= 45000
    assert any(c.get('refresh') == [True, {'waitForRefresh':True}] for c in result['calls'])


@pytest.mark.parametrize('confirm,plan_failed,starts',
                         [(False, False, False), (True, False, True), (True, True, False)])
def test_real_unknown_fit_plan_confirmation_and_failure_contract(tmp_path, confirm, plan_failed, starts):
    from src.model_serving_control import build_launch_plan
    import os
    exe = tmp_path / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
    exe.write_bytes(b'fixture only; never executed')
    model = tmp_path / 'fixture.gguf'
    model.write_bytes(b'GGUF')
    plan = build_launch_plan(engine='llamacpp', executable=str(exe),
                             model_path=str(model), port=18101, context_length=512, gpu_layers=0)
    assert plan['memory_validation'] == 'unknown'
    result = run_js({'plan':plan, 'confirm':confirm, 'planFailed':plan_failed,
                     'ready':{'ok':False, 'state':'startup-timeout'}})
    urls = [c['url'] for c in result['calls'] if c.get('url')]
    assert urls[0].endswith('/plan')
    start = [c for c in result['calls'] if c.get('url', '').endswith('/start')]
    assert bool(start) is starts
    if starts:
        assert start[0]['body']['allow_unestimated_context'] is True
        assert start[0]['body']['context_length'] == 512
        assert start[0]['body']['gpu_layers'] == 0
    assert bool(result['confirmations']) is (not plan_failed)


def test_timeout_never_publishes_a_ready_model_selection():
    result = run_js({'followOnly':True, 'ready':{'ok':False, 'state':'startup-timeout',
                                               'models':['stale-fixture-model']}})
    assert result['events'][0]['detail']['ready'] is False
    assert result['events'][0]['detail']['models'] == []
    assert 'is not ready' in result['toasts'][0]
