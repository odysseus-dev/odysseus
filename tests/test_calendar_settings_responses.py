"""Calendar settings must commit UI state only after the write succeeds."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(action, outcome=500, *, confirm=True, pending=False, change_while_pending=False):
    if not shutil.which('node'):
        pytest.skip('Node is required to execute the calendar controllers')
    script = r"""
const fs=require('fs'),vm=require('vm'),options=JSON.parse(process.argv[1]);
const toasts=[],errors=[],calls=[],removed=[],timers=[];let release;
function node(value=''){return {value,style:{},dataset:{},handlers:{},classList:{add(){},remove(){},toggle(){}},
 addEventListener(type,fn){this.handlers[type]=fn},setAttribute(){},remove(){this.deleted=true}}}
const name=node('Owned calendar'),color=node('#5b8abf'),del=node();
const row=node();row.dataset.id='owned';row.querySelector=s=>({'.cal-s-name':name,'.cal-s-color':color,'.cal-s-del':del}[s]);
const nodes=new Map();const overlay=node();overlay.querySelector=s=>{if(!nodes.has(s))nodes.set(s,node());return nodes.get(s)};
overlay.querySelectorAll=s=>s==='.cal-settings-row'?[row]:[];
const context={options,toasts,errors,calls,row,name,color,
 window:{location:{origin:'http://fixture.test'},addEventListener(){},styledConfirm:async()=>options.confirm},
 document:{addEventListener(){},removeEventListener(){},getElementById:()=>null,createElement:()=>overlay,body:{appendChild(){}}},
 localStorage:{getItem:()=>null,removeItem:key=>removed.push(key)},
 uiModule:{esc:s=>String(s),showToast:s=>toasts.push(s),showError:s=>errors.push(s)},
 attachColorPicker(){},setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout(){},
 fetch:async(url,init)=>{if(!init?.method)return {ok:true,status:200,json:async()=>({calendars:[],events:[]})};calls.push({url,...init});if(options.pending)return await new Promise(resolve=>{release=resolve});if(options.outcome==='network')throw Error('Synthetic network failure');return {ok:options.outcome>=200&&options.outcome<300,status:options.outcome}}};
vm.createContext(context);
let source=fs.readFileSync('static/js/calendar.js','utf8');
source=source.replace(/^import[\s\S]*?;\s*$/gm,'').replace(/export default[^;]+;/g,'').replace(/^export \{[^}]+\};?$/gm,'');
vm.runInContext(source,context);

function snapshot(){return {toasts:[...toasts],errors:[...errors],removed:[...removed],rowDeleted:!!row.deleted,
 ...JSON.parse(vm.runInContext('JSON.stringify({calendars:_calendars,events:_allEvents,ranges:_fetchedRanges,renders})',context))}}
(async()=>{await new Promise(setImmediate);calls.length=0;timers.length=0;removed.length=0;vm.runInContext("let renders=0;_render=()=>{renders++};_calendars=[{href:'owned',name:'Owned calendar',color:'#5b8abf'}];_allEvents={event:{calendar_href:'owned',calendar:'Owned calendar',color:'#5b8abf'}};_fetchedRanges=[['2026-01-01','2026-02-01']];",context);await vm.runInContext('_showCalSettings()',context);let operation;
 if(options.action==='save'){name.value='Draft calendar';color.value='#4caf50';name.handlers.change();operation=timers.at(-1)()}
 else operation=del.handlers.click();
 let before;if(options.pending){await new Promise(setImmediate);before=snapshot();if(!release)throw Error('No pending request');if(options.change_while_pending){name.value='Unsubmitted draft';color.value='#ff9800'}release({ok:true,status:200})}
 await operation;console.log(JSON.stringify({...snapshot(),calls,before,inputName:name.value}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script, json.dumps(dict(action=action, outcome=outcome, confirm=confirm, pending=pending, change_while_pending=change_while_pending))], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('action', ['save', 'delete'])
@pytest.mark.parametrize('outcome', [403, 404, 500, 'network'])
def test_rejected_write_keeps_calendar_events_and_cache(action, outcome):
    result = _run(action, outcome)
    assert result['toasts'] == [] and len(result['errors']) == 1
    assert result['calendars'] == [{'href': 'owned', 'name': 'Owned calendar', 'color': '#5b8abf'}]
    assert result['events']['event']['calendar'] == 'Owned calendar'
    assert result['events']['event']['color'] == '#5b8abf'
    assert result['ranges'] == [['2026-01-01', '2026-02-01']]
    assert result['removed'] == [] and result['renders'] == 0 and not result['rowDeleted']
    assert result['calls'][0]['method'] == ('PUT' if action == 'save' else 'DELETE')


@pytest.mark.parametrize('action', ['save', 'delete'])
@pytest.mark.parametrize('outcome', [200, 204])
def test_accepted_write_keeps_existing_success_updates(action, outcome):
    result = _run(action, outcome)
    assert result['errors'] == [] and result['renders'] == 1
    assert result['ranges'] == [] and result['removed'] == ['odysseus-calendar-cache']
    if action == 'save':
        assert result['calendars'][0]['name'] == 'Draft calendar'
        assert result['events']['event']['color'] == '#4caf50'
        assert result['toasts'] == ['Saved “Draft calendar”']
        assert not result['rowDeleted']
    else:
        assert result['calendars'] == [] and result['events'] == {} and result['rowDeleted']


def test_cancelled_delete_does_not_write_or_mutate():
    result = _run('delete', confirm=False)
    assert result['calls'] == result['errors'] == result['toasts'] == result['removed'] == []
    assert len(result['calendars']) == 1 and not result['rowDeleted'] and result['renders'] == 0


@pytest.mark.parametrize('action', ['save', 'delete'])
def test_pending_write_leaves_state_until_response(action):
    before = _run(action, pending=True)['before']
    assert before['toasts'] == before['errors'] == before['removed'] == []
    assert len(before['calendars']) == 1 and before['events']['event']['calendar'] == 'Owned calendar'
    assert not before['rowDeleted'] and before['renders'] == 0


def test_success_applies_submitted_values_not_newer_unsubmitted_input():
    result = _run('save', pending=True, change_while_pending=True)
    assert 'name=Draft%20calendar' in result['calls'][0]['url']
    assert result['calendars'][0]['name'] == result['events']['event']['calendar'] == 'Draft calendar'
    assert result['calendars'][0]['color'] == '#4caf50'
    assert result['inputName'] == 'Unsubmitted draft'
    assert result['toasts'] == ['Saved “Draft calendar”']
