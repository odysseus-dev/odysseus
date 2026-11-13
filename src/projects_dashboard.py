"""Read-only Projects dashboard data layer.

Joins what Odysseus and Hermes already record. It owns no registry, task store or permission store and writes nothing:

  projects     WebUI projects.json + workspaces.json, the Webapp projects.db, Odysseus write grants, Wiki Steward projects
  sessions     the managed gateway's state.db (Webapp) and the WebUI session index
  agent state  only what a record proves (see ``session_state``); everything else is ``unknown``
  git          the repository on disk (HEAD file for the overview; ``git status``/``log`` only for one project's detail)
  permissions  the grants file, the blocked-folder list and the HERMES_WRITE_SAFE_ROOT variable
  wiki         tasks and handoffs parsed from the Wiki Steward folder (never through its write API)
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

STALE_STREAM_SECONDS = 15 * 60
HEARTBEAT_FRESH_SECONDS = 120
ACTIVITY_FRESH_SECONDS = 90
STATES = ('working', 'waiting', 'blocked', 'completed', 'idle', 'interrupted', 'unknown')
ATTENTION = ('waiting', 'blocked', 'interrupted')
_GIT_TIMEOUT = 8


def norm(path):
    if not path:
        return ''
    text = os.path.normpath(str(path)).replace('/', '\\' if os.name == 'nt' else '/')
    return text.casefold() if os.name == 'nt' else text


def inside(child, parent):
    c, p = norm(child), norm(parent)
    if not c or not p:
        return False
    return c == p or c.startswith(p.rstrip('\\/') + os.sep)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def _ro(path):
    return sqlite3.connect('file:' + str(path).replace('\\', '/') + '?mode=ro', uri=True, timeout=2)


def _git_exe():
    for candidate in (shutil.which('git'), r'C:\Program Files\Git\cmd\git.exe', '/usr/bin/git'):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _git(cwd, *args):
    exe = _git_exe()
    if not exe:
        return None
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    try:
        done = subprocess.run([exe, '--no-optional-locks', '-c', 'core.quotepath=off', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false', '-C', str(cwd), *args], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=_GIT_TIMEOUT, creationflags=flags, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# -- repository identity (file system only, no subprocess) --------------------------------------------------------------------
def repo_info(path):
    """{'toplevel', 'common', 'git_dir', 'is_worktree', 'branch', 'head'} for a path inside a repository, else None."""
    here = Path(path)
    for folder in (here, *here.parents):
        marker = folder / '.git'
        try:
            if marker.is_dir():
                git_dir = marker
                common = marker
                break
            if marker.is_file():
                text = marker.read_text(encoding='utf-8', errors='replace').strip()
                if not text.startswith('gitdir:'):
                    return None
                git_dir = (folder / text[7:].strip()).resolve()
                common_file = git_dir / 'commondir'
                common = (git_dir / common_file.read_text(encoding='utf-8').strip()).resolve() if common_file.is_file() else git_dir
                break
        except OSError:
            return None
    else:
        return None
    branch = head = None
    try:
        raw = (git_dir / 'HEAD').read_text(encoding='utf-8').strip()
        if raw.startswith('ref:'):
            ref = raw[4:].strip()
            branch = ref[11:] if ref.startswith('refs/heads/') else ref
        else:
            head, branch = raw[:12], None
    except OSError:
        pass
    return {'toplevel': str(folder), 'common': str(common), 'git_dir': str(git_dir), 'is_worktree': common != git_dir, 'branch': branch, 'head': head}


def project_key(path):
    info = repo_info(path)
    basis = info['common'] if info else str(path)
    return hashlib.sha1(norm(basis).encode('utf-8')).hexdigest()[:10]


# -- agent state: evidence or unknown -----------------------------------------------------------------------------------------
def session_state(source, row, *, now, gateway_live, webui_live):
    """Return (state, evidence). Never guesses: a state is only returned when a field in the record supports it."""
    if source == 'webui':
        updated = row.get('updated_at') or 0
        streaming = bool(row.get('is_streaming') and row.get('active_stream_id'))
        if row.get('recommended_recovery_action'):
            return 'blocked', 'recovery needed: %s' % str(row['recommended_recovery_action'])[:80]
        if streaming:
            if webui_live and now - updated < STALE_STREAM_SECONDS:
                return 'working', 'stream %s active, updated %ds ago' % (str(row['active_stream_id'])[:8], now - updated)
            return 'interrupted', 'stream flag set but %s' % ('WebUI is not running' if not webui_live else 'no update for %d min' % ((now - updated) // 60))
        if row.get('has_pending_user_message'):
            return 'interrupted', 'a user message is pending with no running stream'
        return 'idle', 'no running stream; last update %s' % time.strftime('%Y-%m-%d %H:%M', time.localtime(updated)) if updated else 'no running stream'
    admission = row.get('admission') or {}
    status, outcome = admission.get('status'), admission.get('outcome')
    lease_live = (row.get('lease_expires') or 0) > now
    if status == 'unknown':
        return 'blocked', 'lost turn %s: outcome unknown, queue paused until it is discarded' % str(admission.get('id', ''))[:12]
    if status == 'started':
        if gateway_live and lease_live:
            return 'working', 'turn running under a live gateway lease'
        return 'interrupted', 'turn marked started but the gateway lease is %s' % ('missing' if gateway_live else 'dead: gateway not running')
    recent = now - (row.get('last_activity') or 0)
    if not row.get('ended_at') and gateway_live and 0 <= recent < ACTIVITY_FRESH_SECONDS:
        return 'working', 'activity %ds ago: %s' % (recent, row.get('activity_text') or 'no description')
    if row.get('ended_at'):
        reason = row.get('end_reason')
        if reason in ('ws_orphan_reap', 'startup_orphan_reap'):
            return 'interrupted', 'session reaped (%s)' % reason
        if status == 'terminal' and outcome == 'completed':
            return 'completed', 'last turn completed; session closed (%s)' % (reason or 'no reason recorded')
        return 'unknown', 'session ended (%s) without a recorded final outcome' % (reason or 'no reason')
    if status == 'terminal':
        if outcome == 'interrupted':
            return 'interrupted', 'last turn interrupted'
        if outcome == 'cancelled':
            return 'idle', 'last turn cancelled by the user'
        if outcome == 'completed':
            return 'idle', 'last turn completed; session open'
    if status == 'queued':
        return 'idle', 'queued input, no turn started'
    return 'unknown', 'no admission record'


def task_state(task, now):
    status = task.get('status')
    if status == 'open':
        if (task.get('expires_ts') or 0) > now:
            return 'working', 'task open until %s' % time.strftime('%H:%M', time.localtime(task['expires_ts']))
        return 'interrupted', 'task window expired without a recorded result'
    if status in ('done', 'committed'):
        return 'completed', 'task %s' % status
    if status == 'partial':
        return 'idle', 'partly done: more work remains'
    if status == 'blocked':
        return 'blocked', 'agent recorded the task as blocked'
    if status == 'interrupted':
        return 'interrupted', 'marked interrupted by the recovery pass'
    return 'unknown', 'status %r' % status


BS = chr(92)
PROJECT_ID = re.compile(r'[0-9a-f]{10}')


# The related-conversation scan reads every recorded tool call, which is by far the slowest part of a project's detail (~0.9 s of ~1.1 s measured on the real
# history, growing with it). It is loaded separately from the basic detail and its result is reused for this long.
RELATED_TTL_S = 120


class Dashboard:
    def __init__(self, hermes_home, webui_state, component_root, wiki_root=None, environment=None):
        self.hermes_home = Path(hermes_home) if hermes_home else None
        self.webui_state = Path(webui_state) if webui_state else None
        self.component_root = Path(component_root) if component_root else None
        self.wiki_root = Path(wiki_root) if wiki_root else None
        self.environment = os.environ if environment is None else environment
        self._related_cache = {}

    # -- sources ---------------------------------------------------------------------------------------------------------------
    def _webapp_config(self):
        return _read_json(self.component_root / 'managed' / 'webapp' / 'component.json', {}) if self.component_root else {}

    def write_roots(self):
        return [r for r in (self._webapp_config().get('write_roots') or []) if isinstance(r, str)]

    def blocked_folders(self):
        if not self.hermes_home:
            return []
        data = _read_json(self.hermes_home / 'vault' / 'odysseus-protected-paths.json', None)
        if data is None:
            data = _read_json(self.hermes_home / 'vault' / 'webui-trusted-workspaces.json', {})
        items = data.get('blocked') if isinstance(data, dict) else []
        return [(i.get('path') if isinstance(i, dict) else i) for i in items or [] if i]

    def legacy_roots(self):
        raw = self.environment.get('HERMES_WRITE_SAFE_ROOT') or ''
        return [p for p in raw.split(os.pathsep if os.name == 'nt' else ':') if p.strip()]

    def _wiki_config(self):
        return _read_json(self.wiki_root / 'steward.json', {}) if self.wiki_root else {}

    def _wiki_records(self, kind, project):
        base = self.wiki_root / kind if self.wiki_root else None
        out = []
        pattern = '%s/*.md' % project if kind == 'tasks' else '*/*.md'
        for path in sorted(base.glob(pattern)) if base and base.exists() else []:
            try:
                record = json.loads(path.read_text(encoding='utf-8').split('---\n', 2)[1])
            except (OSError, ValueError, IndexError):
                continue
            if kind == 'handoffs' and record.get('project') != project:
                continue
            out.append(record)
        return out

    def webui_live(self):
        port = int(self.environment.get('ODYSSEUS_WEBUI_PORT') or 8787)
        import socket
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                return True
        except OSError:
            return False

    def gateway_live(self, now=None):
        now = now or time.time()
        if not self.hermes_home or not (self.hermes_home / 'state.db').is_file():
            return False
        try:
            with _ro(self.hermes_home / 'state.db') as db:
                rows = db.execute('select pid, last_heartbeat from gateway_heartbeats order by last_heartbeat desc limit 3').fetchall()
        except sqlite3.Error:
            return False
        return any(now - (beat or 0) < HEARTBEAT_FRESH_SECONDS and pid_alive(pid) for pid, beat in rows)

    def sessions(self, now=None):
        """Every conversation once: id, engines, title, workspace, updated, state, evidence, resume_url.

        The legacy WebUI index and the gateway's state.db both hold the migrated conversations; they are merged by id.
        A conversation present in the gateway resumes in the Webapp (same host); only WebUI-only ones use the legacy port."""
        now = now or time.time()
        webui_live = self.webui_live()
        gateway_live = self.gateway_live(now)
        index = _read_json(self.webui_state / 'sessions' / '_index.json', []) if self.webui_state else []
        names = {p.get('project_id'): p.get('name') for p in _read_json(self.webui_state / 'projects.json', [])} if self.webui_state else {}
        merged = {}
        for row in index if isinstance(index, list) else []:
            if row.get('archived') or not row.get('session_id'):
                continue
            state, evidence = session_state('webui', row, now=now, gateway_live=gateway_live, webui_live=webui_live)
            merged[row['session_id']] = {'id': row['session_id'], 'engines': ['webui'], 'title': row.get('title') or '(untitled)', 'workspace': row.get('workspace') or '',
                                         'updated': row.get('updated_at') or 0, 'messages': row.get('message_count'), 'model': row.get('model'), 'pinned': bool(row.get('pinned')),
                                         'project_name': names.get(row.get('project_id')), 'state': state, 'evidence': evidence}
        if self.hermes_home and (self.hermes_home / 'state.db').is_file():
            try:
                with _ro(self.hermes_home / 'state.db') as db:
                    db.row_factory = sqlite3.Row
                    sessions = db.execute("select id, title, display_name, cwd, git_repo_root, last_activity_at, last_activity_description, started_at, ended_at, end_reason, message_count, model, "
                                          "pinned, archived, hidden from sessions where coalesce(archived,0)=0 and coalesce(hidden,0)=0").fetchall()
                    last = {}
                    for adm in db.execute('select target_session_id, admission_id, status, outcome, seq from session_admissions order by seq'):
                        last[adm['target_session_id']] = {'id': adm['admission_id'], 'status': adm['status'], 'outcome': adm['outcome']}
                    leases = {r[0]: r[1] for r in db.execute('select conversation_id, expires_at from session_turn_leases')}
            except sqlite3.Error:
                sessions, last, leases = [], {}, {}
            for s in sessions:
                row = {'admission': last.get(s['id']), 'lease_expires': leases.get(s['id']), 'ended_at': s['ended_at'], 'end_reason': s['end_reason'],
                       'last_activity': float(s['last_activity_at'] or 0), 'activity_text': s['last_activity_description']}
                state, evidence = session_state('gateway', row, now=now, gateway_live=gateway_live, webui_live=webui_live)
                updated = float(s['last_activity_at'] or s['started_at'] or 0)
                have = merged.get(s['id'])
                if have:                                      # same conversation in both stores: keep the user-chosen WebUI workspace/title, use gateway evidence if it has any
                    have['engines'].append('gateway')
                    have['updated'] = max(have['updated'], updated)
                    if state != 'unknown':
                        have['state'], have['evidence'] = state, evidence
                    continue
                merged[s['id']] = {'id': s['id'], 'engines': ['gateway'], 'title': s['title'] or s['display_name'] or '(untitled)', 'workspace': s['git_repo_root'] or s['cwd'] or '',
                                   'updated': updated, 'messages': s['message_count'], 'model': s['model'], 'pinned': bool(s['pinned']), 'project_name': None, 'state': state, 'evidence': evidence}
        return sorted(merged.values(), key=lambda s: -s['updated'])

    def resume(self, session, port=7000):
        """(url, kind): prefer the Webapp on this host; the legacy WebUI port is a temporary compatibility route."""
        if 'gateway' in session['engines']:
            return 'http://hermes.localhost:%d/%s' % (port, quote(session['id'], safe='')), 'webapp'
        return 'http://localhost:%d/session/%s' % (int(self.environment.get('ODYSSEUS_WEBUI_PORT') or 8787), quote(session['id'], safe='')), 'legacy-webui'

    def related_sessions(self, scope, exclude_ids, limit=8, minimum=3):
        """Conversations that did not run in this project's folders but whose agent tool calls referenced files under them.

        Evidence only (counted from recorded tool calls), shown separately, never counted as the project's own sessions."""
        if not (self.hermes_home and (self.hermes_home / 'state.db').is_file()):
            return []
        patterns = []
        for path in scope:
            forward = os.path.normpath(path).replace(BS, '/').lower()
            patterns += [forward + '/', forward + '"']
        if not patterns:
            return []
        key = (tuple(patterns), minimum)
        cached = self._related_cache.get(key)
        if cached and time.monotonic() - cached[0] < RELATED_TTL_S:
            return [r for r in cached[1] if r['id'] not in exclude_ids][:limit]
        clause = ' or '.join('instr(t, ?) > 0' for _ in patterns)
        flatten = "lower(replace(replace(replace(tool_calls, char(92,92,92,92), char(92)), char(92,92), char(92)), char(92), '/'))"
        try:
            with _ro(self.hermes_home / 'state.db') as db:
                rows = db.execute("select session_id, count(*) from (select session_id, %s as t from messages where role='assistant' and tool_calls is not null) where %s group by 1" % (flatten, clause),
                                  patterns).fetchall()
        except sqlite3.Error:
            return []
        found = [{'id': sid, 'calls': n} for sid, n in sorted(rows, key=lambda r: -r[1]) if n >= minimum]
        if len(self._related_cache) > 64:
            self._related_cache.clear()
        self._related_cache[key] = (time.monotonic(), found)
        return [r for r in found if r['id'] not in exclude_ids][:limit]

    # -- projects --------------------------------------------------------------------------------------------------------------
    def _candidates(self, sessions):
        """(name, path, source, color) records from every registry that already exists."""
        out = []
        projects = _read_json(self.webui_state / 'projects.json', []) if self.webui_state else []
        by_project = {}
        for s in sessions:
            if 'webui' in s['engines'] and s['project_name'] and s['workspace']:
                by_project.setdefault(s['project_name'], []).append(s['workspace'])
        for p in projects:
            for path in dict.fromkeys(by_project.get(p.get('name'), [])):
                out.append((p.get('name'), path, 'WebUI project', p.get('color')))
        for w in _read_json(self.webui_state / 'workspaces.json', []) if self.webui_state else []:
            if w.get('path'):
                out.append((w.get('name'), w['path'], 'WebUI workspace', None))
        if self.hermes_home and (self.hermes_home / 'projects.db').is_file():
            try:
                with _ro(self.hermes_home / 'projects.db') as db:
                    for name, color, path in db.execute('select p.name, p.color, f.path from projects p join project_folders f on f.project_id=p.id where p.archived=0'):
                        out.append((name, path, 'Hermes project', color))
            except sqlite3.Error:
                pass
        for root in self.write_roots():
            out.append((None, root, 'Write grant', None))
        for name, cfg in (self._wiki_config().get('projects') or {}).items():
            if name != 'selftest' and isinstance(cfg, dict) and cfg.get('repo'):
                out.append((name, cfg['repo'], 'Wiki project', None))
        return out

    def projects(self, now=None):
        now = now or time.time()
        sessions = self.sessions(now)
        grouped = {}
        for name, path, source, color in self._candidates(sessions):
            if not os.path.isdir(path):
                key = hashlib.sha1(norm(path).encode()).hexdigest()[:10]
            else:
                key = project_key(path)
            entry = grouped.setdefault(key, {'id': key, 'names': [], 'paths': [], 'sources': [], 'color': color, 'exists': os.path.isdir(path), 'primary': None})
            if source in ('WebUI project', 'Hermes project') and not entry['primary'] and os.path.isdir(path):
                entry['primary'] = path
            if name and name not in entry['names']:
                entry['names'].append(name)
            if path not in entry['paths']:
                entry['paths'].append(path)
            if source not in entry['sources']:
                entry['sources'].append(source)
            entry['color'] = entry['color'] or color
        wiki_cfg = (self._wiki_config().get('projects') or {})
        wiki_by_key = {project_key(c['repo']) if os.path.isdir(c['repo']) else None: n for n, c in wiki_cfg.items() if n != 'selftest' and isinstance(c, dict) and c.get('repo')}
        roots, blocked = self.write_roots(), self.blocked_folders()
        result = []
        for key, e in grouped.items():
            info = repo_info(e['paths'][0]) if e['exists'] else None
            main = str(Path(info['common']).parent) if info and Path(info['common']).name == '.git' and os.path.isdir(Path(info['common']).parent) else e['paths'][0]
            if e['primary']:
                main = e['primary']
            main_info = repo_info(main) if info else None
            names = [n for n in e['names'] if not n.startswith('Home')] or e['names']
            mine = [s for s in sessions if self._belongs(s['workspace'], e['paths'], key)]
            states = {}
            for s in mine:
                states[s['state']] = states.get(s['state'], 0) + 1
            wiki_name = wiki_by_key.get(key)
            handoffs = [h for h in self._wiki_records('handoffs', wiki_name) if h.get('status') == 'open'] if wiki_name else []
            tasks = self._wiki_records('tasks', wiki_name) if wiki_name else []
            result.append({
                'id': key, 'name': names[0] if names else Path(main).name, 'path': main, 'paths': list(dict.fromkeys([main] + e['paths'])), 'sources': e['sources'],
                'color': e['color'], 'exists': e['exists'], 'kind': 'git' if info else ('folder' if e['exists'] else 'missing'),
                'branch': (main_info or {}).get('branch'), 'detached': bool(main_info and main_info['head']), 'is_worktree': bool(main_info and main_info['is_worktree']),
                'permission': self.summary_permission(e['paths'] + [main], roots, blocked),
                'sessions': {'total': len(mine), 'states': states, 'attention': sum(states.get(k, 0) for k in ATTENTION), 'last': max([s['updated'] for s in mine], default=0)},
                'wiki': {'project': wiki_name, 'open_handoffs': len(handoffs), 'open_tasks': sum(1 for t in tasks if task_state(t, now)[0] in ('working', 'interrupted', 'blocked')),
                         'tasks': len(tasks), 'last': max([t.get('created_ts') or 0 for t in tasks], default=0)} if wiki_name else None,
            })
        for r in result:
            r['activity'] = max(r['sessions']['last'], (r['wiki'] or {}).get('last', 0))
        return sorted(result, key=lambda r: (0 if r['sessions']['attention'] or (r['wiki'] or {}).get('open_handoffs') else 1, -r['activity'], r['name'].casefold()))

    @staticmethod
    def _belongs(workspace, paths, key):
        if not workspace:
            return False
        if any(inside(workspace, p) for p in paths):
            return True
        return os.path.isdir(workspace) and project_key(workspace) == key

    def permission(self, path, roots=None, blocked=None):
        roots = self.write_roots() if roots is None else roots
        blocked = self.blocked_folders() if blocked is None else blocked
        # Compare real locations: a junction or symlink inside a granted folder that leads elsewhere is not covered by that grant.
        real = os.path.realpath(path) if path and os.path.exists(path) else path
        grant = next((r for r in roots if inside(real, os.path.realpath(r))), None)
        block = next((b for b in blocked if inside(real, os.path.realpath(b)) or inside(path, b)), None)
        legacy = next((r for r in self.legacy_roots() if inside(real, os.path.realpath(r))), None)
        if block:
            state, why = 'blocked', 'inside a blocked folder (%s)' % block
        elif grant:
            state, why = 'granted', 'inside write grant %s' % grant
        else:
            state, why = 'none', 'no write grant covers this folder'
        return {'state': state, 'reason': why, 'grant': grant, 'legacy_env': legacy, 'blocked_by': block}

    def summary_permission(self, paths, roots, blocked):
        found = [self.permission(p, roots, blocked) for p in dict.fromkeys(paths)]
        for wanted in ('blocked', 'granted'):
            hit = next((f for f in found if f['state'] == wanted), None)
            if hit:
                return {**hit, 'partial': wanted == 'granted' and any(f['state'] != 'granted' for f in found)}
        return {**found[0], 'partial': False}

    # -- one project ------------------------------------------------------------------------------------------------------------
    def detail(self, project_id, now=None, port=7000, related=True):
        if not PROJECT_ID.fullmatch(str(project_id)):
            return None
        now = now or time.time()
        project = next((p for p in self.projects(now) if p['id'] == project_id), None)
        if not project:
            return None
        path = project['path']
        roots, blocked = self.write_roots(), self.blocked_folders()
        git = {'available': False}
        worktrees = []
        if project['kind'] == 'git':
            status = _git(path, 'status', '--porcelain=v1', '-b', '--untracked-files=normal')
            if status is not None:
                lines = status.splitlines()
                header = lines[0] if lines else ''
                files = [{'xy': l[:2], 'path': l[3:]} for l in lines[1:301]]
                ahead = re.search(r'ahead (\d+)', header)
                behind = re.search(r'behind (\d+)', header)
                git = {'available': True, 'header': header[3:], 'changed': sum(1 for f in files if f['xy'] != '??'), 'untracked': sum(1 for f in files if f['xy'] == '??'),
                       'files': files, 'truncated': len(lines) - 1 > 300, 'ahead': int(ahead.group(1)) if ahead else 0, 'behind': int(behind.group(1)) if behind else 0}
            log = _git(path, 'log', '-n', '10', '--date=iso-strict', '--pretty=format:%h%x1f%an%x1f%ad%x1f%s')
            git['commits'] = [dict(zip(('hash', 'author', 'date', 'subject'), l.split('\x1f'))) for l in (log or '').splitlines() if l.count('\x1f') == 3]
            listing = _git(path, 'worktree', 'list', '--porcelain') or ''
            current = {}
            for line in listing.splitlines() + ['']:
                if not line.strip():
                    if current.get('worktree'):
                        wt = os.path.normpath(current['worktree'])
                        info = repo_info(wt) if os.path.isdir(wt) else None
                        worktrees.append({'path': wt, 'branch': (current.get('branch') or '').replace('refs/heads/', '') or None, 'head': (current.get('HEAD') or '')[:9],
                                          'detached': 'detached' in current, 'prunable': 'prunable' in current, 'locked': 'locked' in current,
                                          'permission': self.permission(wt, roots, blocked), 'git_dir': info and info['git_dir'], 'current': norm(wt) == norm(path)})
                    current = {}
                else:
                    key, _, value = line.partition(' ')
                    current[key] = value or True
        scope = list(project['paths']) + [w['path'] for w in worktrees]
        sessions = [s for s in self.sessions(now) if self._belongs(s['workspace'], scope, project_id)]
        for s in sessions:
            s['resume_url'], s['resume_kind'] = self.resume(s, port)
        placed = {s['id'] for s in sessions}
        by_id = {s['id']: s for s in self.sessions(now)}
        related_list = self._related_list(scope, placed, by_id, port) if related else None
        wiki = None
        if project['wiki']:
            name = project['wiki']['project']
            tasks = sorted(self._wiki_records('tasks', name), key=lambda t: -(t.get('created_ts') or 0))
            wiki = {'project': name, 'root': str(self.wiki_root), 'tasks': [], 'handoffs': []}
            for t in tasks[:12]:
                state, evidence = task_state(t, now)
                wiki['tasks'].append({'id': t.get('id'), 'agent': t.get('agent'), 'summary': t.get('summary'), 'status': t.get('status'), 'state': state, 'evidence': evidence,
                                      'commits': [c[:9] for c in t.get('commits') or []], 'unverified_commits': len(t.get('unverified_commits') or []), 'files': (t.get('files') or [])[:6],
                                      'created_ts': t.get('created_ts')})
            for h in sorted(self._wiki_records('handoffs', name), key=lambda h: -(h.get('created_ts') or 0))[:10]:
                wiki['handoffs'].append({'id': h.get('id'), 'from': h.get('from'), 'to': h.get('to'), 'status': h.get('status'), 'auto': bool(h.get('auto')), 'summary': h.get('summary'),
                                         'next_steps': h.get('next_steps') or [], 'open_questions': h.get('open_questions') or [], 'created_ts': h.get('created_ts')})
        permission = {'path': path, 'gateway': project['permission'], 'write_roots': roots, 'blocked': blocked, 'legacy_env_roots': self.legacy_roots(),
                      'request': self.request_command(path), 'request_template': self.request_command('{path}'), 'revoke_template': self.revoke_command('{path}'), 'revoke': self.revoke_command(project['permission'].get('grant')),
                      'note': 'These are file-tool write rules, not OS containment: a terminal, MCP servers and any program running as you are not limited by them. '
                              '"Allow for this chat" approvals are held in memory by the running Hermes process and cannot be listed here.'}
        return {**project, 'git': git, 'worktrees': worktrees, 'session_list': sessions, 'related_sessions': related_list, 'related_pending': related_list is None, 'wiki_detail': wiki, 'permission_detail': permission, 'generated_at': now}

    def _related_list(self, scope, placed, by_id, port):
        out = []
        for hit in self.related_sessions(scope, placed):
            base = by_id.get(hit['id'])
            if base:
                url, kind = self.resume(base, port)
                out.append({**base, 'calls': hit['calls'], 'resume_url': url, 'resume_kind': kind,
                            'evidence': 'inferred: %d agent tool calls referenced files under this project (it ran in %s)' % (hit['calls'], base['workspace'] or 'no recorded folder')})
        return out

    def related_detail(self, project_id, now=None, port=7000):
        """The inferred-conversation list for one project (the slow part of detail), or None for an unknown project."""
        if not PROJECT_ID.fullmatch(str(project_id)):
            return None
        now = now or time.time()
        project = next((p for p in self.projects(now) if p['id'] == project_id), None)
        if not project:
            return None
        scope = list(project['paths'])
        for line in (_git(project['path'], 'worktree', 'list', '--porcelain') or '').splitlines() if project['kind'] == 'git' else []:
            if line.startswith('worktree '):
                scope.append(os.path.normpath(line[9:]))
        sessions = self.sessions(now)
        placed = {s['id'] for s in sessions if self._belongs(s['workspace'], scope, project_id)}
        return self._related_list(scope, placed, {s['id']: s for s in sessions}, port)

    @staticmethod
    def request_command(path):
        return 'venv\\Scripts\\python.exe scripts\\hermes-webapp-component.py roots --add-root "%s"' % path

    @staticmethod
    def revoke_command(grant):
        return ('venv\\Scripts\\python.exe scripts\\hermes-webapp-component.py roots --remove-root "%s"' % grant) if grant else None

    def overview(self, now=None):
        now = now or time.time()
        projects = self.projects(now)
        sessions = self.sessions(now)
        placed = {s['id'] for p in projects for s in sessions if self._belongs(s['workspace'], p['paths'], p['id'])}
        states = {}
        for s in sessions:
            states[s['state']] = states.get(s['state'], 0) + 1
        return {'generated_at': now, 'projects': projects, 'sessions': {'total': len(sessions), 'states': states, 'unplaced': sum(1 for s in sessions if s['id'] not in placed)},
                'gateway_live': self.gateway_live(now), 'webui_live': self.webui_live(),
                'sources': {'webui_projects': bool(self.webui_state and (self.webui_state / 'projects.json').is_file()), 'webapp_projects_db': bool(self.hermes_home and (self.hermes_home / 'projects.db').is_file()),
                            'write_grants': len(self.write_roots()), 'wiki': bool(self._wiki_config())}}


def from_environment(environment=None):
    env = os.environ if environment is None else environment
    local = env.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    root = Path(env.get('ODYSSEUS_HERMES_COMPONENT_ROOT') or env.get('ODYSSEUS_HERMES_ROOT') or Path(local) / 'Odysseus' / 'hermes')
    wiki = env.get('ODYSSEUS_WIKI_ROOT') or r'F:\Projects\Odysseus-Wiki'
    return Dashboard(hermes_home=env.get('ODYSSEUS_HERMES_HOME') or root / 'state' / 'hermes-gateway',
                     webui_state=env.get('ODYSSEUS_WEBUI_STATE') or Path(local) / 'hermes' / 'webui', component_root=root, wiki_root=wiki if os.path.isdir(wiki) else None, environment=env)
