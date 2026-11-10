"""Contacts event handlers retain drafts and report rejected writes."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='requires Node')


def run_case(config):
    script = r'''
import fs from 'node:fs';import vm from 'node:vm';import {parse} from 'acorn';
const config=JSON.parse(process.argv[1]),source=fs.readFileSync(process.argv[2]+'/static/js/settings.js','utf8');
const ast=parse(source,{ecmaVersion:'latest',sourceType:'module'}),nodes=[];
function walk(node){if(!node||typeof node!=='object')return;nodes.push(node);for(const value of Object.values(node)){if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value)}}
walk(ast);
function callback(callee,within=nodes){const call=within.find(n=>n.type==='CallExpression'&&source.slice(n.callee.start,n.callee.end)===callee);if(!call)throw Error('Missing callback '+callee);return source.slice(call.arguments[1].start,call.arguments[1].end)}
const helper=nodes.find(n=>n.type==='FunctionDeclaration'&&n.id.name==='_writeContacts');
const fields={};
function field(value=''){return {value,style:{display:'flex'},textContent:'',disabled:false,focus(){this.focused=true}}}
for(const name of ['cm-add-name','cm-add-email','cm-add-phone','cm-add-address','cm-add-row','uf-carddav-url','uf-carddav-user','uf-carddav-pass','uf-carddav-msg','cm-import-btn'])fields[name]=field('draft '+name);
fields['cm-add-name'].value=config.empty?'':'Owned new draft';fields['cm-add-email'].value=config.empty?'':'new@example.invalid';
fields['cm-import-btn'].textContent='Import';
const rowFields={'.contact-edit-name':field('Owned edited draft'),'.contact-edit-emails':field('edited@example.invalid'),'.contact-edit-phones':field('5550100'),'.contact-edit-address':field('Owned address')};
const row={querySelector:s=>rowFields[s]},formEl={style:{display:'block'}},errors=[],toasts=[],calls=[];
let renders=0,lists=0,notifications=0;
const context={el:id=>fields[id],row,uid:'owned/contact',formEl,btn:{dataset:{intgType:config.mode==='clear'?'contacts':'carddav',intgId:'owned'}},
 uiModule:{showError:e=>errors.push(e),showToast:e=>toasts.push(e),styledConfirm:async()=>config.confirm!==false},
 window:{styledConfirm:async()=>config.confirm!==false,confirm:()=>config.confirm!==false},alert:e=>errors.push(e),
 _renderContactsManager:async()=>renders++,renderList:async()=>lists++,notifyIntegrationsChanged:()=>notifications++,
 fetch:async(url,options)=>{calls.push({url,options});if(config.network)throw Error('owned network failure');return {ok:config.status===undefined||config.status<400,status:config.status??200,json:async()=>{if(config.badJson)throw Error('owned invalid JSON');return config.body??{success:true}}}}};
vm.createContext(context);if(helper)vm.runInContext(source.slice(helper.start,helper.end),context);
let handler;
if(config.mode==='clear'||config.mode==='remove'){
 const outer=nodes.find(n=>n.type==='CallExpression'&&source.slice(n.callee.start,n.callee.end)==="listEl.querySelectorAll('.intg-del-btn').forEach"&&source.slice(n.start,n.end).includes("/api/contacts/clear"));
 const inner=[];function sub(n){if(!n||typeof n!=='object')return;inner.push(n);for(const v of Object.values(n)){if(Array.isArray(v))v.forEach(sub);else if(v&&typeof v==='object')sub(v)}}sub(outer);
 handler=callback('btn.addEventListener',inner);
}else{
 const targets={add:"el('cm-add-save')?.addEventListener",config:"el('uf-carddav-save').addEventListener",edit:"row.querySelector('.contact-save')?.addEventListener",delete:"row.querySelector('.contact-del')?.addEventListener",import:"el('cm-import-file')?.addEventListener"};handler=callback(targets[config.mode]);
}
const event={stopPropagation(){},target:{value:'owned.csv',files:[{name:'owned.csv',text:async()=> 'name,email\nFixture,fixture@example.invalid'}]}};
await vm.runInContext('('+handler+')',context)(event);
console.log(JSON.stringify({errors,toasts,calls,renders,lists,notifications,formDisplay:formEl.style.display,addName:fields['cm-add-name'].value,addEmail:fields['cm-add-email'].value,addDisplay:fields['cm-add-row'].style.display,editName:rowFields['.contact-edit-name'].value,configMessage:fields['uf-carddav-msg'].textContent,configValue:fields['uf-carddav-url'].value,importDisabled:fields['cm-import-btn'].disabled,importValue:event.target.value}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(config), str(ROOT)],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


MODES = ['add', 'edit', 'delete', 'config', 'clear', 'remove']
FAILURES = [
    {'status': 403, 'body': {'detail': 'Denied'}},
    {'status': 500, 'body': {'success': True}},
    {'body': {'success': False, 'error': 'Rejected'}},
    {'body': {}},
    {'body': {'success': 'true'}},
    {'badJson': True},
    {'network': True},
]


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('response', FAILURES)
def test_failed_contacts_write_keeps_inputs_and_does_not_finish_or_refresh(mode, response):
    result = run_case({'mode': mode, **response})
    assert len(result['calls']) == 1
    assert result['renders'] == result['lists'] == result['notifications'] == 0
    assert result['formDisplay'] == 'block'
    if mode == 'add':
        assert result['addName'] == 'Owned new draft'
        assert result['addEmail'] == 'new@example.invalid'
        assert result['addDisplay'] == 'flex'
    if mode == 'edit':
        assert result['editName'] == 'Owned edited draft'
    if mode == 'config':
        assert result['configMessage'] and result['configMessage'] != 'Saved'
        assert result['configValue'] == 'draft uf-carddav-url'
    else:
        assert len(result['errors']) == 1 and result['errors'][0]


@pytest.mark.parametrize('mode', MODES)
def test_successful_contacts_write_completes_existing_ui_flow(mode):
    result = run_case({'mode': mode})
    assert len(result['calls']) == 1 and result['calls'][0]['options']['credentials'] == 'same-origin'
    assert result['errors'] == []
    if mode == 'add':
        assert result['addName'] == result['addEmail'] == '' and result['addDisplay'] == 'none'
        assert result['renders'] == 1
    elif mode in {'edit', 'delete'}:
        assert result['renders'] == 1
    elif mode == 'config':
        assert result['configMessage'] == 'Saved'
        assert result['renders'] == result['lists'] == result['notifications'] == 1
    else:
        assert result['formDisplay'] == 'none' and result['lists'] == result['notifications'] == 1


@pytest.mark.parametrize('mode', ['delete', 'clear'])
def test_canceled_contact_removal_does_not_send_or_complete_a_write(mode):
    result = run_case({'mode': mode, 'confirm': False})
    assert result['calls'] == result['errors'] == []
    assert result['renders'] == result['lists'] == result['notifications'] == 0
    assert result['formDisplay'] == 'block'


@pytest.mark.parametrize('response,error,toast', [
    ({'status': 403, 'body': {'detail': 'Denied'}}, True, None),
    ({'body': {'success': True, 'imported': 1, 'total': 2, 'failed': 1}}, False, 'Imported 1/2 (1 failed)'),
    ({'body': {'success': False, 'imported': 0, 'total': 1, 'failed': 0}}, False, 'Imported 0/1'),
    ({'body': {'success': False, 'error': 'Invalid contact file'}}, True, None),
])
def test_import_reports_http_failure_and_preserves_partial_or_skipped_counts(response, error, toast):
    result = run_case({'mode': 'import', **response})
    assert bool(result['errors']) is error
    assert result['toasts'] == ([] if error else [toast])
    assert not result['importDisabled'] and result['importValue'] == ''


def test_empty_add_form_keeps_existing_required_field_validation():
    result = run_case({'mode': 'add', 'empty': True})
    assert result['calls'] == result['errors'] == [] and result['renders'] == 0
    assert result['addDisplay'] == 'flex'
