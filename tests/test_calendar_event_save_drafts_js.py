"""Failed calendar writes roll back cache state without destroying an editor."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.helpers.js_modules import js_function_source


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node required')


def run_write(action, outcome, form=True, opened=True, original=True):
    source = (ROOT / 'static/js/calendar.js').read_text(encoding='utf-8')
    functions = '\n'.join(js_function_source(name, source) for name in (
        '_optimisticEvent', '_createEvent', '_updateEvent',
    ))
    config = json.dumps(dict(action=action, outcome=outcome, form=form,
                             opened=opened, original=original))
    script = 'const config = ' + config + ';\n' + r'''
      const saved = {uid:'owned', summary:'Saved', location:'Old office'};
      const _calendars = [{href:'calendar', name:'Owned calendar'}];
      const _allEvents = config.original ? {owned:saved} : {};
      const _open = config.opened, API_BASE = '';
      let form = config.form ? {summary:'Draft', description:'Unsaved body'} : null;
      let renders = 0, cacheWrites = 0, optimistic = null, request = null;
      const errors = [], _fetchedRanges = [];
      const document = {querySelector(selector) {
        if (selector !== '#cal-body .cal-form') throw Error('Unexpected selector: '+selector);
        return form;
      }};
      const _render = () => { renders++; form = null; };
      const _saveCache = () => { cacheWrites++; };
      const window = {uiModule:{showError(message) {errors.push(message);}}};
      const localStorage = {removeItem() {}};
      const fetch = async (url, options) => {
        request = {url, method:options.method, body:JSON.parse(options.body)};
        optimistic = JSON.parse(JSON.stringify(_allEvents));
        if (config.outcome === 'network') throw Error('Owned connection failure');
        return {ok:config.outcome === 'ok' || config.outcome === 'json',
          status:config.outcome === '403' ? 403 : 500,
          json:async () => {
            if (config.outcome === 'json') throw SyntaxError('Owned invalid JSON');
            return {uid:'owned',event:{...saved,...request.body},reminder:null};
          }};
      };
    ''' + functions + r'''
      const data = {summary:'Draft',description:'Unsaved body',calendar_href:'calendar'};
      let rejected = false, result;
      try {result = config.action === 'create'
        ? await _createEvent(data) : await _updateEvent('owned',data);}
      catch {rejected = true;}
      console.log(JSON.stringify({rejected,result,renders,cacheWrites,form,errors,
        events:_allEvents,optimistic,request}));
    '''
    proc = subprocess.run(['node', '--input-type=module'], input=script,
                          cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize('action', ['create', 'update'])
@pytest.mark.parametrize('outcome', ['403', '500', 'network'])
@pytest.mark.parametrize('form', [True, False])
def test_failed_write_restores_cache_and_preserves_any_open_draft(action, outcome, form):
    result = run_write(action, outcome, form=form)
    assert result['rejected']
    assert result['events'] == {'owned': {'uid': 'owned', 'summary': 'Saved', 'location': 'Old office'}}
    assert result['form'] == ({'summary': 'Draft', 'description': 'Unsaved body'} if form else None)
    assert result['renders'] == (0 if form else 1)
    assert result['cacheWrites'] == 0
    assert len(result['errors']) == 1
    assert result['request']['method'] == ('POST' if action == 'create' else 'PUT')
    if action == 'create':
        assert any(key.startswith('temp-') for key in result['optimistic'])
    else:
        assert result['optimistic']['owned']['summary'] == 'Draft'


@pytest.mark.parametrize('action', ['create', 'update'])
def test_closed_calendar_is_not_rendered_after_write_failure(action):
    result = run_write(action, '500', form=False, opened=False)
    assert result['rejected']
    assert result['renders'] == 0


def test_unreadable_create_response_keeps_draft_and_removes_temporary_event():
    result = run_write('create', 'json')
    assert result['rejected']
    assert result['form']['description'] == 'Unsaved body'
    assert list(result['events']) == ['owned']
    assert result['renders'] == 0


def test_failed_update_without_cached_original_does_not_leave_optimistic_event():
    result = run_write('update', '500', form=False, original=False)
    assert result['rejected']
    assert result['events'] == {}
    assert result['renders'] == 1


@pytest.mark.parametrize('action', ['create', 'update'])
def test_successful_write_still_commits_server_event_and_cache(action):
    result = run_write(action, 'ok')
    assert not result['rejected']
    assert result['errors'] == []
    assert result['events']['owned']['summary'] == 'Draft'
    assert result['cacheWrites'] == 1
    assert not any(key.startswith('temp-') for key in result['events'])
    assert result['renders'] == (1 if action == 'create' else 0)
