"""An automatic fallback must not prevent resolving the configured default."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.js_modules import js_function_source

ROOT = Path(__file__).resolve().parents[1]
PICKER = (ROOT / 'static/js/modelPicker.js').read_text()


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
@pytest.mark.parametrize('case', ['fallback', 'manual', 'default', 'session', 'manual-during-fetch', 'no-default'])
def test_configured_default_and_selection_ownership(case):
    source = '\n'.join(js_function_source(name, PICKER) for name in [
        '_ensureDefaultPendingChat', '_ensureModelCacheForFallback', '_firstAvailableModel',
    ])
    script = r'''
const source = JSON.parse(process.argv[1]), mode = process.argv[2];
let pending = {url:'http://models/v1/chat/completions',modelId:'first',endpointId:'shared',source:'fallback'};
if (mode === 'manual' || mode === 'default') pending.source = mode;
const initial = {...pending};
const configured = {endpoint_url:pending.url,model:'preferred',endpoint_id:'shared'};
let fetches = 0, updates = 0;
const _deps = {getCurrentSessionId:()=> mode === 'session' ? 'existing' : null,
 getPendingChat:()=>pending,setPendingChat:value=>{pending=value;}};
const window = {modelsModule:{getCachedItems:()=>[{url:initial.url,models:['first','preferred'],endpoint_id:'shared'}]}};
const localStorage = {setItem(){}};
const API_BASE = 'http://app';
let _defaultChatPickInFlight = false, _defaultPendingSeq = 0;
function updateModelPicker(){updates++;}
async function fetch(){fetches++; if(mode==='manual-during-fetch') pending={...initial,modelId:'chosen',source:'manual'};
 return {ok:true,json:async()=>mode==='no-default'?{}:configured};}
// All helpers share the same lexical dependencies as the production function.
eval(source.replace('async function _ensureDefaultPendingChat','globalThis.ensureDefault = async function'));
await globalThis.ensureDefault();
console.log(JSON.stringify({pending,fetches,updates,inFlight:_defaultChatPickInFlight}));
'''
    # Script is a module so async fetch ordering is exercised without a browser shim.
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(source), case],
                            cwd=ROOT, capture_output=True, text=True, check=True, timeout=15)
    data = json.loads(result.stdout)
    assert data['inFlight'] is False
    if case == 'fallback':
        assert data['pending']['modelId'] == 'preferred'
        assert data['pending']['source'] == 'default'
        assert data['fetches'] == 1
    elif case == 'manual-during-fetch':
        assert data['pending']['modelId'] == 'chosen'
        assert data['pending']['source'] == 'manual'
    else:
        assert data['pending']['modelId'] == 'first'
        assert data['pending']['source'] == (case if case in {'manual', 'default'} else 'fallback')
        assert data['fetches'] == (1 if case == 'no-default' else 0)


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_cached_default_updates_visible_picker_after_fallback():
    names = ['_ensureDefaultPendingChat', '_ensureModelCacheForFallback', '_firstAvailableModel', '_modelExists', 'updateModelPicker']
    source = '\n'.join(js_function_source(name, PICKER).removeprefix('export ') for name in names)
    script = r'''
let pending={url:'http://models/v1/chat/completions',modelId:'first',endpointId:'shared',source:'fallback'};
const _deps={getCurrentSessionId:()=>null,getSessions:()=>[],getPendingChat:()=>pending,setPendingChat:value=>{pending=value;}};
const label={textContent:''};
const document={getElementById:id=>id==='model-picker-label'?label:null};
const window={__odysseusDefaultChat:{endpoint_url:pending.url,endpoint_id:'shared',model:'preferred'},
 modelsModule:{getCachedItems:()=>[{url:pending.url,endpoint_id:'shared',models:['first','preferred']}]}};
const localStorage={setItem(){}};
let _defaultChatPickInFlight=false,_defaultPendingSeq=0,_autoSelectingDefault=false;
function providerLogo(){return null;}function providerLabel(){return 'Fixture';}function _updateReasoningEffortUI(){}
eval(JSON.parse(process.argv[1])+'\nupdateModelPicker();');
console.log(JSON.stringify({pending,label:label.textContent}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(source)],
                            cwd=ROOT, capture_output=True, text=True, check=True, timeout=15)
    data = json.loads(result.stdout)
    assert data['pending']['modelId'] == 'preferred'
    assert 'preferred' in data['label']
