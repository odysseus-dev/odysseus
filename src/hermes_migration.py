"""Move a user's existing Hermes home into an Odysseus-managed Webapp gateway home.

The SOURCE is only ever read (SQLite through read-only snapshots, files copied into a bundle). The TARGET must be the home of the managed Webapp
component, its gateway must be stopped, and it must hold no sessions yet. Nothing here installs packages, touches credentials by default, or
starts a process; the only subprocesses are the Hermes interpreter running upstream's own backup-import code (with its gateway-revival step
disabled) and a small YAML helper.

Bundle (a directory): data.zip (portable files, upstream backup format), config.filtered.yaml, env.settings, blocked.json, manifest.json.
"""
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path

BUNDLE_VERSION = 1
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

CREDENTIAL_NAME = re.compile(r'(API_?KEY|_TOKEN|_SECRET|PASSWORD|PASSPHRASE|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY)', re.I)
SECRET_PATH = re.compile(r'(key|token|secret|password|credential)', re.I)

COPY_DIRS = ('memories', 'skills', 'plugins', 'bot_relay', 'kanban', 'pets', 'pastes', 'assets', 'hooks', 'cron')
COPY_FILES = ('SOUL.md', 'ui-preferences.json', '.hermes_history', 'import-sync.json', 'channel_directory.json')
DB_FILES = ('state.db', 'projects.db', 'shared-state.db', 'kanban.db', 'cron/executions.db')
EXCLUDED_NAMES = {'__pycache__', '.git', 'node_modules', '.locks', '.tick.lock', '.jobs.lock', 'ticker_heartbeat', 'ticker_last_success', '.vault.lock'}
EXCLUDED_PATTERNS = ('*.pyc', '*.lock', '*.db-wal', '*.db-shm', '*.db-journal', '*.tmp')
CREDENTIAL_FILES = {'auth.json', '.env', 'vault.key', 'vault.json.enc', '.signing_key', '.pbkdf2_key', '.sessions.json', 'session-token', 'id_rsa', 'id_ed25519'}
CREDENTIAL_FILE_PATTERNS = ('.env.*', 'auth.json.*', '*.pem', '*.p12', '*.pfx', '*.key')
DATABASE_PATTERNS = ('*.db', '*.sqlite', '*.sqlite3')

IMPORT_SECTIONS = ('model', 'fallback_model', 'auxiliary', 'agent', 'attachments', 'browser', 'code_execution', 'compression', 'cron', 'delegation', 'display',
                   'group_sessions_per_user', 'kanban', 'max_concurrent_sessions', 'memory', 'onboarding', 'platform_toolsets', 'plugins', 'prompt_caching',
                   'skills', 'streaming', 'stt', 'terminal', 'tool_loop_guardrails', 'mcp_servers')
NEVER_IMPORT = {
    'webapp': 'owned by the managed component (port, sign-in gate)',
    'desktop': 'owned by the managed component (repository discovery limits)',
    'approvals': 'a security decision made on the managed gateway, never inherited',
    'command_allowlist': 'earlier "always allow" decisions are not carried into a gateway with the route guard',
    'gateway': 'install specific (messaging adapters, watchdogs)',
    'auth': 'install specific (which external logins to adopt)',
    'database': 'install specific', 'runtime': 'install specific', 'updates': 'the managed component pins its own version',
    'telemetry': 'a privacy choice made again on the new gateway', '_config_version': 'schema of the target', '_left_core_installed': 'derived',
    'known_builtin_toolsets': 'derived', 'known_plugin_toolsets': 'derived',
}


class MigrationError(RuntimeError):
    pass


def _sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


def _excluded(name):
    return name in EXCLUDED_NAMES or any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDED_PATTERNS)


def _credential_file(name):
    return name.lower() in CREDENTIAL_FILES or any(fnmatch.fnmatch(name.lower(), pattern) for pattern in CREDENTIAL_FILE_PATTERNS)


