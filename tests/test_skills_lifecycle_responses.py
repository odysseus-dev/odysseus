"""Rejected skill mutations must not be presented as completed lifecycle actions."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(action, outcome=500, *, status='published', initial='draft', confirm=True, pending=False):
    if not shutil.which('node'):
        pytest.skip('Node is required to execute the shipped Skills controllers')
    script = r"""
const fs=require('fs'),vm=require('vm');const options=JSON.parse(process.argv[1]);
const toasts=[],errors=[],calls=[],timers=[],cleared=[],classes=new Set();let release;
const button={textContent:options.initial==='published'?'Approved':'Approve',title:'',classList:{toggle(){}}};
const handlers={};button.addEventListener=(type,fn)=>{handlers[type]=fn};
const card={dataset:{skillStatus:options.initial},_testPoll:15,parentElement:true,
 classList:{add:c=>classes.add(c)},addEventListener(){},remove(){this.parentElement=null},querySelector:()=>null};
const verdict={innerHTML:'',querySelector:s=>s==='[data-act="approve"]'?button:null};
const context={options,card,verdict,toasts,errors,calls,cleared,
 window:{location:{origin:'http://fixture.test'}},
 localStorage:{getItem:()=>null},document:{addEventListener(){},getElementById:()=>null,querySelectorAll:()=>[]},
 uiModule:{esc:s=>String(s),styledConfirm:async()=>options.confirm,showToast:s=>toasts.push(s),showError:s=>errors.push(s)},
 setTimeout:fn=>{timers.push(fn);return timers.length},clearInterval:id=>cleared.push(id),
 fetch:async(url,init)=>{
  calls.push({url,...init});
  if(options.pending)return await new Promise(resolve=>{release=resolve});
  if(options.outcome==='network')throw Error('Synthetic network failure');
  return {ok:options.outcome>=200&&options.outcome<300,status:options.outcome};
 }};
vm.createContext(context);
let source=fs.readFileSync('static/js/skills.js','utf8');
source=source.replace(/^import[\s\S]*?;\s*$/gm,'').replace(/export default[^;]+;/g,'').replace(/^export /gm,'');
vm.runInContext(source,context);
vm.runInContext("let reloads=0;loadSkills=async()=>{reloads++};_setCardRunning=()=>{};_applyVerdictToHeader=()=>{};_mdCache.set('fixture','Owned source');",context);
function snapshot(){return {toasts:[...toasts],errors:[...errors],reloads:vm.runInContext('reloads',context),cached:vm.runInContext("_mdCache.has('fixture')",context),deleting:classes.has('doclib-card-deleting'),poll:card._testPoll,status:card.dataset.skillStatus,label:button.textContent,timers:timers.length,cleared:[...cleared]}}
(async()=>{
 let operation;
 if(options.action==='delete')operation=vm.runInContext("_deleteSkill('fixture',card)",context);
 else if(options.action==='status')operation=vm.runInContext("_setSkillStatus('fixture',options.status)",context);
 else{vm.runInContext("_renderTestVerdict(verdict,{verdict:'pass',summary:'Synthetic result',issues:[]},card,'fixture')",context);operation=handlers.click({stopPropagation(){}})}
 let before;
 if(options.pending){await new Promise(setImmediate);before=snapshot();if(!release)throw Error('Expected pending mutation');release({ok:true,status:200})}
 await operation;console.log(JSON.stringify({...snapshot(),calls,before}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script, json.dumps(dict(action=action, outcome=outcome, status=status, initial=initial, confirm=confirm, pending=pending))], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('outcome', [403, 404, 500, 'network'])
def test_rejected_delete_preserves_source_card_and_polling(outcome):
    result = _run('delete', outcome)
    assert result['toasts'] == []
    assert len(result['errors']) == 1
    assert result['reloads'] == result['timers'] == 0
    assert result['cached'] and not result['deleting']
    assert result['poll'] == 15 and result['cleared'] == []
    assert result['calls'][0]['method'] == 'DELETE'


@pytest.mark.parametrize('status', ['published', 'draft'])
@pytest.mark.parametrize('outcome', [500, 'network'])
def test_rejected_status_change_has_no_success_or_reload(status, outcome):
    result = _run('status', outcome, status=status)
    assert result['toasts'] == [] and len(result['errors']) == 1
    assert result['reloads'] == 0
    assert json.loads(result['calls'][0]['body']) == {'status': status}


@pytest.mark.parametrize('initial', ['draft', 'published'])
def test_rejected_test_result_toggle_keeps_approval_label_and_status(initial):
    result = _run('expanded', 500, initial=initial)
    assert result['status'] == initial
    assert result['label'] == ('Approved' if initial == 'published' else 'Approve')
    assert result['toasts'] == [] and len(result['errors']) == 1


@pytest.mark.parametrize('outcome', [200, 204])
def test_accepted_delete_keeps_existing_cleanup_and_feedback(outcome):
    result = _run('delete', outcome)
    assert result['toasts'] == ['Skill deleted'] and result['errors'] == []
    assert result['reloads'] == result['timers'] == 1
    assert not result['cached'] and result['deleting']
    assert result['poll'] is None and result['cleared'] == [15]


@pytest.mark.parametrize('initial', ['draft', 'published'])
def test_accepted_result_toggle_updates_approval_state(initial):
    result = _run('expanded', 200, initial=initial)
    assert result['status'] == ('published' if initial == 'draft' else 'draft')
    assert result['label'] == ('Approved' if initial == 'draft' else 'Approve')
    assert result['reloads'] == 1 and result['errors'] == []


def test_cancelled_delete_sends_no_request():
    result = _run('delete', confirm=False)
    assert result['calls'] == result['errors'] == result['toasts'] == []
    assert result['cached'] and not result['deleting'] and result['reloads'] == 0


def test_pending_delete_does_not_clear_source_or_stop_polling():
    result = _run('delete', 200, pending=True)
    before = result['before']
    assert before['cached'] and not before['deleting']
    assert before['poll'] == 15 and before['cleared'] == []
    assert before['toasts'] == [] and before['reloads'] == 0
    assert result['toasts'] == ['Skill deleted']
