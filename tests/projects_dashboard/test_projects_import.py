import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import hermes_projects_import as imp  # noqa: E402


def make_state(tmp_path, folders):
    state = tmp_path / 'webui'
    (state / 'sessions').mkdir(parents=True)
    (state / 'projects.json').write_text(json.dumps([
        {'project_id': 'a1', 'name': 'Alpha', 'color': '#7cb9ff'},
        {'project_id': 'b2', 'name': 'Beta', 'color': 'red; DROP'},
        {'project_id': 'c3', 'name': 'Gamma'},
        {'project_id': 'd4', 'name': 'Delta'},
    ]), encoding='utf-8')
    rows = [{'project_id': 'a1', 'workspace': folders['alpha']}, {'project_id': 'a1', 'workspace': folders['alpha']}, {'project_id': 'a1', 'workspace': folders['other']},
            {'project_id': 'b2', 'workspace': folders['beta']}, {'project_id': 'c3', 'workspace': str(tmp_path / 'gone')}, {'workspace': folders['other']}]
    (state / 'sessions' / '_index.json').write_text(json.dumps(rows), encoding='utf-8')
    return state


def folders(tmp_path):
    out = {n: tmp_path / n for n in ('alpha', 'beta', 'other')}
    for p in out.values():
        p.mkdir()
    return {k: str(v) for k, v in out.items()}


def test_legacy_projects_take_the_folder_most_conversations_live_in_and_ignore_bad_data(tmp_path):
    f = folders(tmp_path)
    found = {p['name']: p for p in imp.legacy_projects(make_state(tmp_path, f))}
    assert found['Alpha']['folder'] == f['alpha'] and found['Alpha']['conversations'] == 2 and found['Alpha']['colour'] == '#7cb9ff'
    assert found['Beta']['colour'] is None                                        # not a #rrggbb value: never forwarded
    assert found['Gamma']['folder'] is None and found['Delta']['folder'] is None   # missing folder / no conversations: no guess


def test_plan_creates_only_what_is_missing_and_says_why_it_skips(tmp_path):
    f = folders(tmp_path)
    legacy = imp.legacy_projects(make_state(tmp_path, f))
    existing = [{'name': 'something else', 'primary_path': f['alpha'].upper(), 'folders': []}, {'name': 'beta', 'primary_path': None, 'folders': []}]
    actions = {a['name']: a for a in imp.plan(legacy, existing)}
    assert actions['Alpha']['action'] == 'skip' and 'already belongs' in actions['Alpha']['reason']
    assert actions['Beta']['action'] == 'skip' and 'already exists' in actions['Beta']['reason']
    assert actions['Gamma']['action'] == 'skip' and actions['Delta']['action'] == 'skip'
    fresh = {a['name']: a for a in imp.plan(legacy, [])}
    assert fresh['Alpha']['action'] == 'create' and fresh['Beta']['action'] == 'create'


def test_execute_calls_only_projects_create_with_the_folder_and_survives_one_failure(tmp_path):
    f = folders(tmp_path)
    calls = []

    class Rpc:
        def call(self, method, params=None):
            calls.append((method, params))
            if params['name'] == 'Beta':
                raise RuntimeError('refused')
            return {}

    actions = imp.plan(imp.legacy_projects(make_state(tmp_path, f)), [])
    done = dict(imp.execute(actions, Rpc()))
    assert done['Alpha'] == 'created' and done['Beta'].startswith('failed')
    assert {m for m, _ in calls} == {'projects.create'}
    assert calls[0][1] == {'name': 'Alpha', 'folders': [f['alpha']], 'primary_path': f['alpha'], 'color': '#7cb9ff'}
    assert all('session' not in json.dumps(p).lower() for _, p in calls)


def test_the_tool_has_no_way_to_move_conversations_or_change_grants():
    source = (Path(__file__).resolve().parents[2] / 'src/hermes_projects_import.py').read_text(encoding='utf-8')
    code = source.split('"""', 2)[2]
    for forbidden in ('session.workspace.move', 'session.cwd', 'set_write_roots', 'write_roots', 'projects.delete', 'projects.archive'):
        assert forbidden not in code