def _database_file(name):
    return any(fnmatch.fnmatch(name.lower(), pattern) for pattern in DATABASE_PATTERNS)


def _is_link(path):
    try:
        return os.path.islink(path) or bool(os.stat(path, follow_symlinks=False).st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return os.path.islink(path)


def tree_size(path, limit_seconds=20):
    end, files, total = time.monotonic() + limit_seconds, 0, 0
    for directory, directories, names in os.walk(path):
        directories[:] = [d for d in directories if d not in EXCLUDED_NAMES and not _is_link(os.path.join(directory, d))]
        for name in names:
            try:
                total += os.path.getsize(os.path.join(directory, name))
                files += 1
            except OSError:
                pass
        if time.monotonic() > end:
            return files, total, True
    return files, total, False


def classify(name, is_dir):
    """(category, reason) for one top-level entry of a Hermes home."""
    if name in DB_FILES or (not is_dir and name.endswith('.db')):
        return ('copy-db', 'conversation or project data, copied as a consistent snapshot') if name in DB_FILES else ('skip-runtime', 'unknown database, not copied')
    if name in COPY_DIRS and is_dir:
        return 'copy', 'user data'
    if name in COPY_FILES:
        return 'copy', 'user data'
    if name == 'config.yaml':
        return 'merge-config', 'imported section by section, never overwriting the managed settings'
    if name == '.env':
        return 'env', 'non-credential settings are carried; credentials are listed and need an explicit decision'
    if name == 'auth.json':
        return 'reauth', 'OAuth tokens rotate when refreshed: copying them can break the old install, so sign in again'
    if name == 'vault':
        return 'convert', 'the old WebUI blocked-folder list becomes the neutral Odysseus list'
    if name == 'profiles':
        return 'manual', 'each profile has its own credentials and databases: migrate one at a time, never in bulk'
    if name == 'webui':
        return 'stay', 'the old WebUI keeps its own state (web logins, signing keys, its project grouping); it stays where it is'
    if name in ('install_id', '.install_id.lock', 'gateway.lock', 'gateway.pid', 'gateway_state.json', 'gateway-starts.log', 'gateway-connections.json', 'gateway-service',
                'state', 'locks', 'processes.json', 'spawn-ledger.json', 'runtime', 'terminal-sessions', 'sandboxes', 'pending', 'pending_messages', 'pairing',
                'platforms', 'shared', 'telemetry', 'source-checks', 'plugin-update-checks', 'desktop-plugins', 'disk-cleanup'):
        return 'skip-runtime', 'identity or runtime state of the running install; the new gateway creates its own'
    if name in ('hermes-agent', 'installs', 'tools', 'lsp', 'bin'):
        return 'skip-code', 'program files; the managed component has its own'
    if name in ('cache', 'audio_cache', 'image_cache', 'models_dev_cache.json', 'models_dev_cache.etag', 'provider_models_cache.json', 'context_length_cache.yaml',
                '.skills_prompt_snapshot.json', 'sessions', 'logs', 'backups', '.curator_backups', 'import', 'checkpoints'):
        return 'skip-cache', 'cache, log, backup or debug dump; regenerated or kept where it is'
    if name.startswith(('config.yaml.', '.env.')) or name.endswith(('.lock', '.bak')):
        return 'skip-cache', 'backup or lock file'
    return 'skip-unknown', 'not recognised; left alone so nothing unreviewed moves'


def _env_lines(path):
    names = {}
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            text = line.strip()
            if text and not text.startswith('#') and '=' in text:
                key, _, value = text.partition('=')
                names[key.strip()] = value
    return names


def classify_env(names):
    """Split environment variable NAMES into (settings, credentials). Values are never inspected beyond the name."""
    settings, credentials = [], []
    for name in names:
        (credentials if CREDENTIAL_NAME.search(name) else settings).append(name)
    return sorted(settings), sorted(credentials)


def db_counts(path):
    try:
        with closing(sqlite3.connect('file:' + str(path).replace('\\', '/') + '?mode=ro', uri=True, timeout=10)) as connection:
            sessions = connection.execute('select count(*) from sessions').fetchone()[0]
            messages = connection.execute('select count(*) from messages').fetchone()[0]
        return {'sessions': sessions, 'messages': messages}
    except sqlite3.Error:
        return None


def inventory(source):
    """Read-only description of a Hermes home: what each part is, how big it is, and what a migration would do with it."""
    source = Path(source)
    if not (source / 'config.yaml').is_file():
        raise MigrationError('That folder does not look like a Hermes home (no config.yaml).')
    items = []
    for entry in sorted(os.listdir(source)):
        path = source / entry
        is_dir = path.is_dir()
        category, reason = classify(entry, is_dir)
        files, size, truncated = (tree_size(path, 5) if category != 'skip-code' else (None, None, True)) if is_dir else (1, path.stat().st_size, False)
        items.append({'name': entry, 'kind': 'dir' if is_dir else 'file', 'category': category, 'reason': reason, 'files': files, 'bytes': size, 'size_truncated': truncated})
    report = {'source': str(source), 'items': items}
    if (source / '.env').is_file():
        settings, credentials = classify_env(_env_lines(source / '.env'))
        report['env'] = {'settings': settings, 'credentials': credentials}
    if (source / 'auth.json').is_file():
        try:
            data = json.loads((source / 'auth.json').read_text(encoding='utf-8'))
            report['auth_json'] = {'providers': sorted((data.get('providers') or {}).keys()), 'credential_pool': {k: len(v) for k, v in (data.get('credential_pool') or {}).items()}}
        except (OSError, ValueError):
            report['auth_json'] = {'error': 'unreadable'}
    report['databases'] = {name: db_counts(source / name) for name in DB_FILES if (source / name).is_file()}
    webui = source / 'webui' / 'projects.json'
    if webui.is_file():
        try:
            report['webui_projects'] = [project.get('name') for project in json.loads(webui.read_text(encoding='utf-8'))]
        except (OSError, ValueError, AttributeError):
            pass
    report['profiles'] = sorted(p.name for p in (source / 'profiles').iterdir() if p.is_dir()) if (source / 'profiles').is_dir() else []
    jobs = source / 'cron' / 'jobs.json'
    try:
        report['cron_jobs'] = len(json.loads(jobs.read_text(encoding='utf-8')).get('jobs', [])) if jobs.is_file() else 0
    except (OSError, ValueError, AttributeError):
        report['cron_jobs'] = None
    report['plugins'] = sorted(p.name for p in (source / 'plugins').iterdir() if p.is_dir()) if (source / 'plugins').is_dir() else []
    return report


_FILTER_CONFIG = r'''
import sys, json, re
from ruamel.yaml import YAML
src, out, allowed, never, pattern = sys.argv[1], sys.argv[2], set(json.loads(sys.argv[3])), json.loads(sys.argv[4]), re.compile(sys.argv[5], re.I)
yaml = YAML(typ="safe")
with open(src, encoding="utf-8") as handle: cfg = yaml.load(handle) or {}
kept, dropped, secrets = {}, {}, []
def strip(value, path):
    if isinstance(value, dict):
        clean = {}
        for k, v in value.items():
            p = path + "." + str(k)
            if pattern.search(str(k)) and isinstance(v, str) and v.strip() and not v.strip().startswith("${"):
                secrets.append(p); continue
            clean[k] = strip(v, p)
        return clean
    if isinstance(value, list):
        return [strip(v, path + "[]") for v in value]
    return value
for key, value in cfg.items():
    if key in allowed: kept[key] = strip(value, key)
    else: dropped[key] = never.get(key, "not an importable section")
terminal = kept.get("terminal")
if isinstance(terminal, dict): terminal.pop("cwd", None)
rt = YAML(); rt.default_flow_style = False
with open(out, "w", encoding="utf-8") as handle: rt.dump(kept, handle)
print(json.dumps({"config_version": cfg.get("_config_version"), "kept": sorted(kept), "dropped": dropped, "secret_values_removed_at": secrets}))
'''

_MERGE_CONFIG = r'''
import sys, json, time, shutil, os
from ruamel.yaml import YAML
src, dst, apply_it, agent = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
safe = YAML(typ="safe")
with open(src, encoding="utf-8") as handle: incoming = safe.load(handle) or {}
rt = YAML(); rt.preserve_quotes = True; rt.default_flow_style = False
with open(dst, encoding="utf-8") as handle: target = rt.load(handle) or {}
guard = None
if agent:
    sys.path.insert(0, agent)
    try:
        from hermes_cli.web_route_guard import mcp_command_error as guard
    except Exception: guard = None
imported, held, notes = [], [], []
for key, value in incoming.items():
    if key == "plugins" and isinstance(value, dict) and isinstance(target.get("plugins"), dict):
        merged = target["plugins"]
        for name in ("enabled", "disabled"):
            have = list(merged.get(name) or []); extra = [x for x in (value.get(name) or []) if x not in have]
            if extra or name not in merged: merged[name] = have + extra
        for k, v in value.items():
            if k not in ("enabled", "disabled"): merged[k] = v
        imported.append(key); continue
    if key == "mcp_servers" and isinstance(value, dict):
        ok = {}
        for name, spec in value.items():
            command = spec.get("command") if isinstance(spec, dict) else None
            problem = guard(command, spec.get("args") if isinstance(spec, dict) else None) if guard else None
            if problem: held.append({"server": name, "reason": problem})
            else: ok[name] = spec
        if ok:
            current = target.get("mcp_servers") if isinstance(target.get("mcp_servers"), dict) else {}
            current.update(ok); target["mcp_servers"] = current; imported.append("mcp_servers (" + ", ".join(sorted(ok)) + ")")
        if not guard: notes.append("route-guard check unavailable: MCP servers were imported without it" if ok else "")
        continue
    target[key] = value; imported.append(key)
if apply_it:
    shutil.copy2(dst, dst + ".pre-migration-" + time.strftime("%Y%m%d-%H%M%S"))
    tmp = dst + ".migrating"
    with open(tmp, "w", encoding="utf-8") as handle: rt.dump(target, handle)
    os.replace(tmp, dst)
print(json.dumps({"imported": imported, "held_back": held, "notes": [n for n in notes if n], "applied": apply_it}))
'''

_IMPORT_HELPER = r'''
import sys, os, types, json, io, contextlib
home, bundle, agent = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["HERMES_HOME"] = home
sys.path.insert(0, agent)
import hermes_cli.backup as backup
backup._revive_gateway_after_import = lambda *a, **k: None
buffer = io.StringIO()
code = 0
try:
    with contextlib.redirect_stdout(buffer):
        result = backup.run_import(types.SimpleNamespace(zipfile=bundle, force=True))
    code = result or 0
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
print(buffer.getvalue()[-6000:])
print("RESULT " + json.dumps({"code": code}))
sys.exit(0 if code == 0 else 1)
'''


def _run_python(python, code, *args, env=None, timeout=600):
    process_env = {k: v for k, v in (env if env is not None else os.environ).items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'HERMES_HOME')}
    process_env.update({'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    try:
        return subprocess.run([str(python), '-I', '-B', '-c', code, *map(str, args)], capture_output=True, text=True, timeout=timeout, env=process_env, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError('The Hermes interpreter could not be run.') from exc


def _snapshot_db(source, destination):
    with closing(sqlite3.connect('file:' + str(source).replace('\\', '/') + '?mode=ro', uri=True, timeout=30)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        if copy.execute('pragma quick_check').fetchone()[0] != 'ok':
            raise MigrationError(f'The snapshot of {Path(source).name} failed its integrity check; nothing was bundled.')


def _walk_copy(source, relative, zf, manifest, errors, left_out):
    base = source / relative
    for directory, directories, names in os.walk(base):
        directories[:] = [d for d in directories if not _excluded(d) and not _is_link(os.path.join(directory, d))]
        for name in sorted(names):
            if _excluded(name):
                continue
            path = Path(directory) / name
            rel = path.relative_to(source).as_posix()
            if rel in DB_FILES or _is_link(path):
                continue
            if _credential_file(name):
                left_out['credential_files'].append(rel)
                continue
            if _database_file(name):
                left_out['databases'].append(rel)
                continue
            try:
                zf.write(path, rel)
                manifest[rel] = _sha256(path)
            except OSError as exc:
                errors.append(f'{rel}: {exc.__class__.__name__}')


def build_bundle(source, out_dir, python, carry_credentials=()):
    """Create the bundle directory from a Hermes home (read-only on the source). ``carry_credentials`` names the .env credentials the user explicitly chose to carry."""
    source, out_dir = Path(source), Path(out_dir)
    report = inventory(source)
    if out_dir.exists():
        raise MigrationError('The bundle folder already exists; it will not be reused.')
    work = out_dir.with_name(out_dir.name + '.partial')
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    errors, entries = [], {}
    left_out = {'credential_files': [], 'databases': []}
    try:
        with tempfile.TemporaryDirectory(dir=str(work)) as scratch, zipfile.ZipFile(work / 'data.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in report['items']:
                if item['category'] == 'copy':
                    if item['kind'] == 'dir':
                        _walk_copy(source, item['name'], zf, entries, errors, left_out)
                    elif not _credential_file(item['name']):
                        zf.write(source / item['name'], item['name'])
                        entries[item['name']] = _sha256(source / item['name'])
            for name in DB_FILES:
                if (source / name).is_file():
                    snapshot = Path(scratch) / name.replace('/', '_')
                    _snapshot_db(source / name, snapshot)
                    zf.write(snapshot, name)
                    entries[name] = _sha256(snapshot)
        settings, credentials = (report.get('env') or {}).get('settings', []), (report.get('env') or {}).get('credentials', [])
        unknown = [name for name in carry_credentials if name not in credentials]
        if unknown:
            raise MigrationError('Not a credential in that .env: ' + ', '.join(unknown))
        if (source / '.env').is_file():
            values = _env_lines(source / '.env')
            lines = [f'{name}={values[name]}' for name in settings + sorted(carry_credentials) if name in values]
            (work / 'env.settings').write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
        blocked = []
        legacy = source / 'vault' / 'webui-trusted-workspaces.json'
        if legacy.is_file():
            try:
                blocked = [item['path'] for item in json.loads(legacy.read_text(encoding='utf-8')).get('blocked', []) if isinstance(item, dict) and item.get('path')]
            except (OSError, ValueError, AttributeError):
                raise MigrationError('The old blocked-folder list is unreadable; fix or remove it before migrating.')
        (work / 'blocked.json').write_text(json.dumps({'version': 1, 'blocked': [{'path': path} for path in blocked]}), encoding='utf-8')
        done = _run_python(python, _FILTER_CONFIG, source / 'config.yaml', work / 'config.filtered.yaml', json.dumps(list(IMPORT_SECTIONS)), json.dumps(NEVER_IMPORT), SECRET_PATH.pattern)
        if done.returncode:
            raise MigrationError('The configuration could not be filtered: ' + done.stderr[-300:])
        config = json.loads(done.stdout.strip().splitlines()[-1])
        if errors:
            raise MigrationError(f'{len(errors)} file(s) could not be read (first: {errors[0]}); the bundle was not finalized.')
        manifest = {
            'version': BUNDLE_VERSION, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'source': str(source), 'config': config,
            'databases': report['databases'], 'env': {'settings_carried': settings, 'credentials_carried': sorted(carry_credentials),
                                                      'credentials_not_carried': [c for c in credentials if c not in carry_credentials]},
            'auth_json': report.get('auth_json'), 'plugins': report['plugins'], 'profiles': report['profiles'], 'cron_jobs': report['cron_jobs'],
            'blocked_folders': len(blocked), 'webui_projects_not_transferred': report.get('webui_projects', []), 'data_zip_sha256': _sha256(work / 'data.zip'),
            'entries': len(entries), 'entry_sha256': entries, 'left_out': left_out,
            'categories': {item['name']: item['category'] for item in report['items']},
        }
        (work / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
        os.replace(work, out_dir)
        return manifest
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def read_manifest(bundle_dir):
    bundle_dir = Path(bundle_dir)
    try:
        manifest = json.loads((bundle_dir / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MigrationError('That is not a migration bundle.') from exc
    if manifest.get('version') != BUNDLE_VERSION or _sha256(bundle_dir / 'data.zip') != manifest.get('data_zip_sha256'):
        raise MigrationError('The bundle is the wrong version or was modified after it was built; build it again.')
    return manifest


def _live_gateway(home):
    try:
        record = json.loads((Path(home) / 'gateway.pid').read_text(encoding='utf-8'))
        pid = record.get('pid')
    except (OSError, ValueError):
        return False
    if not isinstance(pid, int) or pid <= 4:
        return False
    try:
        if os.name != 'nt':
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            return True
        output = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW).stdout
        return str(pid) in output
    except (OSError, subprocess.TimeoutExpired):
        return True                                           # cannot tell: assume it is running


def preflight(bundle_dir, target_home, source=None):
    """Everything that must be true before anything is written. Returns a list of problems (empty = go)."""
    problems = []
    target = Path(target_home)
    manifest = read_manifest(bundle_dir)
    if not (target / 'config.yaml').is_file():
        problems.append('The target is not a prepared managed home (no config.yaml).')
    if source is not None and os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(target)):
        problems.append('Source and target are the same folder.')
    if os.path.normcase(os.path.realpath(manifest['source'])) == os.path.normcase(os.path.realpath(target)):
        problems.append('The bundle was made from this very home.')
    if _live_gateway(target):
        problems.append('A gateway is running on the target home; stop it first.')
    counts = db_counts(target / 'state.db') if (target / 'state.db').is_file() else None
    if counts and counts['sessions']:
        problems.append(f"The target already holds {counts['sessions']} session(s); a migration only fills an empty managed home.")
    return problems


def apply_bundle(bundle_dir, target_home, python, agent, backup_dir):
    """Write the bundle into the (stopped, empty, managed) target home. A safety copy of the target comes first; the source is never touched."""
    bundle_dir, target = Path(bundle_dir), Path(target_home)
    problems = preflight(bundle_dir, target)
    if problems:
        raise MigrationError('Not applied: ' + ' '.join(problems))
    manifest = read_manifest(bundle_dir)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    safety = backup_dir / f'pre-migration-{stamp}.zip'
    with zipfile.ZipFile(safety, 'w', zipfile.ZIP_DEFLATED) as zf:
        for directory, directories, names in os.walk(target):
            directories[:] = [d for d in directories if d not in ('node_modules', '__pycache__', 'logs', 'cache')]
            for name in names:
                path = Path(directory) / name
                if not name.endswith(('.lock', '-wal', '-shm')):
                    try:
                        zf.write(path, path.relative_to(target).as_posix())
                    except OSError:
                        pass
    done = _run_python(python, _IMPORT_HELPER, target, bundle_dir / 'data.zip', agent, timeout=1800)
    if done.returncode:
        raise MigrationError('The upstream import did not complete; the safety copy is ' + str(safety) + '. ' + (done.stdout + done.stderr)[-400:])
    merged = _run_python(python, _MERGE_CONFIG, bundle_dir / 'config.filtered.yaml', target / 'config.yaml', '1', agent)
    if merged.returncode:
        raise MigrationError('The configuration merge failed; the safety copy is ' + str(safety) + '. ' + merged.stderr[-300:])
    result = json.loads(merged.stdout.strip().splitlines()[-1])
    env_file = bundle_dir / 'env.settings'
    added = []
    if env_file.is_file() and env_file.read_text(encoding='utf-8').strip():
        target_env = target / '.env'
        existing = _env_lines(target_env) if target_env.is_file() else {}
        lines = [line for line in env_file.read_text(encoding='utf-8').splitlines() if line.partition('=')[0] not in existing]
        if lines:
            with open(target_env, 'a', encoding='utf-8', newline='\n') as handle:
                handle.write(('' if not target_env.exists() or target_env.stat().st_size == 0 else '\n') + '\n'.join(lines) + '\n')
            added = [line.partition('=')[0] for line in lines]
    blocked = json.loads((bundle_dir / 'blocked.json').read_text(encoding='utf-8'))
    if blocked['blocked']:
        (target / 'vault').mkdir(exist_ok=True)
        neutral = target / 'vault' / 'odysseus-protected-paths.json'
        neutral.write_text(json.dumps(blocked, indent=1), encoding='utf-8')
    record = {'applied_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'bundle_sha256': manifest['data_zip_sha256'], 'source': manifest['source'], 'safety_copy': str(safety),
              'config': result, 'env_added': added, 'blocked_folders': len(blocked['blocked']), 'databases': manifest['databases']}
    (target / 'odysseus-migration.json').write_text(json.dumps(record, indent=1), encoding='utf-8')
    return record


def verify(bundle_dir, target_home):
    """Compare the target with what the bundle promised. Read-only."""
    manifest, target = read_manifest(bundle_dir), Path(target_home)
    checks = {}
    for name, counts in manifest['databases'].items():
        if counts and (target / name).is_file():
            now = db_counts(target / name)
            checks[name] = {'expected': counts, 'found': now, 'ok': bool(now) and now['sessions'] >= counts['sessions'] and now['messages'] >= counts['messages']}
        elif counts:
            checks[name] = {'expected': counts, 'found': None, 'ok': False}
    missing = [rel for rel, digest in manifest['entry_sha256'].items() if rel not in DB_FILES and not (target / rel).is_file()]
    changed = [rel for rel, digest in manifest['entry_sha256'].items() if rel not in DB_FILES and (target / rel).is_file() and _sha256(target / rel) != digest]
    checks['files'] = {'expected': len(manifest['entry_sha256']), 'missing': missing[:20], 'changed': changed[:20], 'ok': not missing and not changed}
    checks['no_credential_files_in_bundle'] = {'ok': not any(_credential_file(Path(rel).name) for rel in manifest['entry_sha256'])}
    return {'ok': all(item.get('ok') for item in checks.values()), 'checks': checks}


def rollback(target_home, safety_copy):
    """Put the target back as it was before the migration. The migrated home is moved aside (never deleted)."""
    target, safety = Path(target_home), Path(safety_copy)
    if not safety.is_file() or _live_gateway(target):
        raise MigrationError('Nothing was rolled back: the safety copy is missing or a gateway is running on the home.')
    aside = target.with_name(target.name + '.migrated-' + time.strftime('%Y%m%d-%H%M%S'))
    os.replace(target, aside)
    try:
        target.mkdir()
        with zipfile.ZipFile(safety) as zf:
            for member in zf.namelist():
                destination = (target / member).resolve()
                if target.resolve() not in destination.parents and destination != target.resolve():
                    raise MigrationError('The safety copy contains an unsafe path.')
            zf.extractall(target)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        os.replace(aside, target)
        raise
    return str(aside)
