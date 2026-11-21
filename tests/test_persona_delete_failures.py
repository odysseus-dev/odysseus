"""Run the shipped persona delete handler against rejected/successful responses."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='requires Node')


def run_case(*, status=200, success=True, network=False, malformed=False, saved=True, builtin=True, confirm=True):
    source = (ROOT / 'static/js/presets.js').read_text(encoding='utf-8')
    start = source.index('function initNameDropdown()')
    handler = source[start:source.index('\nfunction _tryLoadTemplate(', start)]
    payload = json.dumps(dict(status=status, success=success, network=network, malformed=malformed,
                              saved=saved, builtin=builtin, confirm=confirm))
    script = '''
const config=JSON.parse(process.argv[1]);
const listeners={},errors=[],requests=[],storage=new Map(),elements={};
const name=config.builtin?'Socrates':'Owned persona';
function element(id,value=''){return elements[id]={value,style:{},classList:{remove(){}},
addEventListener(type,fn){(listeners[id+':'+type]??=[]).push(fn)},
dispatchEvent(event){for(const fn of listeners[id+':'+event.type]||[])fn(event)}}}
element('char-template-select',name);element('char-delete-template-btn');element('custom-character-name',name);
element('custom-system-prompt','OWNED UNSAVED PROMPT');element('character-indicator-btn');element('overflow-preset-btn');
const document={getElementById:id=>elements[id]||null};
const localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
const window={styledConfirm:async()=>config.confirm};
const uiModule={showError:message=>errors.push(message)};
window.uiModule=uiModule;
const API_BASE='',PROMPT_TEMPLATES=config.builtin?[{name,isCharacter:true}]:[];
let selectedPreset='custom',presets={custom:{character_name:name,system_prompt:'Saved prompt',enabled:true,persona_memory:'Saved continuity',persona_memory_schema:'general'}};
let userTemplates=config.saved?[{id:'owned',name}]:[],reloads=0,syncs=0;
function loadStoredArray(key){return JSON.parse(localStorage.getItem(key)||'[]')}
async function loadUserTemplates(){reloads++}
function _syncCharIndicator(){syncs++}
const fetch=async(url,options)=>{requests.push({url,method:options.method});if(config.network)throw Error('owned network failure');
return {ok:config.status>=200&&config.status<300,status:config.status,json:async()=>{if(config.malformed)throw Error('owned invalid JSON');return {success:config.success}}}};
'''
    script += handler
    script += '''
initNameDropdown();
(async()=>{await listeners['char-delete-template-btn:click'][0]();await new Promise(resolve=>setTimeout(resolve,1));
console.log(JSON.stringify({selected:elements['char-template-select'].value,name:elements['custom-character-name'].value,
prompt:elements['custom-system-prompt'].value,hidden:loadStoredArray('odysseus-hidden-presets'),selectedPreset,
custom:presets.custom,errors,requests,reloads,syncs}));})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([NODE, '-e', script, payload], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('kwargs', [
    {'status': 403}, {'status': 404}, {'status': 500}, {'status': 200, 'success': False},
    {'status': 200, 'success': None}, {'network': True}, {'malformed': True},
])
def test_rejected_delete_keeps_draft_visibility_activation_and_reports_error(kwargs):
    result = run_case(**kwargs)
    assert result['selected'] == result['name'] == 'Socrates'
    assert result['prompt'] == 'OWNED UNSAVED PROMPT'
    assert result['hidden'] == [] and result['selectedPreset'] == 'custom'
    assert result['custom']['enabled'] is True and result['custom']['persona_memory'] == 'Saved continuity'
    assert result['reloads'] == result['syncs'] == 0
    assert len(result['requests']) == 1 and len(result['errors']) == 1


@pytest.mark.parametrize('builtin', [True, False])
def test_successful_saved_delete_retains_existing_completion_behavior(builtin):
    result = run_case(builtin=builtin)
    assert result['selected'] == '__default__'
    assert result['prompt'] == result['name'] == ''
    assert result['selectedPreset'] is None and result['custom']['enabled'] is False
    assert result['hidden'] == (['Socrates'] if builtin else [])
    assert result['errors'] == [] and result['reloads'] == 1 and len(result['requests']) == 1


def test_builtin_only_hide_needs_no_server_write():
    result = run_case(saved=False)
    assert result['selected'] == '__default__' and result['hidden'] == ['Socrates']
    assert result['requests'] == result['errors'] == []


def test_cancel_preserves_the_entire_edit_without_network():
    result = run_case(confirm=False)
    assert result['selected'] == result['name'] == 'Socrates'
    assert result['prompt'] == 'OWNED UNSAVED PROMPT'
    assert result['hidden'] == [] and result['selectedPreset'] == 'custom'
    assert result['requests'] == result['errors'] == [] and result['reloads'] == 0