def test_ownership_is_by_real_location_nesting_and_archived_projects_count(tmp_path):
    f = folders(tmp_path)
    legacy = imp.legacy_projects(make_state(tmp_path, f))
    parent = str(tmp_path)                                                       # an existing project owning a parent folder already covers alpha and beta
    actions = {a['name']: a for a in imp.plan(legacy, [{'name': 'Everything', 'primary_path': parent, 'archived': True, 'folders': [{'path': parent}]}])}
    assert actions['Alpha']['action'] == 'skip' and "'Everything'" in actions['Alpha']['reason'] and '(archived)' in actions['Alpha']['reason']
    swallow = {a['name']: a for a in imp.plan(legacy, [{'name': 'Inner', 'primary_path': str(Path(f['alpha']) / 'sub'), 'archived': False, 'folders': []}])}
    assert swallow['Alpha']['action'] == 'skip' and 'would contain' in swallow['Alpha']['reason']


def test_two_legacy_projects_on_one_folder_create_only_the_first(tmp_path):
    f = folders(tmp_path)
    state = make_state(tmp_path, f)
    (state / 'projects.json').write_text(json.dumps([{'project_id': 'a1', 'name': 'Alpha'}, {'project_id': 'b2', 'name': 'Twin'}]), encoding='utf-8')
    (state / 'sessions' / '_index.json').write_text(json.dumps([{'project_id': 'a1', 'workspace': f['alpha']}, {'project_id': 'b2', 'workspace': f['alpha']}]), encoding='utf-8')
    made = [a['action'] for a in imp.plan(imp.legacy_projects(state), [])]
    assert made == ['create', 'skip']


def test_missing_folder_is_reported_with_what_was_recorded_and_never_guessed(tmp_path):
    f = folders(tmp_path)
    actions = {a['name']: a for a in imp.plan(imp.legacy_projects(make_state(tmp_path, f)), [])}
    assert actions['Gamma']['action'] == 'skip' and 'missing or offline' in actions['Gamma']['reason'] and 'gone' in actions['Gamma']['reason']


def test_projects_db_is_read_and_backed_up_without_being_changed(tmp_path):
    import sqlite3
    home = tmp_path / 'home'
    home.mkdir()
    db = sqlite3.connect(home / 'projects.db')
    db.executescript("create table projects(id text primary key, slug text, name text, primary_path text, archived integer);"
                     "create table project_folders(project_id text, path text, is_primary integer);"
                     "insert into projects values('p1','one','One','C:/x',1); insert into project_folders values('p1','C:/x',1);")
    db.commit()
    db.close()
    before = (home / 'projects.db').read_bytes()
    existing = imp.existing_from_db(home)
    assert existing == [{'id': 'p1', 'name': 'One', 'primary_path': 'C:/x', 'archived': True, 'folders': [{'path': 'C:/x'}]}]
    copy = imp.backup_db(home, tmp_path / 'backups')
    assert sqlite3.connect(copy).execute('select name from projects').fetchall() == [('One',)]
    assert (home / 'projects.db').read_bytes() == before and imp.existing_from_db(tmp_path / 'nothing') == [] and imp.backup_db(tmp_path / 'nothing', tmp_path / 'b2') is None


def test_a_named_current_folder_replaces_a_vanished_one_and_attaches_the_history_without_guessing(tmp_path):
    f = folders(tmp_path)
    live = tmp_path / 'live'
    live.mkdir()
    state = make_state(tmp_path, f)
    legacy = {p['name']: p for p in imp.legacy_projects(state, {'gamma': str(live)})}
    assert legacy['Gamma']['folder'] == str(live) and legacy['Gamma']['attach_history'] == [str(tmp_path / 'gone')]
    assert imp.legacy_projects(state, {'gamma': str(tmp_path / 'not-a-dir')})[2]['folder'] is None      # an unreachable override is not accepted
    calls = []

    class Rpc:
        def call(self, method, params=None):
            calls.append((method, params))
            return {'project': {'id': 'p1'}}

    actions = [a for a in imp.plan(list(legacy.values()), []) if a['name'] == 'Gamma']
    assert dict(imp.execute(actions, Rpc()))['Gamma'].startswith('created')
    assert [m for m, _ in calls] == ['projects.create', 'projects.add_folder'] and calls[1][1] == {'id': 'p1', 'path': str(tmp_path / 'gone')}
