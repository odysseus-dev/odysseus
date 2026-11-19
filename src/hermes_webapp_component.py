"""Optional official Hermes Gateway + Webapp component: pinned source, the official Webapp build, ownership-aware lifecycle.

Separate from src/hermes_component.py (the WebUI): its own config file (<root>/managed/webapp/component.json), its own lock and its own
release folders. Nothing here reads or writes the WebUI component or its state, so the existing install and its rollback are unaffected.

Ownership
  managed  Odysseus provisioned it: a pinned clone, a runtime, the built Webapp bundle and a gateway home it created. Odysseus may start,
           stop, upgrade and roll it back, and only ever touches what it created.
  adopted  The user's existing Hermes. Read-only: the checkout, the runtime and the home are verified and recorded, never written. The
           bundle (optional) is built from a pinned `git archive` export into a release folder and served with HERMES_WEBAPP_DIST, the
           switch upstream documents for a prebuilt bundle, so the user's checkout is not touched. Odysseus never starts, stops or edits it.

Sources are pinned to one immutable full commit (config/hermes-component.lock.json, key "webapp"). Node and pnpm minimums come from the
pinned checkout's own package.json "engines", so the official build's requirements are not duplicated here.
"""
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from src.hermes_component import (ComponentError, SourcePin, atomic_bytes, atomic_json, checkout_source, component_lock, patch_state,
                                  private_directory, probe_python, run_install, safe_directory, verify_checkout)

ORIGINS = ('managed', 'adopted')
PLUGIN_NAME = 'odysseus-return'
RESUME_PLUGIN_NAME = 'odysseus-resume'
USAGE_PLUGIN_NAME = 'odysseus-usage'
KNOWN_PREVIOUS_RESUME_SHA256 = (            # earlier shipped copies of the resume plugin that may be upgraded in place (first deployment: CRLF and LF forms)
    '71c3a1ff23b12020c0de2f3d5fa097f67d61d4f0a1f73a35e0a864b46b5727e4',
    '596d5f44b76d532148bcf9bc536852d7f37f0225099658752a04e20f8e8814b0',
    # the copy deployed on 2026-11-13 (Projects sidebar entry, commit d918daf3): CRLF and LF forms
    'db774b38fd7da2b507907fe5d7d9709604df9f471dc323042b3887e9a5031f50',
    '9e5c02a49f3c4d09a7646073201ce778c9bfd53bb59cf45792364fb444d595ea',
    # the copy deployed on 2026-11-14 with release f36711161129 (commit 66152ea4): CRLF and LF forms
    'f32302a7efa828392c91c12914b6a1ea66c2ed5d5d0f6b1d53226b66516c984a',
    'be98c959607c10aed099e46b602704633e79e663612e95896e3fa3d520c47234',
    # the copy deployed on 2026-11-14 with release b1e8abf79b78 (commit c4f0a95d, composer control center): CRLF and LF forms
    '0c439ab6ab31f1475ba975d47ca06a1de49030e46c3a4d18eae46c6d667619c7',
    '6909ac35e3165d149049e7cf5e039d92c410c93d79d7d5ede00fda65c2e77ede',
)
WORKSPACE_GRANTS_PLUGIN = 'odysseus-workspace-grants'
WORKSPACE_GRANTS_PLUGIN_SOURCE = 'integrations/hermes-mode/plugins/odysseus-workspace-grants'
KNOWN_PREVIOUS_PLUGIN_SHA256 = ('f72f51dabc67c4c99f40aca182ac66546220c678e5341020c95c3e73536d1760',
                                'd87945810fe157502ff0dc498e03d59dea737f469a7f571716a7955bfff1b0a5')       # earlier shipped copies of OUR plugin: safe to upgrade; anything else is the user's and is never overwritten
WIKI_CONTEXT_PLUGIN = 'odysseus-wiki-context'
WIKI_CONTEXT_PLUGIN_SOURCE = 'integrations/wiki/plugins/odysseus-wiki-context'
WIKI_CONTEXT_CONFIG = 'wiki-context.json'
SECURITY_PLUGIN = 'hermes-security'
SECURITY_PLUGIN_PATCH = 'integrations/hermes-mode/patches/hermes-security-skill-descriptions.patch'
BUILD_SCRIPT = 'apps/desktop/scripts/build-webapp.mjs'
BUNDLE_RELATIVE = 'apps/desktop/dist-webapp'
ROUTE_GUARD_PATCH = 'integrations/hermes-mode/patches/webapp-route-guard.patch'
MCP_RECOVERY_PATCH = 'integrations/hermes-mode/patches/webapp-mcp-recovery.patch'
DESKTOP_WORKSPACE_PATCH = 'integrations/hermes-mode/patches/webapp-desktop-workspace.patch'
COMPOSER_PATCH = 'integrations/hermes-mode/patches/webapp-composer-control-center.patch'
RESUME_OUTCOMES_PATCH = 'integrations/hermes-mode/patches/webapp-resume-outcomes.patch'
UPDATE_PROTECTION_PATCH = 'integrations/hermes-mode/patches/webapp-update-protection.patch'
USAGE_CONTEXT_PATCH = 'integrations/hermes-mode/patches/webapp-usage-context.patch'
TRANSCRIPT_CLARITY_PATCH = 'integrations/hermes-mode/patches/webapp-transcript-clarity.patch'
MANAGED_MARKER = '.odysseus-managed.json'
# What importing each runtime extra must make possible, proven against the committed environment before a release is 'ready'.
RUNTIME_EXTRA_IMPORTS = {'mcp': 'mcp.client.stdio'}
_EXTRA_NAME = re.compile(r'[a-z0-9][a-z0-9_-]*')
PRESERVED_SETTINGS = ('write_roots', 'logins')
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_RELEASE = re.compile(r'[0-9a-f]{12}')

# Logins the user's own tools keep OUTSIDE the Hermes home. The gateway runs as the user, so unless these point somewhere else it finds and may use
# them. 'isolated' (the default) points each at an empty folder; 'allowed' leaves them alone, which is a deliberate choice recorded in the config.
LOGIN_TOOLS = {'claude': 'CLAUDE_CONFIG_DIR', 'codex': 'CODEX_HOME'}
FAILED_RELEASES_KEPT = 2
LOGIN_WARNING = (
    'Isolation only stops Hermes\'s own provider code from finding these logins; it is not a security boundary. Commands the agent runs, and any other '
    'program running as you, can read ~/.claude/.credentials.json and ~/.codex/auth.json either way. When a login is allowed, the gateway may also refresh '
    'the Claude OAuth token and rewrite ~/.claude/.credentials.json (the old Hermes already does this); the gateway keeps no copy anywhere else.')
_SECRET_NAME = re.compile(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL', re.I)
# What the gateway process is given. Odysseus' own environment (provider keys, session secrets) is NOT passed on: the agent's shell inherits this.
_GATEWAY_ENV = ('SystemRoot', 'SYSTEMROOT', 'SystemDrive', 'windir', 'ComSpec', 'PATHEXT', 'PATH', 'OS', 'USERNAME', 'USERDOMAIN', 'COMPUTERNAME',
                'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'ProgramData', 'ProgramFiles', 'ProgramFiles(x86)',
                'ProgramW6432', 'CommonProgramFiles', 'CommonProgramFiles(x86)', 'CommonProgramW6432', 'PROCESSOR_ARCHITECTURE',
                'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL', 'TZ')


# ── pin ──────────────────────────────────────────────────────────────────────────────────────
def load_webapp_pin(application_root):
    """The pinned official source for the gateway and the Webapp: one approved repository, one immutable full commit."""
    try:
        data = json.loads((Path(application_root) / 'config/hermes-component.lock.json').read_text(encoding='utf-8'))
        entry = data['webapp']
        return SourcePin('agent', entry['repository'], entry['revision'])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ComponentError('The Hermes Webapp pin is missing or invalid.') from exc


def load_runtime_extras(application_root):
    """The Hermes extras every managed release must carry (the lock's ``runtime.extras``). A release built without them silently ran without
    the MCP SDK; naming them in the lock makes each new release install and prove them."""
    try:
        data = json.loads((Path(application_root) / 'config/hermes-component.lock.json').read_text(encoding='utf-8'))
        extras = data['runtime']['extras']
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ComponentError('The Hermes runtime extras are missing from the component lock.') from exc
    if not isinstance(extras, list) or not all(isinstance(item, str) and _EXTRA_NAME.fullmatch(item) for item in extras):
        raise ComponentError('The Hermes runtime extras in the component lock are invalid.')
    return tuple(extras)


def loopback_port(url):
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not port or url != f'http://127.0.0.1:{port}/':
        raise ComponentError('The Webapp URL must be exactly http://127.0.0.1:PORT/.')
    return port


# ── configuration file ────────────────────────────────────────────────────────────────────
def read_webapp_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError('oversized')
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or data.get('kind') != 'webapp'
                or type(data.get('enabled')) is not bool or data.get('origin') not in ORIGINS):
            raise ValueError('schema')
        for key in ('url', 'home', 'python', 'agent'):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(key)
        loopback_port(data['url'])
        for key in ('release', 'previous'):
            if data.get(key) is not None and not (isinstance(data[key], str) and _RELEASE.fullmatch(data[key])):
                raise ValueError(key)
        if data.get('bundle') is not None and not isinstance(data['bundle'], str):
            raise ValueError('bundle')
        return data
    except (OSError, ValueError, RecursionError, ComponentError) as exc:
        raise ComponentError('Webapp component configuration is unreadable or unsupported; it was not replaced.') from exc


# ── helpers ───────────────────────────────────────────────────────────────────────────────
def _remove_release_tree(path, base):
    """Delete one release folder, and only a real folder directly inside ``base`` (git leaves read-only files that Windows will not delete)."""
    path, base = Path(path), Path(base)
    if path.is_symlink() or os.path.realpath(path.parent) != os.path.realpath(base) or not _RELEASE.fullmatch(path.name):
        raise ComponentError('Refusing to delete a folder that is not a release.')

    def writable(function, target, _error):
        os.chmod(target, 0o700)
        function(target)

    shutil.rmtree(path, onexc=writable) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=writable)


@contextmanager
def read_only_git():
    """`git status` normally refreshes the index; against a checkout we only inspect, that is still a write."""
    previous = os.environ.get('GIT_OPTIONAL_LOCKS')
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GIT_OPTIONAL_LOCKS', None)
        else:
            os.environ['GIT_OPTIONAL_LOCKS'] = previous


@dataclass(frozen=True)
class Tool:
    name: str
    executable: str
    directory: str
    version: str


def _version_key(text):
    return tuple(int(part) for part in re.findall(r'\d+', text)[:4])


