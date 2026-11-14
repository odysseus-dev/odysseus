"""Usage Center backend: stored-conversation context restore, cost labelling (Hermes patch, behavioural) and the period/profile/project
cost overview (Odysseus side). Behavioural tests need HERMES_PATCHED_SRC / HERMES_PATCHED_PYTHON (a pin with the patches applied)."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src import usage_costs

HERE = Path(__file__).parent
SRC, PY = os.environ.get('HERMES_PATCHED_SRC'), os.environ.get('HERMES_PATCHED_PYTHON')


def make_state(path, rows, sessions):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE sessions (id TEXT, cwd TEXT, profile_name TEXT)')
    db.execute('CREATE TABLE session_model_usage (session_id, model, billing_provider, billing_mode, task, api_call_count, input_tokens, output_tokens, '
               'cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status, cost_source, last_seen)')
    db.executemany('INSERT INTO sessions VALUES (?,?,?)', sessions)
    db.executemany('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    db.commit()
    db.close()


@pytest.mark.parametrize('status,provider,mode,expected', [
    ('actual', 'x', '', 'billed'), ('included', 'openai-codex', 'subscription_included', 'included'),
    ('estimated', 'deepseek', '', 'estimated_api'), ('estimated', 'claude-subscription-directsdk-experimental', '', 'subscription_equivalent'),
    ('estimated', 'x', 'subscription_included', 'subscription_equivalent'), ('unknown', 'nous', '', 'unpriced'), (None, 'nous', '', 'unpriced')])
def test_cost_class(status, provider, mode, expected):
    assert usage_costs.cost_class(status, provider, mode) == expected


def test_overview_periods_classes_profiles_projects(tmp_path):
    now = time.time()
    day = 86400
    rows = [
        ('s1', 'sonnet', 'claude-subscription-x', '', '', 10, 100, 50, 5000, 0, 0, 2.5, 0.0, 'estimated', 'provider_cost_api', now - 60),
        ('s1', 'sonnet', 'claude-subscription-x', '', 'title', 1, 10, 5, 0, 0, 0, 0.0, 0.0, None, None, now - 60),
        ('s2', 'deepseek-chat', 'deepseek', '', '', 3, 12000, 400, 0, 0, 0, 0.0008, 0.0, 'estimated', 'official_docs_snapshot', now - 3 * day),
        ('s3', 'deepseek-chat', 'deepseek', '', '', 1, 500, 20, 0, 0, 0, 0.0001, 0.0002, 'actual', 'provider_generation_api', now - 20 * day),
        ('s4', 'longcat', 'nous', '', '', 13, 97563, 9738, 603008, 0, 0, 0.0, 0.0, 'unknown', 'provider_models_api', now - 40 * day)]
    sessions = [('s1', 'F:/Work/App/sub', 'default'), ('s2', 'F:/Elsewhere', 'coder'), ('s3', 'F:/Work/App', 'default'), ('s4', None, None)]
    state, projects = tmp_path / 'state.db', tmp_path / 'projects.db'
    make_state(state, rows, sessions)
    p = sqlite3.connect(projects)
    p.execute('CREATE TABLE projects (id, name, archived)')
    p.execute('CREATE TABLE project_folders (project_id, path)')
    p.execute("INSERT INTO projects VALUES ('a','App',0)")
    p.execute("INSERT INTO project_folders VALUES ('a','F:\\Work\\App')")
    p.commit()
    p.close()
    out = usage_costs.overview(state, projects, now=now)
    assert out['available'] and 'other clients' in out['scope']
    by = lambda period: {c['class']: c for c in out['periods'][period]['by_class']}
    assert set(by('today')) == {'subscription_equivalent', 'unpriced'}            # the title call has no price: Unpriced, not $0.00
    assert by('today')['subscription_equivalent']['usd'] == 2.5 and by('today')['unpriced']['usd'] is None
    assert by('today')['unpriced']['aux_calls'] == 1
    assert by('last_7_days')['estimated_api']['usd'] == 0.0008                     # sub-cent amounts are not rounded away
    assert 'billed' not in by('last_7_days') and by('last_30_days')['billed']['usd'] == 0.0002
    assert by('all_recorded')['unpriced']['input'] == 97563 + 10 and by('last_30_days')['unpriced']['input'] == 10
    models = {(m['provider'], m['model'], m['class']) for m in out['periods']['last_30_days']['by_model']}
    assert ('deepseek', 'deepseek-chat', 'billed') in models and ('deepseek', 'deepseek-chat', 'estimated_api') in models
    assert {(x['profile'], x['class']) for x in out['periods']['last_7_days']['by_profile']} == {
        ('default', 'subscription_equivalent'), ('default', 'unpriced'), ('coder', 'estimated_api')}
    assert {(x['project'], x['class']) for x in out['periods']['last_30_days']['by_project']} == {
        ('App', 'subscription_equivalent'), ('App', 'unpriced'), ('App', 'billed')}      # sub-folder attributed; /Elsewhere is not guessed


def test_overview_unreadable_store(tmp_path):
    assert usage_costs.overview(tmp_path / 'missing.db')['available'] is False
    bad = tmp_path / 'bad.db'
    bad.write_bytes(b'not sqlite')
    assert usage_costs.overview(bad)['available'] is False


def test_overview_never_writes_the_store(tmp_path):
    state = tmp_path / 'state.db'
    make_state(state, [], [])
    before = state.read_bytes()
    usage_costs.overview(state)
    assert state.read_bytes() == before


@pytest.mark.skipif(not (SRC and PY), reason='needs HERMES_PATCHED_SRC / HERMES_PATCHED_PYTHON')
def test_stored_conversation_scenarios(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    proc = subprocess.run([PY, '-B', str(HERE / 'usage_context_scenarios.py'), SRC, str(tmp_path)], capture_output=True, text=True, env=env, timeout=240)
    text = proc.stdout + proc.stderr
    assert '@@' in text, text[-1500:]
    o = json.loads(text.split('@@', 1)[1].splitlines()[0])
    rep = o['reported']                                                           # persisted provider reading restored
    assert (rep['context_used'], rep['context_max'], rep['context_source'], rep['context_estimated']) == (16632, 1000000, 'provider_usage', False)
    assert rep['context_window_source'] == 'model_metadata' and rep['context_input_source'] == 'provider_anchor'
    plus = o['plus_estimate']                                                     # newer messages are a labelled local estimate
    assert plus['context_estimated'] is True and plus['context_source'] == 'local_estimate' and plus['context_used'] > rep['context_used']
    nr = o['no_reading']                                                          # no provider reading and no usable estimate: say so
    assert nr.get('context_used') is None or nr.get('context_estimated') is True
    uw = o['unknown_window']                                                      # the window is never invented
    assert 'context_max' not in uw and uw['context_window_source'] == 'unknown' and uw['context_known_tokens'] > 0
    assert o['stale_anchor']['context_estimated'] is True                         # transcript rewritten (compaction): old reading not trusted
    assert o['switch_a']['context_max'] == 1000000 and o['switch_b']['context_max'] == 32768      # model switch: no stale window
    assert o['gone'] is None
    s = o['summaries']
    assert s['compressions'] == 3 and s['last_compaction_at'] and s['compression_error'] == 'summary model unavailable' and s['compression_cooldown_until']
    classes = lambda k: {g['class']: g for g in o[k]['cost_breakdown']}
    assert classes('cost_sub')['subscription_equivalent']['usd'] == 2.105 and classes('cost_sub')['unpriced']['label'] == 'Unpriced'
    assert classes('cost_api')['billed']['label'] == '$0.0002' and classes('cost_api')['estimated_api']['usd'] == 0.0008
    assert classes('cost_free')['included']['usd'] is None and classes('cost_free')['unpriced']['usd'] is None
    assert o['frame_top_level_keys_unchanged'] is True and o['frame_nested_has_extras'] == 500000
