import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import projects_dashboard as pd  # noqa: E402

GIT = pd._git_exe()
needs_git = pytest.mark.skipif(not GIT, reason='git is required')
NOW = 1_800_000_000.0


def run_git(cwd, *args):
    subprocess.run([GIT, '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *args], check=True, capture_output=True)


def snapshot(root):
    out = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and '.git' not in path.parts:
            out[str(path)] = hashlib.sha1(path.read_bytes()).hexdigest()
    return out


def gateway_db(home, sessions=(), admissions=(), leases=(), beats=()):
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / 'state.db')
    db.executescript("""create table sessions(id text, title text, display_name text, cwd text, git_repo_root text, last_activity_at real, last_activity_description text,
        started_at real, ended_at real, end_reason text, message_count int, model text, pinned int, archived int, hidden int);
        create table session_admissions(seq integer primary key autoincrement, admission_id text, target_session_id text, status text, outcome text);
        create table session_turn_leases(conversation_id text, holder text, acquired_at real, expires_at real);
        create table gateway_heartbeats(backend_id text, pid int, started_at real, last_heartbeat real);""")
    for s in sessions:
        db.execute('insert into sessions values (:id,:title,null,:cwd,null,:la,:lad,:st,:ended,:reason,5,"m",0,0,0)',
                   {'title': None, 'cwd': '', 'la': 0, 'lad': '', 'st': NOW - 1000, 'ended': None, 'reason': None, **s})
    for a in admissions:
        db.execute('insert into session_admissions(admission_id,target_session_id,status,outcome) values (?,?,?,?)', a)
    for lease in leases:
        db.execute('insert into session_turn_leases values (?,?,?,?)', (lease[0], 'h', 0, lease[1]))
    for beat in beats:
        db.execute('insert into gateway_heartbeats values (?,?,?,?)', ('b', beat[0], 0, beat[1]))
    db.commit()
    db.close()


class TestSessionState:
    def live(self, **row):
        return pd.session_state('gateway', row, now=NOW, gateway_live=True, webui_live=True)

    def test_unknown_admission_is_blocked_not_replayable(self):
        state, why = self.live(admission={'id': 'adm-1', 'status': 'unknown', 'outcome': None})
        assert state == 'blocked' and 'discarded' in why

    def test_started_turn_needs_a_live_gateway_and_lease(self):
        row = {'admission': {'id': 'a', 'status': 'started'}, 'lease_expires': NOW + 30}
        assert self.live(**row)[0] == 'working'
        assert self.live(admission=row['admission'], lease_expires=NOW - 5)[0] == 'interrupted'
        assert pd.session_state('gateway', row, now=NOW, gateway_live=False, webui_live=True)[0] == 'interrupted'

    def test_fresh_activity_only_counts_with_a_live_gateway(self):
        row = {'last_activity': NOW - 20, 'activity_text': 'receiving stream response'}
        assert self.live(**row)[0] == 'working'
        assert pd.session_state('gateway', row, now=NOW, gateway_live=False, webui_live=True)[0] == 'unknown'

    def test_orphan_reap_is_interrupted_and_clean_close_with_completed_turn_is_completed(self):
        assert self.live(ended_at=1, end_reason='ws_orphan_reap')[0] == 'interrupted'
        assert self.live(ended_at=1, end_reason='cli_close', admission={'id': 'a', 'status': 'terminal', 'outcome': 'completed'})[0] == 'completed'

    def test_no_record_is_unknown_never_idle(self):
        assert self.live()[0] == 'unknown'

    def test_open_session_after_completed_turn_is_idle(self):
        assert self.live(admission={'id': 'a', 'status': 'terminal', 'outcome': 'completed'})[0] == 'idle'

    def test_webui_stream_flag_needs_a_running_webui(self):
        row = {'is_streaming': True, 'active_stream_id': 'abcdef123', 'updated_at': NOW - 30}
        assert pd.session_state('webui', row, now=NOW, gateway_live=True, webui_live=True)[0] == 'working'
        assert pd.session_state('webui', row, now=NOW, gateway_live=True, webui_live=False)[0] == 'interrupted'
        stale = {**row, 'updated_at': NOW - 3600}
        assert pd.session_state('webui', stale, now=NOW, gateway_live=True, webui_live=True)[0] == 'interrupted'
        assert pd.session_state('webui', {'updated_at': NOW - 10}, now=NOW, gateway_live=True, webui_live=True)[0] == 'idle'
        assert pd.session_state('webui', {'recommended_recovery_action': 'retry'}, now=NOW, gateway_live=True, webui_live=True)[0] == 'blocked'

    def test_task_states(self):
        assert pd.task_state({'status': 'open', 'expires_ts': NOW + 60}, NOW)[0] == 'working'
        assert pd.task_state({'status': 'open', 'expires_ts': NOW - 60}, NOW)[0] == 'interrupted'
        assert pd.task_state({'status': 'committed'}, NOW)[0] == 'completed'
        assert pd.task_state({'status': 'blocked'}, NOW)[0] == 'blocked'
        assert pd.task_state({'status': 'weird'}, NOW)[0] == 'unknown'


class TestPaths:
    def test_containment_is_not_a_string_prefix(self):
        assert pd.inside(r'F:\a\b', r'F:\a')
        assert pd.inside(r'F:\a', r'F:\a')
        assert not pd.inside(r'F:\ab', r'F:\a')
        assert not pd.inside('', r'F:\a')

    def test_permission_blocked_beats_grant_and_reports_why(self):
        d = pd.Dashboard(None, None, None)
        got = d.permission(r'F:\p\x', [r'F:\p'], [r'F:\p\x'])
        assert got['state'] == 'blocked'
        assert d.permission(r'F:\p\x', [r'F:\p'], [])['state'] == 'granted'
        assert d.permission(r'F:\q', [r'F:\p'], [])['state'] == 'none'


@pytest.fixture
def world(tmp_path):
    home = tmp_path / 'hermes-gateway'
    webui = tmp_path / 'webui'
    root = tmp_path / 'odysseus-hermes'
    wiki = tmp_path / 'wiki'
    repo = tmp_path / 'repos' / 'app'
    repo.mkdir(parents=True)
    run_git(repo, 'init', '-q', '-b', 'main')
    (repo / 'a.txt').write_text('x')
    run_git(repo, 'add', '.')
    run_git(repo, 'commit', '-q', '-m', 'first')
    tree = tmp_path / 'repos' / 'app-feature'
    run_git(repo, 'worktree', 'add', '-q', '-b', 'feature/x', str(tree))
    (tree / 'b.txt').write_text('dirty')
    (webui / 'sessions').mkdir(parents=True)
    (webui / 'projects.json').write_text(json.dumps([{'project_id': 'p1', 'name': 'My App', 'color': '#fff'}]))
    (webui / 'workspaces.json').write_text(json.dumps([{'path': str(repo), 'name': 'app'}, {'path': str(tmp_path / 'gone'), 'name': 'gone'}]))
    (webui / 'sessions' / '_index.json').write_text(json.dumps([
        {'session_id': 'w1', 'title': 'Webui chat', 'workspace': str(tree), 'project_id': 'p1', 'updated_at': NOW - 100, 'message_count': 9, 'pinned': True},
        {'session_id': 'w2', 'title': 'Archived', 'workspace': str(tree), 'archived': True, 'updated_at': NOW}]))
    gateway_db(home, sessions=[{'id': 'g1', 'title': 'Gateway chat', 'cwd': str(repo / 'sub'), 'la': NOW - 5, 'lad': 'receiving stream response'},
                               {'id': 'g2', 'title': 'Elsewhere', 'cwd': str(tmp_path / 'other'), 'ended': 1, 'reason': 'ws_orphan_reap'}],
               beats=[(os.getpid(), NOW - 3)])
    (root / 'managed' / 'webapp').mkdir(parents=True)
    (root / 'managed' / 'webapp' / 'component.json').write_text(json.dumps({'write_roots': [str(tree)]}))
    (wiki / 'tasks' / 'myproj').mkdir(parents=True)
    (wiki / 'handoffs' / 'hermes').mkdir(parents=True)
    (wiki / 'steward.json').write_text(json.dumps({'projects': {'myproj': {'repo': str(repo)}, 'selftest': {'repo': str(repo)}}}))
    task = {'id': 't-1', 'project': 'myproj', 'agent': 'Hermes', 'summary': 'did a thing', 'status': 'done', 'created_ts': NOW - 50, 'commits': ['abcdef1234567'], 'files': ['a.txt']}
    (wiki / 'tasks' / 'myproj' / 't-1.md').write_text('---\n' + json.dumps(task) + '\n---\nbody\n')
    handoff = {'id': 'h-1', 'project': 'myproj', 'from': 'claude-code', 'to': 'hermes', 'status': 'open', 'auto': True, 'summary': 'resume', 'next_steps': ['a', 'b'], 'created_ts': NOW - 10}
    (wiki / 'handoffs' / 'hermes' / 'h-1.md').write_text('---\n' + json.dumps(handoff) + '\n---\nbody\n')
    env = {'HERMES_WRITE_SAFE_ROOT': str(repo)}
    dash = pd.Dashboard(home, webui, root, wiki, environment=env)
    dash.webui_live = lambda: True
    return dash, repo, tree, tmp_path


@needs_git
class TestProjects:
    def test_repo_and_its_worktree_are_one_project_with_registry_name(self, world):
        dash, repo, tree, _ = world
        projects = dash.projects(NOW)
        mine = [p for p in projects if p['name'] == 'My App']
        assert len(mine) == 1
        project = mine[0]
        assert {'WebUI project', 'WebUI workspace', 'Write grant', 'Wiki project'} <= set(project['sources'])
        assert pd.norm(project['path']) == pd.norm(tree)            # the registered folder wins over the repo's main worktree
        assert project['kind'] == 'git' and project['branch'] == 'feature/x'
        assert project['permission']['state'] == 'granted'
        assert project['wiki']['open_handoffs'] == 1

    def test_missing_folder_is_reported_not_hidden(self, world):
        dash, *_ = world
        gone = next(p for p in dash.projects(NOW) if p['name'] == 'gone')
        assert gone['kind'] == 'missing' and gone['exists'] is False

    def test_sessions_are_placed_by_workspace_only(self, world):
        dash, repo, tree, _ = world
        sessions = {s['id']: s for s in dash.sessions(NOW)}
        assert 'w2' not in sessions                                  # archived sessions are not listed
        assert sessions['g1']['state'] == 'working'                  # fresh activity + live gateway heartbeat (this test process pid)
        assert sessions['g2']['state'] == 'interrupted'
        overview = dash.overview(NOW)
        assert overview['sessions']['unplaced'] == 1                  # g2 ran outside every project folder
        mine = next(p for p in overview['projects'] if p['name'] == 'My App')
        assert mine['sessions']['total'] == 2 and mine['sessions']['states'] == {'idle': 1, 'working': 1}

    def test_detail_has_git_worktrees_wiki_and_access(self, world):
        dash, repo, tree, _ = world
        pid = next(p['id'] for p in dash.projects(NOW) if p['name'] == 'My App')
        d = dash.detail(pid, NOW, port=7000)
        assert d['git']['available'] and d['git']['changed'] == 0 and d['git']['untracked'] == 1
        assert d['git']['commits'][0]['subject'] == 'first'
        paths = {pd.norm(w['path']): w for w in d['worktrees']}
        assert len(paths) == 2 and paths[pd.norm(tree)]['permission']['state'] == 'granted' and paths[pd.norm(repo)]['permission']['state'] == 'none'
        assert d['wiki_detail']['tasks'][0]['state'] == 'completed' and d['wiki_detail']['handoffs'][0]['auto'] is True
        access = d['permission_detail']
        assert access['write_roots'] == [str(tree)] and access['revoke'].endswith('--remove-root "%s"' % tree)
        assert 'not OS containment' in access['note']
        urls = {s['id']: s['resume_url'] for s in d['session_list']}
        assert urls['w1'] == 'http://localhost:8787/session/w1' and urls['g1'] == 'http://hermes.localhost:7000/g1'

    def test_dashboard_never_writes_anything(self, world):
        dash, repo, tree, tmp = world
        before = snapshot(tmp)
        index_before = sorted(p.name for p in (repo / '.git').iterdir())
        pid = next(p['id'] for p in dash.projects(NOW) if p['name'] == 'My App')
        dash.overview(NOW)
        dash.detail(pid, NOW)
        assert snapshot(tmp) == before
        assert sorted(p.name for p in (repo / '.git').iterdir()) == index_before   # no index.lock or other residue from git status


def test_router_enforces_the_guard():
    fastapi = pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from routes.projects_dashboard_routes import make_projects_dashboard_router

    def deny(request):
        raise fastapi.HTTPException(403, 'nope')

    app = fastapi.FastAPI()
    app.include_router(make_projects_dashboard_router(lambda: pytest.fail('must not be built before the guard passes'), guard=deny))
    client = TestClient(app)
    for path in ('/api/projects-dashboard', '/api/projects-dashboard/sessions', '/api/projects-dashboard/project/x'):
        assert client.get(path).status_code == 403


@needs_git
class TestHardening:
    def test_malicious_repo_config_is_not_executed_by_status(self, tmp_path):
        repo = tmp_path / 'evil'
        repo.mkdir()
        run_git(repo, 'init', '-q', '-b', 'main')
        marker = tmp_path / 'ran.txt'
        script = tmp_path / 'hook.cmd' if os.name == 'nt' else tmp_path / 'hook.sh'
        script.write_text('@echo off\r\necho x> "%s"\r\n' % marker if os.name == 'nt' else '#!/bin/sh\necho x > "%s"\n' % marker)
        script.chmod(0o755)
        run_git(repo, 'config', 'core.fsmonitor', str(script))
        out = pd._git(repo, 'status', '--porcelain=v1', '-b')
        assert out is not None and not marker.exists()

    def test_project_id_must_be_ten_hex_characters(self, world):
        dash, *_ = world
        for bad in ('../../etc', '8f96158581/../x', '', 'ZZZZZZZZZZ', 'a' * 11, '-C', '8f96158581' + chr(10)):
            assert dash.detail(bad, NOW) is None

    def test_get_never_runs_with_a_caller_supplied_path(self, world):
        dash, *_ = world
        import inspect
        assert 'path' not in inspect.signature(dash.detail).parameters and 'path' not in inspect.signature(dash.overview).parameters


class TestSessionsMerge:
    def test_migrated_conversation_is_listed_once_and_resumes_in_the_webapp(self, tmp_path):
        home, webui = tmp_path / 'home', tmp_path / 'webui'
        (webui / 'sessions').mkdir(parents=True)
        (webui / 'sessions' / '_index.json').write_text(json.dumps([
            {'session_id': 'both', 'title': 'Chosen title', 'workspace': r'E:\Project\X', 'updated_at': NOW - 500},
            {'session_id': 'legacy', 'title': 'Only here', 'workspace': r'E:\Project\X', 'updated_at': NOW - 400}]))
        gateway_db(home, sessions=[{'id': 'both', 'title': 'Gateway title', 'cwd': r'C:\Users\u', 'la': NOW - 3000}], beats=[(os.getpid(), NOW - 2)])
        dash = pd.Dashboard(home, webui, None, None, environment={})
        dash.webui_live = lambda: True
        rows = {s['id']: s for s in dash.sessions(NOW)}
        assert sorted(rows) == ['both', 'legacy']
        assert rows['both']['title'] == 'Chosen title' and rows['both']['workspace'] == r'E:\Project\X' and rows['both']['engines'] == ['webui', 'gateway']
        assert dash.resume(rows['both'], 7000) == ('http://hermes.localhost:7000/both', 'webapp')
        assert dash.resume(rows['legacy'], 7000) == ('http://localhost:8787/session/legacy', 'legacy-webui')

    def test_resume_url_encodes_the_id(self):
        dash = pd.Dashboard(None, None, None, None, environment={})
        url, _ = dash.resume({'id': 'a/b?c', 'engines': ['gateway']}, 7000)
        assert url == 'http://hermes.localhost:7000/a%2Fb%3Fc'

    def test_related_sessions_need_tool_call_evidence_and_respect_folder_boundaries(self, tmp_path):
        home = tmp_path / 'home'
        gateway_db(home, sessions=[{'id': 'h1', 'cwd': r'C:\Users\u'}, {'id': 'h2', 'cwd': r'C:\Users\u'}, {'id': 'own', 'cwd': r'F:\proj'}], beats=[])
        db = sqlite3.connect(home / 'state.db')
        db.execute('create table messages(session_id text, role text, tool_calls text)')
        call = lambda path: json.dumps([{'function': {'arguments': json.dumps({'path': path})}}])
        for _ in range(4):
            db.execute("insert into messages values ('h1','assistant',?)", (call(r'F:\proj\src\a.py'),))
        db.execute("insert into messages values ('h1','assistant',?)", (call('F:/proj/b.py'),))
        for _ in range(5):
            db.execute("insert into messages values ('h2','assistant',?)", (call(r'F:\proj-other\a.py'),))        # sibling folder with the same prefix
        db.execute("insert into messages values ('h2','assistant',?)", (call(r'F:\proj\once.py'),))              # below the threshold
        for _ in range(4):
            db.execute("insert into messages values ('own','assistant',?)", (call(r'F:\proj\own.py'),))
        db.commit()
        db.close()
        dash = pd.Dashboard(home, None, None, None, environment={})
        assert dash.related_sessions([r'F:\proj'], exclude_ids={'own'}) == [{'id': 'h1', 'calls': 5}]


def test_router_sets_no_store_and_rejects_malformed_ids():
    fastapi = pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from routes.projects_dashboard_routes import make_projects_dashboard_router

    class Fake:
        def overview(self):
            return {'projects': []}

        def detail(self, project_id, port=7000):
            raise AssertionError('a malformed id must not reach the data layer')

    app = fastapi.FastAPI()
    app.include_router(make_projects_dashboard_router(lambda: Fake(), guard=lambda request: None))
    client = TestClient(app)
    ok = client.get('/api/projects-dashboard')
    assert ok.status_code == 200 and ok.headers['cache-control'] == 'no-store'
    assert client.get('/api/projects-dashboard/project/..%2F..%2Fetc').status_code == 404
    assert client.get('/api/projects-dashboard/project/ZZZZZZZZZZ').status_code == 404


def test_production_wiring_uses_the_default_admin_guard():
    import inspect
    from routes import projects_dashboard_routes as routes
    assert inspect.signature(routes.make_projects_dashboard_router).parameters['guard'].default is routes._reader
    source = (Path(__file__).resolve().parents[2] / 'app.py').read_text(encoding='utf-8')
    wiring = [line for line in source.splitlines() if 'make_projects_dashboard_router(' in line and 'import' not in line]
    assert len(wiring) == 1 and 'guard' not in wiring[0]


class TestResumeLink:
    URL = 'http://hermes.localhost:7000/webapp/launch#ticket=T0K&query=%3Fold'

    def test_resume_adds_the_query_to_the_fragment_and_keeps_the_ticket(self):
        from urllib.parse import parse_qsl, urlsplit
        from src.application_modes import with_resume
        out = with_resume(self.URL, 'local-abc_1.2')
        fragment = dict(parse_qsl(urlsplit(out).fragment))
        assert fragment == {'ticket': 'T0K', 'query': '?odysseus_resume=local-abc_1.2'}
        assert urlsplit(out).path == '/webapp/launch' and urlsplit(out).netloc == 'hermes.localhost:7000'

    def test_malformed_ids_change_nothing(self):
        from src.application_modes import with_resume
        for bad in ('../x', 'a b', 'x&query=?evil', '', None, 'a' * 201, 'x\n'):
            assert with_resume(self.URL, bad) == self.URL

    def test_plugin_validates_the_id_and_only_opens_a_session(self):
        text = (Path(__file__).resolve().parents[2] / 'integrations/hermes-mode/desktop-plugins/odysseus-resume/plugin.js').read_text(encoding='utf-8')
        assert "/^[A-Za-z0-9._-]{1,200}$/" in text and 'host.openSession(id)' in text
        assert text.count('window.location.assign(') == 1 and 'location.href =' not in text and 'window.open' not in text      # the single navigation is the diagnostic-dashboard link