def find_tool(name, minimum_major, environment):
    """The newest Node or pnpm at or above ``minimum_major``. ODYSSEUS_HERMES_<NAME>_DIR is exclusive when set; otherwise the Hermes
    tools folder (%LOCALAPPDATA%\\hermes\\tools) is searched newest first, then PATH. None when nothing qualifies."""
    explicit = environment.get(f'ODYSSEUS_HERMES_{name.upper()}_DIR')
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    else:
        local = environment.get('LOCALAPPDATA')
        tools = Path(local) / 'hermes' / 'tools' if local else None
        if tools and tools.is_dir():
            found = [item for item in tools.glob(name + '-*') if item.is_dir()]
            candidates += sorted(found, key=lambda item: _version_key(item.name), reverse=True)
        on_path = shutil.which(name, path=environment.get('PATH'))
        if on_path:
            candidates.append(Path(on_path).parent)
    for directory in candidates:
        executable = shutil.which(name, path=str(directory))
        if not executable:
            continue
        try:
            result = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
                                    env={k: v for k, v in environment.items() if not _SECRET_NAME.search(k)})
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r'\d+\.\d+\.\d+', result.stdout)
        if result.returncode == 0 and match and int(match.group(0).split('.')[0]) >= minimum_major:
            return Tool(name, executable, str(directory), match.group(0))
    return None


