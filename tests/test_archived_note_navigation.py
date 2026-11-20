"""Read-only note links resolve the correct collection before changing the panel."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='requires Node')
FULL = '12345678-aaaa-bbbb-cccc-123456789abc'
OTHER = '12345678-dddd-eeee-ffff-123456789abc'


def run_case(config):
    paths = {name: (ROOT / path).as_uri() for name, path in {
        'http': 'static/js/features/notes/infrastructure/http.js',
        'workflows': 'static/js/features/notes/application/workflows.js',
        'panel': 'static/js/features/notes/ui/panel.js',
        'toolbar': 'static/js/features/notes/ui/toolbar.js',
    }.items()}
    script = '''
const config=JSON.parse(process.argv[1]),paths=JSON.parse(process.argv[2]);
const {createNotesApi}=await import(paths.http);
const {createNotesWorkflows}=await import(paths.workflows);
const {createPanel}=await import(paths.panel);
const {createToolbar}=await import(paths.toolbar);
const response=(status,body)=>({status,ok:status>=200&&status<300,json:async()=>body});
function makeApp(initial={}) {
 const state={open:true,showingArchived:false,activeLabel:'Work',activeFilter:'pinned',searchQuery:'draft',notes:[],...initial};
 const loading=[];
 return {state,loading,setOpen:v=>state.open=v,setEditingId:v=>state.editingId=v,
 setShowingArchived:v=>state.showingArchived=v,setActiveLabel:v=>state.activeLabel=v,
 setActiveFilter:v=>state.activeFilter=v,setSearchQuery:v=>state.searchQuery=v,
 setLoading:v=>loading.push(v),replaceNotes:v=>state.notes=v};
}
function classList(initial=[]) {const values=new Set(initial);return {
 add:v=>values.add(v),remove:v=>values.delete(v),contains:v=>values.has(v),
 toggle(v,on){if(on??!values.has(v))values.add(v);else values.delete(v)}
}}
async function main() {
 if(config.mode==='url') {
  const calls=[];const api=createNotesApi({origin:'http://owned',request:async(...args)=>calls.push(args)});
  await api.getNote('id / with spaces');return calls;
 }
 if(config.mode==='resolve') {
  const app=makeApp(),getCalls=[],listCalls=[];
  const api={getNote:async id=>{getCalls.push(id);if(config.network)throw Error('owned network failure');
   const result=response(config.status??200,config.note);if(config.badJson)result.json=async()=>{throw Error('owned invalid JSON')};return result},
   listNotes:async archived=>{listCalls.push(archived);return response(config.listStatus??200,(archived?config.archived:config.active)??{notes:[]})}};
  const workflows=createNotesWorkflows({app,api,effects:{}});
  let note,error;try {note=await workflows.resolveLinkedNote(config.id)}catch(e){error=e.message}
  return {note:note??null,error:error??null,getCalls,listCalls};
 }
 if(config.mode==='fetch-race') {
  const app=makeApp(),pending=[];
  const api={listNotes:()=>new Promise(resolve=>pending.push(resolve))};
  const workflows=createNotesWorkflows({app,api,effects:{}});
  const first=workflows._fetchNotes();app.state.showingArchived=true;
  const second=workflows._fetchNotes();pending[1](response(200,{notes:[{id:'archive-new'}]}));await second;
  pending[0](response(config.failOld?500:200,{notes:[{id:'active-old'}]}));await first;
  return {notes:app.state.notes,loading:app.loading};
 }
 const app=makeApp({open:config.mode!=='close-pending',showingArchived:!!config.initialArchived});
 const pane={classList:classList(app.state.showingArchived?['notes-pane-archive']:[]),querySelector:id=>id==='#notes-archive-toggle'?button:null};
 const button={classList:classList(app.state.showingArchived?['active']:[]),style:{},title:app.state.showingArchived?'Exit archive':'View archive'};
 const search={value:'draft'},errors=[],timers=[];let renders=0,fetches=0,fronts=0,resolves=0;
 globalThis.document={getElementById:id=>({'notes-pane':pane,'notes-archive-toggle':button,'notes-search':search}[id]||null)};
 globalThis.setTimeout=fn=>{timers.push(fn)};
 const toolbar=createToolbar({app,state:app.state});
 let release;
 const resolveLinkedNote=async id=>{resolves++;
  if(config.mode==='latest'&&id==='active')return {id:'active',archived:false};
  if(['latest','close-pending','focus-pending'].includes(config.mode))return new Promise(resolve=>release=resolve);
  if(config.mode==='missing')return null;if(config.mode==='failure')throw Error('owned read failure');
  return {id:'resolved-full-id',archived:!!config.archived};
 };
 let refreshRelease;
 const panel=createPanel({app,state:app.state,uiModule:{showError:e=>errors.push(e)},
 workflows:{resolveLinkedNote},syncArchiveView:toolbar.syncArchiveView,
 _bringNotesToFront:()=>fronts++,
 _fetchNotes:async()=>{fetches++;if(config.mode==='focus-refresh')await new Promise(resolve=>refreshRelease=resolve)},
 _renderNotes:()=>renders++,Modals:{isRegistered:()=>false}});
 const first=panel.openNote(config.mode==='no-id'?undefined:'first');
 if(config.mode==='latest'){await panel.openNote('active');release({id:'old-archive',archived:true})}
 if(config.mode==='close-pending'){panel.closePanel('down');release({id:'old-archive',archived:true})}
 if(config.mode==='focus-pending'){panel.openPanel();release({id:'old-archive',archived:true})}
 if(config.mode==='focus-refresh'){await new Promise(resolve=>setImmediate(resolve));panel.openPanel();refreshRelease()}
 await first;
 return {state:app.state,errors,renders,fetches,fronts,resolves,timers:timers.length,
 buttonActive:button.classList.contains('active'),archiveTitle:button.title,paneArchived:pane.classList.contains('notes-pane-archive')};
}
console.log(JSON.stringify(await main()));
'''
    result = subprocess.run([NODE, '--input-type=module', '-e', script, json.dumps(config), json.dumps(paths)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('archived', [False, True])
def test_exact_link_resolves_without_fetching_other_collections(archived):
    note = {'id': FULL, 'archived': archived}
    result = run_case({'mode': 'resolve', 'id': FULL, 'note': note})
    assert result['note'] == note and result['error'] is None
    assert result['getCalls'] == [FULL] and result['listCalls'] == []


@pytest.mark.parametrize('archived', [False, True])
def test_short_prefix_finds_its_unique_target_in_either_collection(archived):
    note = {'id': FULL, 'archived': archived}
    result = run_case({'mode': 'resolve', 'id': '12345678', 'status': 404,
                       'archived' if archived else 'active': {'notes': [note]}})
    assert result['note'] == note and result['error'] is None
    assert result['listCalls'] == [False, True]


def test_exact_legacy_short_id_wins_before_prefix_search():
    note = {'id': '12345678', 'archived': True}
    result = run_case({'mode': 'resolve', 'id': note['id'], 'note': note})
    assert result['note'] == note and result['listCalls'] == []


def test_ambiguous_short_prefix_is_not_guessed_across_collections():
    result = run_case({'mode': 'resolve', 'id': '12345678', 'status': 404,
                       'active': [{'id': FULL, 'archived': False}], 'archived': [{'id': OTHER, 'archived': True}]})
    assert result['note'] is None and result['error'] is None


def test_missing_full_id_does_not_fall_back_to_an_unrelated_prefix_match():
    result = run_case({'mode': 'resolve', 'id': FULL, 'status': 404})
    assert result['note'] is None and result['listCalls'] == []


@pytest.mark.parametrize('extra', [
    {'status': 500}, {'network': True}, {'badJson': True},
    {'note': {'id': OTHER, 'archived': False}}, {'note': {'id': FULL}},
    {'status': 404, 'id': '12345678', 'listStatus': 403},
    {'status': 404, 'id': '12345678', 'active': {'unexpected': []}},
])
def test_read_failures_and_invalid_metadata_do_not_produce_a_navigation_target(extra):
    result = run_case({'mode': 'resolve', 'id': FULL, 'note': {'id': FULL, 'archived': False}, **extra})
    assert result['note'] is None and result['error']


@pytest.mark.parametrize('fail_old', [False, True])
def test_stale_collection_response_cannot_replace_newer_archive_data(fail_old):
    result = run_case({'mode': 'fetch-race', 'failOld': fail_old})
    assert result['notes'] == [{'id': 'archive-new'}]
    assert result['loading'] == [True, True, False]


@pytest.mark.parametrize('archived', [False, True])
def test_open_link_selects_target_collection_and_synchronizes_existing_panel(archived):
    result = run_case({'mode': 'panel', 'archived': archived, 'initialArchived': not archived})
    assert result['state']['showingArchived'] is archived
    assert result['buttonActive'] is archived and result['paneArchived'] is archived
    assert result['archiveTitle'] == ('Exit archive' if archived else 'View archive')
    assert result['state']['activeLabel'] is result['state']['activeFilter'] is None
    assert result['state']['searchQuery'] == ''
    assert result['fetches'] == result['renders'] == 1 and result['errors'] == []


@pytest.mark.parametrize('mode', ['missing', 'failure'])
def test_unavailable_link_keeps_current_filters_and_panel(mode):
    result = run_case({'mode': mode, 'initialArchived': True})
    assert result['state']['showingArchived'] is True
    assert result['state']['activeLabel'] == 'Work' and result['state']['searchQuery'] == 'draft'
    assert result['fetches'] == result['renders'] == 0 and len(result['errors']) == 1


def test_latest_link_wins_when_earlier_metadata_arrives_last():
    result = run_case({'mode': 'latest'})
    assert result['state']['showingArchived'] is False
    assert result['fetches'] == result['renders'] == 1 and result['errors'] == []


@pytest.mark.parametrize('mode', ['close-pending', 'focus-pending'])
def test_closed_or_refocused_panel_does_not_apply_pending_link(mode):
    result = run_case({'mode': mode})
    assert result['state']['showingArchived'] is False
    assert result['state']['searchQuery'] == 'draft'
    assert result['fetches'] == result['renders'] == 0 and result['errors'] == []
    if mode == 'close-pending':
        assert result['state']['open'] is False


def test_bringing_committed_view_to_front_does_not_discard_its_pending_refresh():
    result = run_case({'mode': 'focus-refresh', 'archived': True})
    assert result['state']['showingArchived'] is True and result['renders'] == 1
    assert result['errors'] == []


def test_open_without_a_note_id_keeps_normal_active_panel_behavior():
    result = run_case({'mode': 'no-id', 'initialArchived': True})
    assert result['state']['showingArchived'] is False
    assert result['resolves'] == 0 and result['fetches'] == result['renders'] == 1


def test_note_http_lookup_encodes_identifier_and_keeps_same_origin_credentials():
    assert run_case({'mode': 'url'}) == [['http://owned/api/notes/id%20%2F%20with%20spaces', {'credentials': 'same-origin'}]]
