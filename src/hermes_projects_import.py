"""Bring the legacy WebUI projects into the Hermes Webapp's own project store (operator tool, dry run by default).

The Webapp keeps first-class projects in the gateway home's ``projects.db`` and groups conversations by FOLDER: a conversation belongs to the project that
owns its working folder. The legacy WebUI instead tagged conversations with a project id. Every legacy project here has its conversations in one folder,
so creating the matching Webapp project (name, colour, that folder) is enough for the Webapp's sidebar to group them; no conversation is moved or edited.

What this never does: move or rewrite a conversation, change a working folder, touch write grants, or run inside the web app. It writes only through the
gateway's own ``projects.create`` method, with the gateway's session token read from the local file, from a command the operator starts.
"""
import asyncio
import collections
import itertools
import json
import os
import re
from pathlib import Path

COLOUR = re.compile(r'#[0-9a-fA-F]{6}')


def _key(path):
    """Canonical comparison form: real location (junctions and links resolved when the folder is reachable), normalised, case-folded on Windows."""
    text = str(path)
    try:
        text = os.path.realpath(text)
    except OSError:
        pass
    return os.path.normcase(os.path.normpath(text)).rstrip('\\/')


def _within(child, parent):
    return child == parent or child.startswith(parent + os.sep)


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def legacy_projects(webui_state, folder_overrides=None):
    """[{name, colour, folder, conversations}] from the WebUI registry; folder is the one existing directory most of the project's conversations live in."""
    state = Path(webui_state)
    registry = _read(state / 'projects.json', [])
    index = _read(state / 'sessions' / '_index.json', [])
    if isinstance(index, dict):
        index = index.get('sessions') or []
    folders = collections.defaultdict(collections.Counter)
    for row in index:
        if isinstance(row, dict) and row.get('project_id') and row.get('workspace'):
            folders[row['project_id']][str(row['workspace'])] += 1
    overrides = {str(k).casefold(): v for k, v in (folder_overrides or {}).items()}
    found = []
    for entry in registry if isinstance(registry, list) else []:
        if not isinstance(entry, dict) or not entry.get('name'):
            continue
        counts = folders.get(entry.get('project_id'), collections.Counter())
        usable = [(n, f) for f, n in counts.most_common() if os.path.isdir(f)]
        name = str(entry['name']).strip()
        chosen = overrides.get(name.casefold())
        if chosen and os.path.isdir(chosen):             # an operator-named current folder for a project whose recorded folder is gone
            folder, seen = str(chosen), sum(counts.values())
        else:
            folder, seen = (usable[0][1], usable[0][0]) if usable else (None, 0)
        found.append({'name': name, 'colour': entry.get('color') if COLOUR.fullmatch(str(entry.get('color') or '')) else None,
                      'folder': folder, 'conversations': seen, 'folders_seen': list(counts),
                      'attach_history': [f for f in counts if not os.path.isdir(f)] if chosen and folder == str(chosen) else []})
    return found


def plan(legacy, existing):
    """One action per legacy project: create, or skip with the reason. ``existing`` is the project list (archived ones included).
    Ownership is by real location: an equal folder, a folder inside an existing project's folder, or a folder that would swallow an existing one all skip.
    Re-running after an apply therefore plans nothing."""
    owned = [(_key(f['path']), p) for p in existing for f in (p.get('folders') or [])]
    owned += [(_key(p['primary_path']), p) for p in existing if p.get('primary_path')]
    names = {str(p.get('name', '')).casefold(): p for p in existing}
    actions = []
    taken = []
    for item in legacy:
        folder = item['folder']
        key = _key(folder) if folder else None
        owner = next((p for k, p in owned if key and _within(key, k)), None)
        swallowed = next((p for k, p in owned if key and _within(k, key) and k != key), None)
        clash = names.get(item['name'].casefold())
        if not folder:
            seen = ', '.join(item.get('folders_seen') or []) or 'none recorded'
            why = f'its conversations\' folder is missing or offline right now (recorded: {seen}); nothing is guessed, re-run when it is reachable'
            actions.append({**item, 'action': 'skip', 'reason': why})
        elif owner:
            actions.append({**item, 'action': 'skip', 'reason': f"folder already belongs to Hermes project '{owner.get('name')}'" + (' (archived)' if owner.get('archived') else '')})
        elif swallowed:
            actions.append({**item, 'action': 'skip', 'reason': f"folder would contain Hermes project '{swallowed.get('name')}'; decide by hand"})
        elif clash:
            actions.append({**item, 'action': 'skip', 'reason': 'a Hermes project with that name already exists' + (' (archived)' if clash.get('archived') else '')})
        elif any(_key(t['folder']) == key for t in taken):
            actions.append({**item, 'action': 'skip', 'reason': 'another legacy project in this run already uses that folder'})
        else:
            actions.append({**item, 'action': 'create', 'reason': ''})
            taken.append(item)
    return actions