def verify_managed_marker(source):
    """A release is only built when the update-protection patch really left its managed-install marker in the tree: without it the
    pinned updater would treat the release as an ordinary git checkout. The marker is a tracked file of that patch, so it is recreated
    for every release; this proves it, rather than trusting it."""
    try:
        data = json.loads((Path(source) / MANAGED_MARKER).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ComponentError('The managed-install marker is missing from the new release, so direct updates would not be refused; it was not built.') from exc
    if not isinstance(data, dict) or data.get('managed_by') != 'odysseus':
        raise ComponentError('The managed-install marker in the new release is invalid; it was not built.')


def engine_majors(source):
    """Minimum Node and pnpm major versions the pinned checkout itself declares (package.json "engines")."""
    try:
        engines = json.loads((Path(source) / 'package.json').read_text(encoding='utf-8'))['engines']
        majors = {name: int(re.search(r'\d+', str(engines[name])).group(0)) for name in ('node', 'pnpm')}
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ComponentError('The pinned Hermes source does not declare its Node and pnpm requirements.') from exc
    return majors


def build_environment(base, tools, directory):
    """Environment for the official Webapp build: toolchain first on PATH, caches and temp inside the component, no Hermes/Odysseus
    variables and nothing credential-shaped."""
    env = {key: value for key, value in base.items()
           if not key.upper().startswith(('HERMES_', 'ODYSSEUS_', 'PYTHON', 'NODE_', 'NPM_', 'PNPM_')) and not _SECRET_NAME.search(key)}
    cache, temp = Path(directory) / 'cache', Path(directory) / 'tmp'
    for folder in (cache, temp):
        folder.mkdir(parents=True, exist_ok=True)
    env['PATH'] = os.pathsep.join([tool.directory for tool in tools] + [env.get('PATH', '')])
    env.update({'pnpm_config_store_dir': str(cache / 'pnpm-store'), 'npm_config_cache': str(cache / 'npm'),
                'TEMP': str(temp), 'TMP': str(temp), 'CI': '1'})
    return env


def inspect_bundle(bundle):
    """Whether ``bundle`` is a complete, unmarked official Webapp bundle with a build receipt."""
    bundle, problems, receipt_sha = Path(bundle), [], None
    if not (bundle / 'index.html').is_file():
        problems.append('index.html is missing')
    try:
        raw = (bundle / 'hermes-build.json').read_bytes()
        data = json.loads(raw)
        receipt_sha = hashlib.sha256(raw).hexdigest()
        if not isinstance(data, dict) or data.get('schema') != 1 or data.get('product') != 'webapp':
            problems.append('the build receipt is not a Webapp receipt')
    except (OSError, ValueError):
        problems.append('the build receipt is missing or unreadable')
    if (bundle / '.hermes-stale').exists():
        problems.append('the bundle is marked stale')
    return {'ok': not problems, 'problems': problems, 'receipt_sha256': receipt_sha}


def export_source(checkout, pin, target, environment):
    """A clean copy of the pinned revision from ``checkout`` (read-only on the checkout), extracted with tar's `data` filter."""
    git = shutil.which('git')
    if not git:
        raise ComponentError('Git for Windows is required to export the pinned Hermes source.')
    target = Path(target)
    if target.exists():
        raise ComponentError('A source destination already exists; it will not be replaced.')
    if not hasattr(tarfile, 'data_filter'):
        raise ComponentError('This Python cannot extract archives safely; use Python 3.12 or newer.')
    archive = target.with_name(target.name + '.tar')
    env = {**environment, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    try:
        with archive.open('wb') as out:
            result = subprocess.run([git, '-C', str(checkout), 'archive', '--format=tar', pin.revision], stdout=out, stderr=subprocess.PIPE,
                                    timeout=900, env=env, creationflags=_NO_WINDOW)
        if result.returncode:
            raise ComponentError('The pinned Hermes revision could not be exported from the existing checkout.')
        target.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            tar.extractall(target, filter='data')
    except (OSError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        raise ComponentError('The pinned Hermes source could not be exported.') from exc
    finally:
        archive.unlink(missing_ok=True)


_READ_SETTINGS = r'''
import json, sys
from pathlib import Path
from ruamel.yaml import YAML
p = Path(sys.argv[1]) / "config.yaml"
d = (YAML(typ="safe").load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}
w = d.get("webapp") if isinstance(d.get("webapp"), dict) else {}
dk = d.get("desktop") if isinstance(d.get("desktop"), dict) else {}
ap = d.get("approvals") if isinstance(d.get("approvals"), dict) else {}
print(json.dumps({"config_present": p.is_file(), "webapp": {k: w.get(k) for k in ("enabled", "host", "port", "public_url")},
  "approvals_mode": ap.get("mode"), "repo_scan_enabled": dk.get("repo_scan_enabled"), "repo_scan_roots": len(dk.get("repo_scan_roots") or []),
  "sign_in_gate_configured": bool((d.get("dashboard") or {}).get("basic_auth")) if isinstance(d.get("dashboard"), dict) else False,
  "plugins_enabled": [x for x in ((d.get("plugins") or {}).get("enabled") or []) if isinstance(x, str)] if isinstance(d.get("plugins"), dict) else []}))
'''

_WRITE_SETTINGS = r'''
import json, os, sys
from ruamel.yaml import YAML
path, payload = sys.argv[1], json.loads(sys.argv[2])
y = YAML(); y.preserve_quotes = True
with open(path, encoding="utf-8") as f:
    data = y.load(f) or {}
for section, values in payload.items():
    if data.get(section) is None:
        data[section] = {}
    for k, v in values.items():
        data[section][k] = v
tmp = path + ".odysseus.tmp"
with open(tmp, "w", encoding="utf-8", newline="") as f:
    y.dump(data, f)
os.replace(tmp, path)
'''


def _yaml_scalar(value):
    return json.dumps(value) if isinstance(value, (str, list)) else ('true' if value is True else 'false' if value is False else str(value))


# ── component ─────────────────────────────────────────────────────────────────────────────
class WebappComponent:
    def __init__(self, root, application_root=None, environment=None):
        self.root = safe_directory(root)
        self.application_root = Path(application_root).resolve() if application_root else Path(__file__).resolve().parents[1]
        if application_root and self.root.is_relative_to(self.application_root):
            raise ComponentError('Persistent Hermes component state must be outside the Odysseus source checkout.')
        self.environment = dict(os.environ if environment is None else environment)
        self.directory = self.root / 'managed' / 'webapp'
        self.config_path = self.directory / 'component.json'
        self.last_actions = []                                  # what the last ensure/repair did besides returning the config
        self._guard_lock = threading.Lock()                     # single-flight for the (git-heavy) patch-state check shown in status()
        self._guard_cache = None                                # (key, monotonic time, state)

    # -- reading ---------------------------------------------------------------------------
    def config(self):
        return read_webapp_config(self.config_path)

    def _release_dir(self, release):
        return self.directory / 'releases' / release

    def _hermes_yaml(self, python, code, *args):
        try:
            result = subprocess.run([str(python), '-I', '-B', '-c', code, *args], capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
            if result.returncode:
                raise ValueError('yaml helper')
            return result.stdout.strip()
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise ComponentError('The Hermes interpreter could not read or write its configuration.') from exc

    def home_settings(self, python, home):
        """What the home's config.yaml says about the Webapp. Values only for non-secret settings; the sign-in secret is reported as present or not."""
        try:
            return json.loads(self._hermes_yaml(python, _READ_SETTINGS, str(home)))
        except ValueError as exc:
            raise ComponentError('The Hermes interpreter returned an unreadable configuration summary.') from exc

    # -- plan (read-only) -----------------------------------------------------------------
    def plan(self, agent=None, home=None, python=None, url=None):
        """Everything `ensure` would rely on or do, with no writes anywhere. Safe against a live, user-owned Hermes."""
        pin, steps, report = load_webapp_pin(self.application_root), [], {'writes_performed': False}
        report['pin'] = {'repository': pin.repository, 'revision': pin.revision}
        local = self.environment.get('LOCALAPPDATA')
        default_home = Path(local) / 'hermes' if local else Path.home() / '.hermes'
        home = Path(home or self.environment.get('HERMES_HOME') or default_home)
        agent = Path(agent or self.environment.get('HERMES_WEBUI_AGENT_DIR') or home / 'hermes-agent')
        current = None
        try:
            current = self.config()
        except ComponentError as exc:
            report['current'] = {'error': str(exc)}
        if current:
            report['current'] = {key: current.get(key) for key in ('origin', 'enabled', 'release', 'previous', 'url', 'home', 'agent')}
        found = {'agent': str(agent), 'home': str(home), 'home_exists': home.is_dir()}
        with read_only_git():
            try:
                verify_checkout(agent, pin)
                found['checkout'] = 'at-pin'
            except ComponentError:
                found['checkout'] = 'differs-or-missing'
        majors = None
        if (agent / 'package.json').is_file():
            try:
                majors = engine_majors(agent)
            except ComponentError:
                majors = None
        report['engines'] = majors
        tools = {}
        for name in ('node', 'pnpm'):
            minimum = (majors or {}).get(name)
            tool = find_tool(name, minimum or 0, self.environment) if minimum else None
            tools[name] = {'version': tool.version, 'directory': tool.directory} if tool else None
        report['tools'] = tools
        runtime = python or self.environment.get('HERMES_WEBUI_PYTHON')
        if runtime:
            try:
                probe_python(runtime, require_agent=True)
                found['python'] = 'ok'
            except ComponentError as exc:
                found['python'] = str(exc)
            if found['python'] == 'ok' and home.is_dir():
                try:
                    found['settings'] = self.home_settings(runtime, home)
                except ComponentError as exc:
                    found['settings_error'] = str(exc)
        else:
            found['python'] = 'not specified'
        found['desktop_plugin'] = (home / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js').is_file()
        report['adopted'] = found
        if found['checkout'] != 'at-pin':
            steps.append('The existing checkout is not at the pinned revision: it will not be modified; a managed install would clone the pin instead.')
        if not all(tools.values()):
            steps.append('Node and pnpm at the versions the pinned source requires were not found: set ODYSSEUS_HERMES_NODE_DIR / _PNPM_DIR or install them in the Hermes tools folder.')
        settings = found.get('settings') or {}
        if settings and not (settings.get('webapp') or {}).get('enabled'):
            steps.append('config.yaml does not enable webapp (a user-owned file: Odysseus will not edit it; enable it yourself).')
        if settings and settings.get('repo_scan_enabled') in (None, True) and not settings.get('repo_scan_roots'):
            steps.append('Repository discovery is on with no roots, so it scans the home folder; set desktop.repo_scan_roots or disable it.')
        if settings and settings.get('approvals_mode') != 'manual':
            steps.append(f'approvals.mode is {settings.get("approvals_mode")!r}, not manual (a user-owned decision).')
        if not found['desktop_plugin']:
            steps.append(f'The {PLUGIN_NAME} desktop plugin is not installed in this home (Odysseus will not install it into a user-owned home).')
        report['steps'] = steps
        return report

    # -- ensure ---------------------------------------------------------------------------
    def ensure(self, origin='managed', *, agent=None, home=None, python=None, port=None, public_url='', repo_roots=None, build=True,
               repair=False, enable=False, rebuild=False):
        """Install, or bring an existing install back to health.

        ensure   first install; on an existing install it verifies and heals the cheap parts (gateway home and config, return plugin,
                 leftover releases). It never rebuilds.
        repair   the same, and also moves the gateway to another home or port when asked. It rebuilds ONLY if the release no longer
                 verifies, or with rebuild=True (about 12 minutes).
        """
        if origin not in ORIGINS:
            raise ComponentError('origin must be managed or adopted.')
        pin = load_webapp_pin(self.application_root)
        self.last_actions = []
        with component_lock(self.directory):
            config = self.config()
            if config and config['origin'] != origin:
                raise ComponentError(f'The Webapp component is {config["origin"]}; Odysseus will not switch its ownership.')
            if config and not config['enabled'] and not enable:
                return config
            if config and not (repair and rebuild):
                if repair:
                    try:
                        self.validate(config)
                    except ComponentError as exc:
                        self.last_actions.append(f'the release no longer verifies, rebuilding: {exc}')
                        config = self._rebuild(pin, origin, config, agent, home, python, port, public_url, repo_roots, build)
                        return config
                else:
                    self.validate(config)
                if enable and not config['enabled']:
                    config = {**config, 'enabled': True}
                    atomic_json(self.config_path, config)
                if config['origin'] == 'managed':
                    config = self._heal(config, home=home, port=port, public_url=public_url, allow_move=repair)
                if repair:
                    self.last_actions.append('release verified; nothing was rebuilt')
                return config
            return self._rebuild(pin, origin, config, agent, home, python, port, public_url, repo_roots, build)

    def _rebuild(self, pin, origin, config, agent, home, python, port, public_url, repo_roots, build, stage=False):
        if origin == 'adopted':
            candidate = self._ensure_adopted(pin, config, agent, home, python, port, build)
        else:
            candidate = self._ensure_managed(pin, config, home, python, port, public_url, repo_roots, stage)
        for key in PRESERVED_SETTINGS:                              # settings the user chose must survive a new release
            if config and key in config and key not in candidate:
                candidate[key] = config[key]
        if stage:
            atomic_json(self._release_dir(candidate['release']) / 'candidate.json', candidate)
            return candidate
        atomic_json(self.config_path, candidate)
        self._sweep_releases(candidate)
        return candidate

    def stage_release(self, *, python=None, port=None, public_url='', repo_roots=None):
        """Build the next managed release side by side, WITHOUT switching to it: nothing the running gateway uses, and no home or configuration
        file, is touched. The release is 'staged' (kept by the sweep) until ``activate_release`` makes it current; a restart then serves it."""
        pin = load_webapp_pin(self.application_root)
        self.last_actions = []
        with component_lock(self.directory):
            config = self.config()
            if not config or config['origin'] != 'managed':
                raise ComponentError('Only a managed Webapp installation can stage a release.')
            return self._rebuild(pin, 'managed', config, None, None, python, port, public_url, repo_roots, True, stage=True)

    def activate_release(self, release):
        """Make a staged release current (the current one becomes ``previous``, so ``rollback`` returns to it). Does not restart the gateway."""
        if not _RELEASE.fullmatch(str(release)):
            raise ComponentError('A release identifier is twelve hexadecimal characters.')
        with component_lock(self.directory):
            config = self._require_managed()
            release_dir = self._release_dir(release)
            try:
                record = json.loads((release_dir / 'release.json').read_text(encoding='utf-8'))
                candidate = json.loads((release_dir / 'candidate.json').read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ComponentError('The staged release is missing or unreadable; nothing was changed.') from exc
            if record.get('state') != 'staged' or candidate.get('release') != release:
                raise ComponentError('Only a complete staged release can be activated; nothing was changed.')
            candidate['previous'] = config.get('release')
            for key in PRESERVED_SETTINGS:
                if key in config and key not in candidate:
                    candidate[key] = config[key]
            self.validate(candidate)
            source, home_path = Path(candidate['agent']), Path(candidate['home'])
            self.check_desktop_plugin(home_path)
            self._prepare_home(candidate['python'], home_path, loopback_port(candidate['url']), candidate.get('public_url') or '', None,
                               self.config_schema_version(source))
            self.install_desktop_plugin(home_path)
            candidate['desktop_plugins'] = self.installed_desktop_plugins(home_path)
            self._write_release(release_dir, 'ready', revision=record['revision'], origin='managed', receipt_sha256=record.get('receipt_sha256'),
                                tools=record.get('tools'))
            atomic_json(self.config_path, candidate)
            self.last_actions = []
            self._sweep_releases(candidate)
            return candidate

    def _heal(self, config, *, home=None, port=None, public_url=None, allow_move=False):
        """Managed only, never builds. Recreates a missing gateway home or its config, applies a requested move to another home or port,
        makes sure the return plugin is current, and removes releases that are not part of this component's history."""
        current_port = loopback_port(config['url'])
        new_port = int(port) if port else current_port
        same = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        moved = bool(home) and not same(home, config['home'])
        if public_url and public_url != config.get('public_url', ''):
            raise ComponentError('Changing webapp.public_url needs a rebuild: run repair --rebuild with --public-url.')
        if (moved or new_port != current_port) and not allow_move:             # ensure is idempotent: its arguments only matter for a first install
            self.last_actions.append(f'ignored the given home or port (the saved ones are {config["home"]} and {current_port}); use repair to move them')
            moved, new_port = False, current_port
        home_path = private_directory(Path(home) if moved else Path(config['home']))
        existed = (home_path / 'config.yaml').is_file()
        if not existed or moved or new_port != current_port:
            self.check_desktop_plugin(home_path)
            self._prepare_home(config['python'], home_path, new_port, config.get('public_url', ''), [], self.config_schema_version(Path(config['agent'])))
            self.last_actions.append('gateway home recreated' if not existed else 'gateway home settings updated')
        self.install_desktop_plugin(home_path)
        self.install_workspace_grants_plugin(home_path, config['python'])
        updated = dict(config)
        plugins = self.installed_desktop_plugins(home_path)
        if plugins != config.get('desktop_plugins'):
            updated['desktop_plugins'] = plugins
        if moved:
            updated['home'] = str(home_path)
        if new_port != current_port:
            updated['url'] = f'http://127.0.0.1:{new_port}/'
        if updated != config:
            atomic_json(self.config_path, updated)
        self._sweep_releases(updated)
        return updated

    def _sweep_releases(self, config):
        """Under the component lock, so no build is running: remove releases that were never finished (a killed build) or are no longer the
        current or previous one, and keep only the newest few failed ones for their logs."""
        base = self.directory / 'releases'
        if not base.is_dir():
            return
        keep = {config.get('release'), config.get('previous'), self.running_release(config)}   # a live gateway still serves its release's source and bundle
        failed = []
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.is_symlink() or not _RELEASE.fullmatch(entry.name) or entry.name in keep:
                continue
            try:
                state = json.loads((entry / 'release.json').read_text(encoding='utf-8')).get('state')
            except (OSError, ValueError):
                state = 'unknown'
            if state == 'staged':                  # a finished release waiting for activation is not a leftover
                continue
            if state == 'failed':
                failed.append(entry)
                continue
            try:
                _remove_release_tree(entry, base)
            except OSError as exc:                       # a file in use: the pointer is already switched, so leave the rest for the next sweep
                self.last_actions.append(f'release {entry.name} left in place: {exc}')
                continue
            self.last_actions.append(f'removed release {entry.name} ({state})')
        for entry in sorted(failed, key=lambda item: item.stat().st_mtime)[:-FAILED_RELEASES_KEPT]:
            _remove_release_tree(entry, base)
            self.last_actions.append(f'removed old failed release {entry.name}')

    def _tools(self, source):
        majors = engine_majors(source)
        tools = [find_tool(name, majors[name], self.environment) for name in ('node', 'pnpm')]
        if not all(tools):
            raise ComponentError(f'Node >= {majors["node"]} and pnpm >= {majors["pnpm"]} (what the pinned Hermes source requires) were not found; '
                                 'set ODYSSEUS_HERMES_NODE_DIR / ODYSSEUS_HERMES_PNPM_DIR.')
        return tools

    def _build(self, source, release_dir, log):
        tools = self._tools(source)
        env = build_environment(self.environment, tools, self.directory)
        run_install([tools[0].executable, str(Path(source) / BUILD_SCRIPT)], log, env, cwd=source)
        bundle = Path(source) / BUNDLE_RELATIVE
        verdict = inspect_bundle(bundle)
        if not verdict['ok']:
            raise ComponentError('The Webapp build did not produce a complete bundle: ' + '; '.join(verdict['problems']))
        return bundle, verdict, {tool.name: tool.version for tool in tools}

    def _write_release(self, release_dir, state, **extra):
        atomic_json(release_dir / 'release.json', {'state': state, **extra})

    def _ensure_adopted(self, pin, previous, agent, home, python, port, build):
        if not (agent and home and python and port):
            raise ComponentError('An adopted Webapp needs --agent, --home, --python and --port (the existing Hermes installation).')
        agent, home = safe_directory(agent), safe_directory(home)
        with read_only_git():
            verify_checkout(agent, pin)
        if not (agent / 'gateway' / 'run.py').is_file() or not home.is_dir():
            raise ComponentError('The existing Hermes layout is incomplete; it was not modified.')
        probe_python(python, require_agent=True)
        release = bundle = verdict = tool_versions = None
        if build:
            release = uuid.uuid4().hex[:12]
            release_dir = private_directory(self._release_dir(release))
            log = release_dir / 'build.log'
            self._write_release(release_dir, 'preparing', revision=pin.revision, origin='adopted')
            export_source(agent, pin, release_dir / 'src', self.environment)
            bundle, verdict, tool_versions = self._build(release_dir / 'src', release_dir, log)
            self._write_release(release_dir, 'ready', revision=pin.revision, origin='adopted', receipt_sha256=verdict['receipt_sha256'], tools=tool_versions)
        return {'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'adopted', 'release': release,
                'previous': previous.get('release') if previous else None, 'agent': str(agent), 'python': str(python), 'home': str(home),
                'bundle': str(bundle) if bundle else None, 'url': f'http://127.0.0.1:{int(port)}/', 'public_url': '',
                'revisions': {'webapp': pin.revision}, 'build': {'receipt_sha256': verdict['receipt_sha256'], 'tools': tool_versions} if verdict else None}

    def _ensure_managed(self, pin, previous, home, python, port, public_url, repo_roots, stage=False):
        port = int(port or (loopback_port(previous['url']) if previous else 0))
        if not port:
            raise ComponentError('A managed Webapp needs --port.')
        release = uuid.uuid4().hex[:12]
        release_dir = private_directory(self._release_dir(release))
        self._write_release(release_dir, 'preparing', revision=pin.revision, origin='managed')
        try:
            return self._provision_managed(pin, previous, release, release_dir, home, python, port, public_url, repo_roots, stage)
        except BaseException:
            self._write_release(release_dir, 'failed', revision=pin.revision, origin='managed')      # kept for its logs; never referenced
            raise

    def _provision_managed(self, pin, previous, release, release_dir, home, python, port, public_url, repo_roots, stage=False):
        log = release_dir / 'install.log'
        env = {**self.environment, 'GIT_TERMINAL_PROMPT': '0'}
        source = release_dir / 'src'
        checkout_source(source, pin, log, env, self.webapp_patches())
        verify_managed_marker(source)
        bundle, verdict, tool_versions = self._build(source, release_dir, log)
        extras = load_runtime_extras(self.application_root)
        home_path = private_directory(home or (previous['home'] if previous else self.root / 'state/hermes-gateway'))
        if python:
            probe_python(python, require_agent=True)
            runtime, runtime_origin = str(python), 'external'
            # The gateway activates this release's own committed environment over `python`: install the extras into THAT one, and prove it.
            self._install_runtime_extras(source, runtime, home_path, extras, log, env)
        else:
            seed = self.environment.get('HERMES_WEBUI_PYTHON') or sys.executable
            probe_python(seed)
            run_install([seed, '-B', '-m', 'pm.build_env', '--source', str(source), '--out', str(release_dir / 'venv'),
                         '--cache', str(self.directory / 'cache/pm'), *[arg for extra in extras for arg in ('--extra', extra)],
                         *(['--record-selection'] if extras else [])], log, {**env, 'HERMES_RUNTIME_DIR': str(self.directory / 'runtime')}, cwd=source)
            runtime = str(release_dir / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python'))
            probe_python(runtime, require_agent=True)
            runtime_origin = 'managed'
            self._verify_runtime_extras(runtime, extras)
        self.check_desktop_plugin(home_path)                 # every conflict is found before anything in the home is written
        schema = self.config_schema_version(source)
        if public_url:
            # Upstream refuses to bind when webapp.public_url is set and no auth provider exists, and Odysseus will not invent credentials.
            existing = self.home_settings(runtime, home_path) if (home_path / 'config.yaml').is_file() else {}
            if not existing.get('sign_in_gate_configured'):
                raise ComponentError('webapp.public_url engages the gateway sign-in gate, which needs an auth provider the user configures '
                                     '(dashboard.basic_auth); none is configured in this home, so no public URL was set.')
        if not stage:
            self._prepare_home(runtime, home_path, port, public_url or '', repo_roots, schema)
            self.install_desktop_plugin(home_path)
        # A staged release is complete but not current: it stays 'staged' (the sweep keeps it) until ``activate_release``.
        self._write_release(release_dir, 'staged' if stage else 'ready', revision=pin.revision, origin='managed',
                            receipt_sha256=verdict['receipt_sha256'], tools=tool_versions)
        return {'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'managed', 'release': release,
                'previous': previous.get('release') if previous else None, 'agent': str(source), 'python': runtime, 'python_origin': runtime_origin,
                'home': str(home_path), 'bundle': str(bundle), 'url': f'http://127.0.0.1:{port}/', 'public_url': public_url or '',
                'revisions': {'webapp': pin.revision}, 'build': {'receipt_sha256': verdict['receipt_sha256'], 'tools': tool_versions},
                'desktop_plugins': self.installed_desktop_plugins(home_path)}

    def _install_runtime_extras(self, source, python, home, extras, log, env):
        """Install ``extras`` into the environment PM commits for ``source`` under ``home`` (what ``hermes pm install --extra`` does), then prove
        each imports from that environment. Nothing is installed for an empty list. Raises, so the release is never marked ready without them."""
        if not extras:
            return
        code = 'import sys; from pm.install import sync_venv; sync_venv(sys.argv[1].split(","), explicit=True)'
        run_install([str(python), '-B', '-c', code, ','.join(extras)], log, {**env, 'HERMES_HOME': str(home), 'PYTHONPATH': str(source),
                                                                          'HERMES_RUNTIME_DIR': str(self.directory / 'runtime')}, cwd=source)
        locate = 'from pathlib import Path; from pm.environments import committed_venv; print(committed_venv(Path.cwd()) or "")'
        try:
            found = subprocess.run([str(python), '-I', '-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]);' + locate, str(source)], cwd=source,
                                   env={**env, 'HERMES_HOME': str(home)}, capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComponentError('The release environment could not be located after installing the runtime extras.') from exc
        venv = found.stdout.strip().splitlines()[-1] if found.returncode == 0 and found.stdout.strip() else ''
        if not venv:
            raise ComponentError('PM committed no environment for this release; the runtime extras are not installed.')
        self._verify_runtime_extras(str(Path(venv) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')), extras)

    @staticmethod
    def _verify_runtime_extras(python, extras):
        for extra in extras:
            module = RUNTIME_EXTRA_IMPORTS.get(extra)
            if not module:
                continue
            try:
                result = subprocess.run([str(python), '-I', '-B', '-c', f'import {module}'], capture_output=True, text=True, timeout=120,
                                        creationflags=_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ComponentError(f'The {extra} runtime extra could not be verified in the release environment.') from exc
            if result.returncode:
                raise ComponentError(f'The release environment cannot import {module} (runtime extra {extra}); the release was not marked ready.')

    # -- gateway home (managed only) -------------------------------------------------------
    @staticmethod
    def config_schema_version(source):
        """The config schema version the pinned source itself declares; a fresh home without it is treated as ancient and warned about."""
        try:
            text = (Path(source) / 'hermes_cli' / 'config_defaults.py').read_text(encoding='utf-8')
            return int(re.search(r'"_config_version":\s*(\d+)', text).group(1))
        except (OSError, ValueError, AttributeError) as exc:
            raise ComponentError('The pinned Hermes source does not declare its configuration version.') from exc

    def _prepare_home(self, python, home, port, public_url, repo_roots, schema=None):
        """Create or update the Webapp settings of a home Odysseus owns. Only the keys below are set; everything else is kept."""
        settings = {'webapp': {'enabled': True, 'host': '127.0.0.1', 'port': port, 'public_url': public_url}}
        desktop = {'repo_scan_enabled': bool(repo_roots), 'repo_scan_roots': list(repo_roots or [])}
        config = home / 'config.yaml'
        if not config.exists():
            lines = [f'_config_version: {schema}', 'webapp:', '  enabled: true', '  host: 127.0.0.1', f'  port: {port}', f'  public_url: {json.dumps(public_url)}',
                     'approvals:', '  mode: manual', 'desktop:', f'  repo_scan_enabled: {_yaml_scalar(desktop["repo_scan_enabled"])}',
                     f'  repo_scan_roots: {json.dumps(desktop["repo_scan_roots"])}']
            atomic_bytes(config, ('\n'.join(lines) + '\n').encode('utf-8'))
            return
        backup = home / f'config.yaml.pre-odysseus-{time.strftime("%Y%m%d-%H%M%S")}'
        shutil.copy2(config, backup)
        self._hermes_yaml(python, _WRITE_SETTINGS, str(config), json.dumps(settings))

    def _plugin_pairs(self, home):
        """(name, source, target, upgradable hashes) for each Odysseus desktop plugin. The return plugin is required; the resume plugin is
        installed only when this Odysseus ships it, so an older install keeps working unchanged."""
        folder = self.application_root / 'integrations/hermes-mode/desktop-plugins'
        out = []
        for name, known, required in ((PLUGIN_NAME, KNOWN_PREVIOUS_PLUGIN_SHA256, True), (RESUME_PLUGIN_NAME, KNOWN_PREVIOUS_RESUME_SHA256, False), (USAGE_PLUGIN_NAME, (), False)):
            source = folder / name / 'plugin.js'
            if not source.is_file():
                if required:
                    raise ComponentError('The packaged desktop plugin is missing.')
                continue
            out.append((name, source, Path(home) / 'desktop-plugins' / name / 'plugin.js', known))
        return out

    @staticmethod
    def _same_plugin(a, b):
        """Equal apart from line endings: a Windows checkout turns the packaged file into CRLF, while a copy installed from the repository is LF."""
        return a.replace(b'\r\n', b'\n') == b.replace(b'\r\n', b'\n')

    def check_desktop_plugin(self, home):
        """Raise if installing the Odysseus plugins into ``home`` would overwrite something different. Writes nothing. Returns the return plugin's (source, target)."""
        pairs = self._plugin_pairs(home)
        for name, source, target, known in pairs:
            if target.exists():
                have = target.read_bytes()
                digests = {hashlib.sha256(have).hexdigest(), hashlib.sha256(have.replace(b'\r\n', b'\n')).hexdigest()}
                if not self._same_plugin(have, source.read_bytes()) and not digests & set(KNOWN_PREVIOUS_PLUGIN_SHA256 if name == PLUGIN_NAME else known):
                    raise ComponentError('A different desktop plugin with this name already exists; it was not overwritten.')
        return pairs[0][1], pairs[0][2]

    def install_desktop_plugin(self, home):
        """Copy the Odysseus desktop plugins (return, resume) into a managed home's desktop-plugins folder. Idempotent (a copy that differs only in line
        endings is already installed and is left alone); a recognised earlier copy of ours is upgraded; a different existing plugin is never overwritten.
        Every conflict is found before anything is written. Returns the return plugin's name."""
        self.check_desktop_plugin(home)
        for name, source, target, known in self._plugin_pairs(home):
            if not target.exists() or not self._same_plugin(target.read_bytes(), source.read_bytes()):
                atomic_bytes(target, source.read_bytes())
        return PLUGIN_NAME

    def installed_desktop_plugins(self, home):
        return [name for name, source, target, known in self._plugin_pairs(home) if target.is_file() and self._same_plugin(target.read_bytes(), source.read_bytes())]

    # -- reviewed plugin overlays (opt-in, never run by ensure/repair) -----------------------------------------------------------------
    def wiki_context_plan(self, home, python, *, wiki_root, steward_src, workspaces, project='odysseus', agent='hermes'):
        """What installing the Wiki context plugin would do. Writes nothing."""
        source = self.application_root / WIKI_CONTEXT_PLUGIN_SOURCE
        if not (source / 'plugin.yaml').is_file():
            raise ComponentError('The packaged Wiki context plugin is missing from this Odysseus installation.')
        if not (Path(steward_src) / 'wiki_steward.py').is_file() or not (Path(wiki_root) / 'steward.json').is_file():
            raise ComponentError('steward_src must hold wiki_steward.py and wiki_root must be a Wiki Steward wiki (steward.json).')
        target = Path(home) / 'plugins' / WIKI_CONTEXT_PLUGIN
        config_path = Path(home) / WIKI_CONTEXT_CONFIG
        wanted = {'wiki_root': str(wiki_root), 'steward_src': str(steward_src), 'agent': agent, 'projects': {project: {'workspaces': [str(w) for w in workspaces]}}}
        settings = self.home_settings(python, home)
        enabled = [x for x in (settings.get('plugins_enabled') or []) if isinstance(x, str)]
        files = {item.relative_to(source).as_posix() for item in source.rglob('*') if item.is_file() and '__pycache__' not in item.parts}
        state = 'new'
        if target.exists():
            manifest = target / 'plugin.yaml'
            if not manifest.is_file() or f'name: {WIKI_CONTEXT_PLUGIN}' not in manifest.read_text(encoding='utf-8', errors='replace'):
                raise ComponentError('A different plugin with this name already exists; it was not overwritten.')
            same = all((target / name).is_file() and (target / name).read_bytes().replace(b'\r\n', b'\n') == (source / name).read_bytes().replace(b'\r\n', b'\n') for name in files)
            state = 'same' if same else 'upgrade'
        config_state = 'new'
        if config_path.exists():
            try:
                config_state = 'same' if json.loads(config_path.read_text(encoding='utf-8-sig')) == wanted else 'differs'
            except ValueError:
                config_state = 'differs'
        return {'plugin': state, 'config': config_state, 'enabled': WIKI_CONTEXT_PLUGIN in enabled, 'target': str(target), 'config_path': str(config_path),
                'wanted_config': wanted, 'applies': 'new chats after a gateway restart or plugin reload'}

    def install_wiki_context_plugin(self, home, python, **kwargs):
        """Copy the Wiki context plugin into a managed home, write its settings file and enable it. An existing settings file that differs is never overwritten."""
        plan = self.wiki_context_plan(home, python, **kwargs)
        if plan['config'] == 'differs':
            raise ComponentError(f'{plan["config_path"]} already exists with different content; it was not overwritten.')
        source = self.application_root / WIKI_CONTEXT_PLUGIN_SOURCE
        target = Path(plan['target'])
        target.mkdir(parents=True, exist_ok=True)
        for item in source.rglob('*'):
            if item.is_file() and '__pycache__' not in item.parts:
                dest = target / item.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_bytes(dest, item.read_bytes())
        if plan['config'] == 'new':
            atomic_json(Path(plan['config_path']), plan['wanted_config'])
        if not plan['enabled']:
            settings = self.home_settings(python, home)
            enabled = [x for x in (settings.get('plugins_enabled') or []) if isinstance(x, str)] + [WIKI_CONTEXT_PLUGIN]
            self._hermes_yaml(python, _WRITE_SETTINGS, str(Path(home) / 'config.yaml'), json.dumps({'plugins': {'enabled': enabled}}))
        return WIKI_CONTEXT_PLUGIN

    def security_plugin_state(self, home):
        """absent | pristine (the unpatched upstream file) | patched (our reviewed patch is in) | unknown (anything else: never touched)."""
        plugin_py = Path(home) / 'plugins' / SECURITY_PLUGIN / 'hermes_security' / 'plugin.py'
        if not plugin_py.is_file():
            return 'absent'
        text = plugin_py.read_text(encoding='utf-8', errors='replace')
        if '_skill_metadata(' in text and 'description=description' in text:
            return 'patched'
        return 'pristine' if 'ctx.register_skill(name, skill_root / name / "SKILL.md")' in text else 'unknown'

    def apply_security_plugin_patch(self, home, dry_run=True):
        """Give the hermes-security plugin's skills real descriptions (upstream registers them with none).

        Reviewed patch: integrations/hermes-mode/patches/hermes-security-skill-descriptions.patch. Built in a staging copy and checked there; the
        installed folder is never edited in place: it is moved to the component's backups folder and the patched copy takes its name. Only a plugin
        whose file is the exact upstream one is touched. Takes effect when the gateway next starts.
        Attribution: hermes-security, NousResearch/hermes-official-plugins, Apache-2.0 (see the plugin's own LICENSE and NOTICE)."""
        state = self.security_plugin_state(home)
        report = {'state': state, 'patch': SECURITY_PLUGIN_PATCH, 'applied': False}
        if state in ('absent', 'patched', 'unknown') or dry_run:
            return report
        patch = (self.application_root / SECURITY_PLUGIN_PATCH).resolve()
        if not patch.is_file():
            raise ComponentError('The reviewed hermes-security patch is missing from this Odysseus installation.')
        git = shutil.which('git')
        if not git:
            raise ComponentError('git is required to apply the reviewed patch.')
        source = Path(home) / 'plugins' / SECURITY_PLUGIN
        stamp = time.strftime('%Y%m%d-%H%M%S')
        staging = private_directory(self.directory / 'staging') / f'{SECURITY_PLUGIN}-{stamp}'
        shutil.copytree(source, staging, ignore=shutil.ignore_patterns('__pycache__'))
        try:
            for step in (('apply', '--check'), ('apply',)):
                done = subprocess.run([git, *step, str(patch)], cwd=str(staging), capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
                if done.returncode:
                    raise ComponentError('The reviewed hermes-security patch does not apply to the installed plugin: ' + done.stderr.strip()[:200])
            compile((staging / 'hermes_security' / 'plugin.py').read_text(encoding='utf-8'), 'plugin.py', 'exec')
            atomic_bytes(staging / 'ODYSSEUS_PATCH.txt', ('hermes-security by NousResearch (hermes-official-plugins, Apache-2.0).\nModified by Odysseus: ' + SECURITY_PLUGIN_PATCH +
                         ' (skill descriptions in the skill index). Applied ' + stamp + '. The original is in the component backups folder.\n').encode('utf-8'))
            backup = private_directory(self.directory / 'backups') / f'{SECURITY_PLUGIN}-{stamp}'
            os.replace(source, backup)
            try:
                os.replace(staging, source)
            except OSError as exc:                      # never leave the gateway home without the plugin: put the original straight back
                os.replace(backup, source)
                raise ComponentError('The patched plugin could not be put in place; the original was restored: ' + str(exc)) from exc
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        report.update(applied=True, backup=str(backup), applies='when the gateway next starts')
        return report

    def install_workspace_grants_plugin(self, home, python):
        """Copy the workspace-grants plugin into a managed home and enable it, preserving the existing plugins.enabled list.

        The plugin is the live half of the deny-by-default write boundary: it rebinds the agent's write-root lookup to the
        component's own grants file, re-read on every file call. A different plugin with this name is never overwritten.
        """
        source = self.application_root / WORKSPACE_GRANTS_PLUGIN_SOURCE
        if not (source / 'plugin.yaml').is_file():
            raise ComponentError('The packaged workspace-grants plugin is missing from this Odysseus installation.')
        target = Path(home) / 'plugins' / WORKSPACE_GRANTS_PLUGIN
        if target.exists():
            manifest = target / 'plugin.yaml'
            if not manifest.is_file():
                raise ComponentError('The existing workspace-grants plugin folder has no plugin.yaml; it was not overwritten.')
            if f'name: {WORKSPACE_GRANTS_PLUGIN}' not in manifest.read_text(encoding='utf-8', errors='replace'):
                raise ComponentError('A different plugin with this name already exists; it was not overwritten.')
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                atomic_bytes(dest, item.read_bytes())
        settings = self.home_settings(python, home)
        enabled = [x for x in (settings.get('plugins_enabled') or []) if isinstance(x, str)]
        if WORKSPACE_GRANTS_PLUGIN not in enabled:
            enabled.append(WORKSPACE_GRANTS_PLUGIN)
            self._hermes_yaml(python, _WRITE_SETTINGS, str(Path(home) / 'config.yaml'), json.dumps({'plugins': {'enabled': enabled}}))
        return WORKSPACE_GRANTS_PLUGIN

    # -- validate / status -----------------------------------------------------------------
    def validate(self, config):
        pin = load_webapp_pin(self.application_root)
        try:
            agent = safe_directory(config['agent'])
            with read_only_git():
                verify_checkout(agent, pin, self.applied_patches(agent) if config.get('origin') == 'managed' else None)
            if config.get('origin') == 'managed' and self.route_guard_state(config) != 'applied':
                raise ComponentError('The managed gateway source lacks the reviewed route guard; it will not be started.')
            probe_python(config['python'], require_agent=True)
            for key in ('home',):
                path = safe_directory(config[key])
                if path.is_relative_to(self.application_root):
                    raise ComponentError('Hermes user state must not be stored under the source checkout.')
            if config.get('bundle'):
                verdict = inspect_bundle(safe_directory(config['bundle']))
                if not verdict['ok']:
                    raise ComponentError('The Webapp bundle is incomplete or stale: ' + '; '.join(verdict['problems']))
                if config.get('build') and verdict['receipt_sha256'] != config['build'].get('receipt_sha256'):
                    raise ComponentError('The Webapp bundle no longer matches the build that was recorded; run repair.')
            elif config['origin'] == 'managed':
                raise ComponentError('A managed Webapp needs its bundle; run repair.')
        except (KeyError, TypeError) as exc:
            raise ComponentError('Webapp component configuration is incomplete; run repair.') from exc

    def status(self):
        config = self.config()
        if not config:
            return {'enabled': False, 'state': 'not_installed'}
        result = {'enabled': config['enabled'], 'origin': config['origin'], 'url': config['url'], 'release': config.get('release'),
                  'previous': config.get('previous'), 'revision': (config.get('revisions') or {}).get('webapp')}
        result['route_guard'] = self.cached_route_guard_state(config)
        result['write_roots'] = config.get('write_roots')            # None: inherited from the operator's HERMES_WRITE_SAFE_ROOT
        result['logins'] = self.logins()
        if not config['enabled']:
            return {**result, 'state': 'disabled'}
        port = loopback_port(config['url'])
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/api/health')
            response = connection.getresponse()
            body = response.read(65537)
            try:
                health = json.loads(body) if len(body) <= 65536 else None
            except ValueError:
                health = None
            identity = isinstance(health, dict) and health.get('ok') is True and isinstance(health.get('auth_required'), bool) and 'displayVersion' in health
            result['state'] = 'ready' if identity and response.status == 200 else ('unavailable' if identity else 'conflict')
            if identity:
                result['sign_in_gate'] = health['auth_required']
        except (OSError, http.client.HTTPException):
            result['state'] = 'stopped' if _port_free(port) else 'unavailable'
        finally:
            connection.close()
        return result

    def active_sessions(self):
        """The gateway's public, best-effort count of sessions active in the last five minutes (None when unreadable). It reports 0 on
        its own errors, so a 0 is not proof that nothing is running."""
        config = self.config()
        if not config:
            return None
        connection = http.client.HTTPConnection('127.0.0.1', loopback_port(config['url']), timeout=3)
        try:
            connection.request('GET', '/api/status')
            data = json.loads(connection.getresponse().read(262144))
            counts = [data.get('active_sessions')]
            for platform in (data.get('gateway_platforms') or {}).values():
                counts.append(((platform or {}).get('metrics') or {}).get('active_runs'))
            return sum(count for count in counts if isinstance(count, int) and not isinstance(count, bool))
        except (OSError, http.client.HTTPException, ValueError, AttributeError):
            return None
        finally:
            connection.close()

    # -- enable / disable / rollback ------------------------------------------------------
    def disable(self):
        with component_lock(self.directory):
            config = self.config() or {'version': 1, 'kind': 'webapp', 'enabled': True}
            atomic_json(self.config_path, {**config, 'enabled': False})

    def rollback(self):
        """Managed only: point the component at the previous release (kept side by side). The gateway must be restarted to serve it."""
        with component_lock(self.directory):
            config = self.config()
            if not config or config['origin'] != 'managed':
                raise ComponentError('Only a managed Webapp has releases to roll back to.')
            previous = config.get('previous')
            if not previous:
                raise ComponentError('There is no previous release to roll back to.')
            release_dir = self._release_dir(previous)
            try:
                record = json.loads((release_dir / 'release.json').read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ComponentError('The previous release is missing or unreadable; nothing was changed.') from exc
            if record.get('state') != 'ready':
                raise ComponentError('The previous release is not complete; nothing was changed.')
            candidate = {**config, 'release': previous, 'previous': config['release'], 'agent': str(release_dir / 'src'),
                         'bundle': str(release_dir / 'src' / BUNDLE_RELATIVE), 'revisions': {'webapp': record['revision']},
                         'build': {'receipt_sha256': record.get('receipt_sha256'), 'tools': record.get('tools')}}
            venv = release_dir / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
            if config.get('python_origin') == 'managed':
                candidate['python'] = str(venv)
            self.validate_release(candidate, record)
            atomic_json(self.config_path, candidate)
            return candidate

    def validate_release(self, candidate, record):
        verdict = inspect_bundle(candidate['bundle'])
        if not verdict['ok'] or verdict['receipt_sha256'] != record.get('receipt_sha256'):
            raise ComponentError('The previous release no longer matches its recorded build; nothing was changed.')
        probe_python(candidate['python'], require_agent=True)

    # -- gateway lifecycle (managed only) --------------------------------------------------
    def _require_managed(self):
        config = self.config()
        if not config or not config['enabled']:
            raise ComponentError('The Webapp component is not installed or is disabled.')
        if config['origin'] != 'managed':
            raise ComponentError('This gateway is user-owned (adopted); Odysseus never starts, stops or restarts it.')
        return config

    PROTECTED_PATHS = 'vault/odysseus-protected-paths.json'

    def protected_paths_file(self, config=None):
        config = config or self.config()
        if not config or config.get('origin') != 'managed':
            raise ComponentError('The blocked-folder list belongs to a managed gateway home; an adopted home is never written.')
        return Path(config['home']) / self.PROTECTED_PATHS

    def blocked_folders(self):
        """The folders the route guard refuses (the neutral list, else the retired WebUI's). Never raises on a missing file."""
        config = self.config()
        if not config:
            return []
        neutral = self.protected_paths_file(config) if config.get('origin') == 'managed' else None
        for path in (neutral, Path(config['home']) / 'vault' / 'webui-trusted-workspaces.json'):
            if path and path.is_file():
                try:
                    return [item['path'] for item in json.loads(path.read_text(encoding='utf-8')).get('blocked') or [] if isinstance(item, dict) and item.get('path')]
                except (OSError, ValueError, AttributeError):
                    raise ComponentError(f'{path.name} is unreadable; the route guard will refuse writes until it is repaired.')
        return []

    def set_blocked_folders(self, paths):
        """Write the neutral list. The first write imports the retired WebUI list, so adopting this store never loses an existing entry."""
        config = self._require_managed()
        target = self.protected_paths_file(config)
        existing = self.blocked_folders()
        wanted = []
        for item in list(existing) + list(paths):
            item = os.path.normpath(str(item))
            if item not in wanted:
                wanted.append(item)
        private_directory(target.parent)
        atomic_json(target, {'version': 1, 'blocked': [{'path': item} for item in wanted]})
        return wanted

    def remove_blocked_folder(self, path):
        config = self._require_managed()
        target = self.protected_paths_file(config)
        keep = [item for item in self.blocked_folders() if os.path.normcase(os.path.normpath(item)) != os.path.normcase(os.path.normpath(str(path)))]
        private_directory(target.parent)
        atomic_json(target, {'version': 1, 'blocked': [{'path': item} for item in keep]})
        return keep

    # -- settings kept in the component config (managed only) -------------------------------------
    def logins(self):
        config = self.config() or {}
        allowed = set(config.get('logins') or [])
        return {tool: 'allowed' if tool in allowed else 'isolated' for tool in LOGIN_TOOLS}

    def set_logins(self, allow=(), deny=()):
        """Deliberately let the gateway use (or stop it using) the Claude / Codex logins the user keeps outside the Hermes home."""
        unknown = [tool for tool in (*allow, *deny) if tool not in LOGIN_TOOLS]
        if unknown:
            raise ComponentError('Unknown login(s): ' + ', '.join(unknown) + '. Known: ' + ', '.join(LOGIN_TOOLS) + '.')
        with component_lock(self.directory):
            config = self._require_managed()
            current = (set(config.get('logins') or []) | set(allow)) - set(deny)
            atomic_json(self.config_path, {**config, 'logins': sorted(current)})
        return self.logins()

    def write_roots(self):
        return list((self.config() or {}).get('write_roots') or [])

    # Folders under the user profile that hold logins or keys: never a write root, whatever else is true.
    NEVER_UNDER_PROFILE = ('.claude', '.codex', '.ssh', '.aws', '.gnupg', 'AppData')

    def _odysseus_trees(self):
        """The running Odysseus install and, when it is a linked worktree, the main checkout it belongs to (the live installation)."""
        trees = [Path(self.application_root)]
        marker = Path(self.application_root) / '.git'
        try:
            if marker.is_file():
                text = marker.read_text(encoding='utf-8').strip()
                if text.startswith('gitdir:'):
                    admin = Path(text.split(':', 1)[1].strip())
                    if admin.parent.name == 'worktrees':
                        trees.append(admin.parent.parent.parent)
        except OSError:
            pass
        return trees

    @staticmethod
    def _filesystem(path):
        if os.name != 'nt':
            return 'unknown'
        import ctypes
        anchor = os.path.splitdrive(os.path.realpath(path))[0] + '\\'
        buffer = ctypes.create_unicode_buffer(64)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(anchor, None, 0, None, None, None, buffer, 64)
        return buffer.value if ok else 'unknown'

    def inspect_root(self, path):
        """Read-only description of a candidate write root: files are read (``.git``, ``HEAD``), nothing is listed, scanned, run or written."""
        candidate = Path(str(path))
        report = {'path': str(path), 'exists': candidate.is_dir(), 'git_root': False, 'kind': None, 'branch': None, 'git_dir': None, 'common_git_dir': None,
                  'filesystem': self._filesystem(candidate) if candidate.is_dir() else None, 'notes': [], 'problem': None}
        marker = candidate / '.git'
        git_dir = None
        if marker.is_dir():
            report.update(git_root=True, kind='repository')
            git_dir = marker
            report['git_dir'] = report['common_git_dir'] = str(marker)
        elif marker.is_file():
            try:
                text = marker.read_text(encoding='utf-8').strip()
            except OSError:
                text = ''
            if text.startswith('gitdir:'):
                git_dir = Path(text.split(':', 1)[1].strip())
                report.update(git_root=True, kind='linked worktree', git_dir=str(git_dir))
                common = git_dir.parent.parent if git_dir.parent.name == 'worktrees' else git_dir
                report['common_git_dir'] = str(common)
                if os.path.normcase(os.path.realpath(common)).startswith(os.path.normcase(os.path.realpath(candidate)) + os.sep):
                    pass
                else:
                    report['notes'].append(f'Git data of this worktree lives OUTSIDE the folder, in {common}: a commit there also writes to that repository (the route guard checks the working folder only)')
        if git_dir is not None:
            try:
                head = (git_dir / 'HEAD').read_text(encoding='utf-8').strip()
                report['branch'] = head[len('ref: refs/heads/'):] if head.startswith('ref: refs/heads/') else f'detached {head[:8]}'
            except OSError:
                pass
        if (report['filesystem'] or '').lower() == 'btrfs':
            report['notes'].append('Btrfs (WinBtrfs): heavy writes (large checkouts, builds, git gc) can trigger the known driver freeze; keep such work off this drive')
        report['problem'] = self.root_problem(path, self.config() or {'home': '', 'url': ''}, _report=report)
        return report

    def root_problem(self, path, config, _report=None):
        """Why ``path`` may not be a write root of the gateway, or None. A write root must be the exact top folder of a Git repository."""
        candidate = Path(str(path))
        if not candidate.is_absolute() or not candidate.is_dir():
            return 'must be an existing folder with an absolute path'
        real = Path(os.path.realpath(candidate))
        if real.parent == real:
            return 'is a drive root'
        norm = lambda item: os.path.normcase(os.path.realpath(item))
        inside = lambda child, parent: norm(child) == norm(parent) or norm(child).startswith(norm(parent) + os.sep)
        profile = norm(Path.home())
        if norm(real) == profile or profile.startswith(norm(real) + os.sep):
            return 'is your whole user profile folder or a parent of it; name the project folders instead'
        if not ((candidate / '.git').is_dir() or (candidate / '.git').is_file()):
            return 'is not the top folder of a Git repository (a write root is one repository, never a parent folder or a sub-folder)'
        forbidden = [self.root, config.get('home') or self.root, *self._odysseus_trees()]
        for variable in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData'):
            if self.environment.get(variable):
                forbidden.append(self.environment[variable])
        if self.environment.get('LOCALAPPDATA'):
            forbidden.append(Path(self.environment['LOCALAPPDATA']) / 'hermes')
        forbidden += [Path.home() / name for name in self.NEVER_UNDER_PROFILE]
        for item in forbidden:
            if inside(real, item):
                return f'is inside {item}, which the gateway must not be able to rewrite'
            if inside(item, real):
                return f'contains {item}, which the gateway must not be able to rewrite'
        return None

    def set_write_roots(self, add=(), remove=(), clear=False):
        """The folders the managed gateway may write to (its HERMES_WRITE_SAFE_ROOT). Narrower than, and independent of, the operator's own variable."""
        with component_lock(self.directory):
            config = self._require_managed()
            roots = [] if clear else list(config.get('write_roots') or [])
            drop = {os.path.normcase(os.path.normpath(item)) for item in remove}
            roots = [item for item in roots if os.path.normcase(os.path.normpath(item)) not in drop]
            for item in add:
                problem = self.root_problem(item, config)
                if problem:
                    raise ComponentError(f'{item} {problem}.')
                clean = os.path.normpath(str(item))
                if os.path.normcase(clean) not in {os.path.normcase(existing) for existing in roots}:
                    roots.append(clean)
            updated = {key: value for key, value in config.items() if key != 'write_roots'}
            if roots:
                updated['write_roots'] = roots
            atomic_json(self.config_path, updated)
        return roots

    def route_guard_patch(self):
        path = (self.application_root / ROUTE_GUARD_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed route-guard patch is missing from this Odysseus installation.')
        return path

    def webapp_patches(self):
        """The reviewed patches a new managed release carries, in the order they are applied."""
        return (*self.usage_patches(), self.transcript_clarity_patch(), self.goal_kanban_patch(), self.worker_git_hang_patch(),
                self.goal_drain_yield_patch())

    def goal_drain_yield_patch(self):
        path = (self.application_root / 'integrations/hermes-mode/patches/webapp-goal-drain-yield.patch').resolve()
        if not path.is_file():
            raise ComponentError('The reviewed goal drain-yield patch is missing from this Odysseus installation.')
        return path

    def goal_kanban_patch(self):
        path = (self.application_root / 'integrations/hermes-mode/patches/goal-kanban.patch').resolve()
        if not path.is_file():
            raise ComponentError('The reviewed goal-Kanban patch is missing from this Odysseus installation.')
        return path


    def worker_git_hang_patch(self):
        path = (self.application_root / 'integrations/hermes-mode/patches/webapp-worker-git-hang.patch').resolve()
        if not path.is_file():
            raise ComponentError('The reviewed worker git-hang patch is missing from this Odysseus installation.')
        return path

    def usage_patches(self):
        """The seven-patch set (through the usage/context patch) releases carried before the transcript-clarity patch (kept for rollback)."""
        return (*self.protected_patches(), self.usage_context_patch())

    def transcript_clarity_patch(self):
        path = (self.application_root / TRANSCRIPT_CLARITY_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed transcript-clarity patch is missing from this Odysseus installation.')
        return path

    def protected_patches(self):
        """The six-patch set (through update protection) releases carried before the usage/context patch (kept for rollback)."""
        return (*self.resume_patches(), self.update_protection_patch())

    def usage_context_patch(self):
        path = (self.application_root / USAGE_CONTEXT_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed usage-context patch is missing from this Odysseus installation.')
        return path

    def resume_patches(self):
        """The five-patch set (through the resume-outcomes patch) releases carried before update protection (kept for rollback)."""
        return (*self.composer_patches(), self.resume_outcomes_patch())

    def update_protection_patch(self):
        path = (self.application_root / UPDATE_PROTECTION_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed update-protection patch is missing from this Odysseus installation.')
        return path

    def composer_patches(self):
        """The four-patch set (workspace patches plus the composer control center) releases carried before the resume-outcomes patch (kept for rollback)."""
        return (*self.workspace_patches(), self.composer_patch())

    def resume_outcomes_patch(self):
        path = (self.application_root / RESUME_OUTCOMES_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed resume-outcomes patch is missing from this Odysseus installation.')
        return path

    def workspace_patches(self):
        """The three-patch set (route guard, MCP recovery, desktop workspace) releases carried before the composer control center (kept for rollback)."""
        return (*self.recovery_patches(), self.desktop_workspace_patch())

    def composer_patch(self):
        path = (self.application_root / COMPOSER_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed composer patch is missing from this Odysseus installation.')
        return path

    def recovery_patches(self):
        """The route guard then the MCP recovery: the set releases carried before the desktop-workspace patch (kept for rollback)."""
        recovery = (self.application_root / MCP_RECOVERY_PATCH).resolve()
        if not recovery.is_file():
            raise ComponentError('The reviewed MCP-recovery patch is missing from this Odysseus installation.')
        return (self.route_guard_patch(), recovery)

    def desktop_workspace_patch(self):
        path = (self.application_root / DESKTOP_WORKSPACE_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed desktop-workspace patch is missing from this Odysseus installation.')
        return path

    def applied_patches(self, agent):
        """The reviewed patch set a managed source carries exactly: the current set, the previous seven-, six-, five-, four- and three-patch sets, the route guard with the MCP recovery, or the route guard alone
        (earlier releases kept side by side for rollback). The current set when it matches neither, so a mismatch is reported against it."""
        for patches in (self.webapp_patches(), self.usage_patches(), self.protected_patches(), self.resume_patches(), self.composer_patches(), self.workspace_patches(), self.recovery_patches(), self.route_guard_patch()):
            if patch_state(agent, patches) == 'applied':
                return patches
        return self.webapp_patches()

    GUARD_CACHE_SECONDS = 60.0

    def _guard_key(self, config):
        try:
            stamp = self.config_path.stat().st_mtime_ns          # every config write (ensure, rollback, roots, logins) changes this
        except OSError:
            stamp = None
        return (config.get('origin'), config.get('agent'), config.get('release'), (config.get('revisions') or {}).get('webapp'), stamp)

    def invalidate_route_guard(self):
        self._guard_cache = None

    def cached_route_guard_state(self, config):
        """Display-only variant of route_guard_state for status(): the answer costs dozens of git processes, so it is reused for a short time,
        keyed on the component config, and computed by one caller at a time. Start-up and security checks call route_guard_state() directly."""
        key = self._guard_key(config)
        cached = self._guard_cache
        if cached and cached[0] == key and time.monotonic() - cached[1] < self.GUARD_CACHE_SECONDS:
            return cached[2]
        with self._guard_lock:
            cached = self._guard_cache                          # another caller may have filled it while this one waited
            if cached and cached[0] == key and time.monotonic() - cached[1] < self.GUARD_CACHE_SECONDS:
                return cached[2]
            state = self.route_guard_state(config)
            if state != 'unknown':                              # an unreadable state is retried, never remembered
                self._guard_cache = (key, time.monotonic(), state)
            return state

    def route_guard_state(self, config):
        """'applied' for a managed source carrying exactly a reviewed patch set, else 'absent' (adopted gateways run the user's unpatched code)."""
        if config.get('origin') != 'managed':
            return 'absent'
        try:
            agent = safe_directory(config['agent'])
            return patch_state(agent, self.applied_patches(agent))
        except (ComponentError, OSError, KeyError):
            return 'unknown'

    def gateway_environment(self, config):
        env = {key: self.environment[key] for key in _GATEWAY_ENV if key in self.environment}
        # Deny by default. The agent treats an EMPTY write-root list as "no restriction", and the operator's own variable (used by the
        # old WebUI) used to be inherited here, which made this gateway able to write to unrelated projects and the Odysseus install.
        # Now the environment names an empty folder only this component owns; the roots the user granted are read live from this
        # component's config by the odysseus-workspace-grants plugin, so granting or revoking needs no restart. If that plugin is not
        # active, the grants simply do not apply and nothing outside the empty folder is writable.
        env['HERMES_WRITE_SAFE_ROOT'] = str(private_directory(self.directory / 'no-workspace'))
        env['ODYSSEUS_WORKSPACE_GRANTS'] = str(self.config_path)
        allowed = set(config.get('logins') or [])
        for tool, variable in LOGIN_TOOLS.items():
            if tool not in allowed:
                env[variable] = str(private_directory(self.directory / 'isolated-logins' / tool))
            elif variable in self.environment:                # allowed: the user's own custom location, if they use one
                env[variable] = self.environment[variable]
        if 'HERMES_WEBAPP_ALLOW_GIT_PUBLISH' in self.environment:       # the operator's explicit opt-in for push / pull request, never invented
            env['HERMES_WEBAPP_ALLOW_GIT_PUBLISH'] = self.environment['HERMES_WEBAPP_ALLOW_GIT_PUBLISH']
        env.update({'HERMES_HOME': config['home'], 'PYTHONPATH': config['agent'], 'HERMES_WEBAPP_DIST': config['bundle'],
                    'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        return env

    def start(self, wait=True, timeout=180):
        config = self._require_managed()
        with component_lock(self.directory / 'service'):
            self.validate(config)
            if not (Path(config['home']) / 'config.yaml').is_file():
                raise ComponentError('The gateway home is missing or incomplete; run repair (it does not rebuild).')
            self.install_workspace_grants_plugin(Path(config['home']), config['python'])
            try:                                              # keeps the Odysseus desktop plugins current at every start; a convenience, never a reason not to start
                self.install_desktop_plugin(Path(config['home']))
            except (ComponentError, OSError) as exc:
                self.last_actions.append(f'desktop plugins left unchanged: {exc}')
            state = self.status()['state']
            if state == 'ready':
                return self.status()
            if state != 'stopped':
                raise ComponentError('The Webapp port is occupied or unhealthy; no takeover attempted.')
            logs = private_directory(self.directory / 'logs')
            with (logs / 'gateway.log').open('ab') as log:
                flags = _NO_WINDOW | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                process = subprocess.Popen([config['python'], '-W', 'ignore', '-m', 'gateway.run'], cwd=config['agent'], env=self.gateway_environment(config),
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        if not wait:
            return {**self.status(), 'state': 'starting'}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ComponentError('The gateway exited while starting; see managed/webapp/logs/gateway.log.')
            if self.status()['state'] == 'ready':
                return self.status()
            time.sleep(1)
        raise ComponentError('The gateway did not become ready in time; it was left running for inspection.')

    def owned_listener(self, config):
        """The pid of the gateway Odysseus provisioned, proven by the gateway's own pid record (kind, home and entry file), or an error."""
        from src import service_controls as controls
        port = loopback_port(config['url'])
        pids, loopback_only = controls.listening_pids(port)
        if len(pids) != 1 or not loopback_only:
            raise controls.Unavailable('The Webapp port is not owned by exactly one loopback-only process; nothing was stopped.')
        pid = next(iter(pids))
        try:
            record = json.loads((Path(config['home']) / 'gateway.pid').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise controls.Unavailable("The gateway's pid record is unreadable; its identity cannot be confirmed, nothing was stopped.") from exc
        norm = lambda value: os.path.normcase(os.path.abspath(str(value)))
        argv = record.get('argv') if isinstance(record.get('argv'), list) else []
        # The gateway was started from the release that was current THEN; activating a newer release moves the pointer but not the running
        # process. It is still ours when its entry file is the run.py of any release this component built under its own releases directory.
        if (record.get('kind') != 'hermes-gateway' or record.get('pid') != pid or norm(record.get('hermes_home', '')) != norm(config['home'])
                or not argv or self._release_of_entry(argv[0]) is None):
            raise controls.Unavailable('The listener is not the gateway Odysseus provisioned; nothing was stopped.')
        return pid

    def _release_of_entry(self, entry):
        """The release id when `entry` is exactly <releases>/<id>/src/gateway/run.py of this component, else None."""
        norm = lambda value: os.path.normcase(os.path.abspath(str(value)))
        try:
            relative = Path(norm(entry)).relative_to(norm(self.directory / 'releases'))
        except ValueError:
            return None
        parts = relative.parts
        if len(parts) == 4 and _RELEASE.fullmatch(parts[0]) and parts[1:] == ('src', 'gateway', 'run.py'):
            return parts[0]
        return None

    def running_release(self, config):
        """The release a live gateway of this component was started from (from its own pid record), or None."""
        try:
            record = json.loads((Path(config['home']) / 'gateway.pid').read_text(encoding='utf-8'))
            release = self._release_of_entry((record.get('argv') or [''])[0]) if record.get('kind') == 'hermes-gateway' else None
            pid = record.get('pid')
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            return None
        if release is None or not isinstance(pid, int):
            return None
        try:
            from src import service_controls as controls
            return release if pid in controls.processes() else None
        except Exception:                                    # noqa: BLE001 - unreadable process table: keep the release, never guess it is free
            return release

    def _running_admissions(self, config):
        """Turns the gateway itself records as queued or started (read-only; None when its store is unreadable). Unlike the five-minute
        session count this is the gateway's own durable truth, so an idle gateway can be drained to a provable zero."""
        import sqlite3
        db = Path(config['home']) / 'state.db'
        if not db.is_file():
            return 0
        try:
            conn = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True, timeout=3)
            try:
                return conn.execute("SELECT COUNT(*) FROM session_admissions WHERE status='started'").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    @staticmethod
    def _ancestor_pids(pid=None):
        """Pids of the processes that started this one (nearest first), or None when the process tree cannot be read."""
        pid = pid or os.getpid()
        try:
            import psutil
            return [parent.pid for parent in psutil.Process(pid).parents()]
        except Exception:                                    # noqa: BLE001 - psutil missing or the process vanished
            pass
        try:
            from src import service_controls as controls
            table, chain = controls.processes(), []
            while pid in table and table[pid][0] not in chain and table[pid][0]:
                pid = table[pid][0]
                chain.append(pid)
            return chain
        except Exception:                                    # noqa: BLE001
            return None

    # The gateway's reversible external-drain marker (gateway/drain_control.py): an ACTIVE marker makes the
    # gateway's _drain_control_watcher refuse NEW turns (_external_drain_active) until the marker is removed.
    # The marker IS the control channel — the same contract POST /api/gateway/drain writes. Odysseus writes it
    # directly into the gateway home it provisioned, exactly as it reads that home's gateway.pid and state.db.
    DRAIN_REQUEST_FILENAME = '.drain_request.json'
    DRAIN_REQUEST_PRINCIPAL = 'odysseus-webapp'

    @staticmethod
    def _instantiation_epoch():
        """Mirror of the gateway's drain_control.current_instantiation_epoch: '<boot_id>:<pid1_start>' on Linux,
        '' where /proc is unreadable (Windows). An empty epoch disables the gateway's staleness check, so the
        marker is honoured on every platform; a same-host restart never changes it."""
        boot_id = pid1_start = ''
        try:
            boot_id = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='utf-8').strip()
        except OSError:
            pass
        try:
            pid1_start = Path('/proc/1/stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()[19]
        except (OSError, IndexError):
            pass
        return f'{boot_id}:{pid1_start}' if (boot_id or pid1_start) else ''

    def _engage_external_drain(self, config):
        """Write the begin-drain marker BEFORE the idle wait so the gateway itself quiesces (new turns refused,
        goal wakes yield) while we wait; without it an active goal keeps admissions started and the wait cannot
        converge. Returns the prior marker's raw text (None when absent) so release can restore it. The payload
        matches gateway.drain_control.write_drain_request; the write is atomic, so a failure leaves no marker."""
        from datetime import datetime, timezone
        path = Path(config['home']) / self.DRAIN_REQUEST_FILENAME
        try:
            prior = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            prior = None
        except OSError as exc:
            raise ComponentError(f'The gateway drain marker at {path} is unreadable; nothing was stopped.') from exc
        payload = {'action': 'drain', 'requested_at': datetime.now(timezone.utc).isoformat(),
                   'principal': self.DRAIN_REQUEST_PRINCIPAL, 'epoch': self._instantiation_epoch(),
                   'suppress_notification': False}
        try:
            atomic_bytes(path, json.dumps(payload).encode('utf-8'))
        except OSError as exc:
            raise ComponentError(f'Could not engage the gateway drain by writing {path}; nothing was stopped.') from exc
        return prior

    def _release_external_drain(self, config, prior):
        """Undo the engagement: remove our marker, or restore a pre-existing one (e.g. an operator's dashboard
        drain) byte-for-byte. Called on success BEFORE the kill and on every error path, so a failed or timed-out
        drain never leaves the gateway refusing turns. Raises ComponentError when the marker cannot be released."""
        path = Path(config['home']) / self.DRAIN_REQUEST_FILENAME
        try:
            if prior is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, prior.encode('utf-8'))
        except OSError as exc:
            raise ComponentError(f'The gateway drain marker at {path} could not be released; remove it manually, '
                                 'otherwise the gateway keeps refusing new turns.') from exc

    def stop(self, force=False, drain=0):
        """Stop the gateway Odysseus provisioned, never one that is serving the caller.

        Refused (even with force) when this process descends from the gateway: a turn that restarts its own gateway leaves the turn
        with an unknown outcome. Otherwise refused while turns are running; `drain` waits up to that many seconds for them to finish.
        With `drain` (and without force) the gateway's reversible external drain is engaged FIRST, so the gateway itself refuses new
        turns while we wait; the marker is released before the kill on success and on timeout/failure, and a pre-existing marker is
        restored. `force` skips both the wait and the engagement. The check does not read command lines, so renaming or wrapping the
        command changes nothing."""
        from src import service_controls as controls
        config = self._require_managed()
        with component_lock(self.directory / 'service'):
            if self.status()['state'] in ('stopped',):
                return self.status()
            pid = self.owned_listener(config)
            if pid in (self._ancestor_pids() or ()):
                raise ComponentError('This command is running inside the gateway it would stop, so the turn that issued it would be lost with an unknown outcome. '
                                     'Stop or restart the gateway from a separate PowerShell/terminal window instead.')
            deadline = time.monotonic() + max(0, drain)
            engage_drain = drain > 0 and not force
            # prior_marker is the marker content that pre-existed (None when absent) — NOT an engagement flag:
            # a fresh drain legitimately returns None, and release must still run for it.
            prior_marker = self._engage_external_drain(config) if engage_drain else None
            try:
                while True:
                    running = self._running_admissions(config)
                    count = 0 if (drain and running == 0) else (running or self.active_sessions())
                    if count == 0 or force or time.monotonic() >= deadline:
                        break
                    time.sleep(1)
                if count is None or count > 0:
                    if not force:
                        raise controls.ActiveRuns(count if count else 1, recent=not running)
            except BaseException as exc:
                if engage_drain:
                    try:
                        self._release_external_drain(config, prior_marker)
                    except ComponentError as release_error:
                        raise release_error from exc
                raise
            if engage_drain:
                # Release BEFORE the kill: a marker left behind would park the gateway restart() starts next in draining.
                # A failed release aborts here with the gateway still running, never a stuck drain after the kill.
                self._release_external_drain(config, prior_marker)
            controls.kill_tree(pid)
            port = loopback_port(config['url'])
            deadline = time.monotonic() + 30
            while not _port_free(port) and time.monotonic() < deadline:
                time.sleep(0.3)
            if not _port_free(port):
                raise ComponentError('The gateway did not release its port; check it manually.')
        return self.status()

    def restart(self, force=False, drain=0):
        self.stop(force=force, drain=drain)
        return self.start()


def _port_free(port):
    import socket
    try:
        with socket.socket() as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