def existing_from_db(home):
    """The Hermes project list read straight from the gateway home's projects.db, read-only: needs no token and cannot change anything. [] when absent."""
    db = Path(home) / 'projects.db'
    if not db.is_file():
        return []
    import sqlite3
    uri = 'file:' + str(db).replace('\\', '/') + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        folders = {}
        for row in conn.execute('select project_id, path from project_folders'):
            folders.setdefault(row['project_id'], []).append({'path': row['path']})
        return [{'id': r['id'], 'name': r['name'], 'primary_path': r['primary_path'], 'archived': bool(r['archived']), 'folders': folders.get(r['id'], [])}
                for r in conn.execute('select id, name, primary_path, archived from projects')]


def backup_db(home, destination):
    """Consistent copy of projects.db (SQLite online backup, safe while the gateway runs) into ``destination``; returns the file or None when there is no database yet."""
    db = Path(home) / 'projects.db'
    if not db.is_file():
        return None
    import sqlite3
    import time
    Path(destination).mkdir(parents=True, exist_ok=True)
    target = Path(destination) / f"projects-{time.strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect('file:' + str(db).replace('\\', '/') + '?mode=ro', uri=True)
    try:
        copy = sqlite3.connect(str(target))
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return target


class GatewayRpc:
    """Minimal JSON-RPC client for the gateway's own websocket (one connection per command)."""

    def __init__(self, url, token):
        self.url, self.token = url.rstrip('/'), token

    async def run(self, calls):
        import websockets
        host = self.url.split('://', 1)[1]
        ids = itertools.count(1)
        results = []
        async with websockets.connect(f'ws://{host}/api/ws?token={self.token}', additional_headers={'Origin': f'http://{host}'}, open_timeout=15, max_size=2 ** 24) as ws:
            await asyncio.wait_for(ws.recv(), 10)                      # gateway.ready
            for method, params in calls:
                rid = f'p{next(ids)}'
                await ws.send(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}))
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 30))
                    if message.get('id') == rid:
                        if 'error' in message:
                            raise RuntimeError(f"{method}: {message['error'].get('message')}")
                        results.append(message.get('result'))
                        break
        return results

    def call(self, method, params=None):
        return asyncio.run(self.run([(method, params or {})]))[0]


def token_for(home):
    return (Path(home) / 'webapp' / 'session-token').read_text(encoding='utf-8').strip()


def execute(actions, rpc):
    """Create the planned projects through ``projects.create``. Returns [(name, 'created'|'failed: ...')]."""
    done = []
    for item in actions:
        if item['action'] != 'create':
            continue
        params = {'name': item['name'], 'folders': [item['folder']], 'primary_path': item['folder']}
        if item.get('colour'):
            params['color'] = item['colour']
        try:
            created = rpc.call('projects.create', params)
            project_id = ((created or {}).get('project') or {}).get('id')
            for old in item.get('attach_history') or []:     # recorded folders that no longer exist: attached as extra folders so those conversations keep their working folder and still group
                rpc.call('projects.add_folder', {'id': project_id, 'path': old})
            done.append((item['name'], 'created' + (f" (+{len(item['attach_history'])} historical folder(s))" if item.get('attach_history') else '')))
        except Exception as exc:                                       # one failure must not stop the rest
            done.append((item['name'], f'failed: {exc}'))
    return done
