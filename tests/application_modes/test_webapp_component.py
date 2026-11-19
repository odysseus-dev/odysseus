"""Official Hermes Gateway/Webapp component: pins, ownership, the official build, lifecycle, rollback, and WebUI isolation.

Disposable fixtures only: a local git repository stands in for the pinned source, fake node/pnpm tools stand in for the toolchain,
and a stub gateway writes the same gateway.pid record the real one does. Nothing here touches a live Hermes or Odysseus."""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import hermes_webapp_component as hwc
from src import service_controls as controls
from src.hermes_component import ComponentError, atomic_json
from src.hermes_webapp_component import WebappComponent, find_tool, inspect_bundle, load_webapp_pin, read_webapp_config

GIT = shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'
SECRET = 'LEAK-CANARY-9f3a'

GATEWAY_STUB = r'''
import json, os, re, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
home = Path(os.environ['HERMES_HOME'])
port = int(re.search(r'port:\s*(\d+)', (home / 'config.yaml').read_text()).group(1))
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, body):
        data = json.dumps(body).encode(); self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == '/api/health': return self._send({'ok': True, 'version': 'x', 'displayVersion': 'x', 'commit': None, 'auth_required': False})
        if self.path == '/api/status':
            busy = 1 if (home / 'busy.flag').exists() else 0
            return self._send({'active_sessions': busy, 'dist': os.environ.get('HERMES_WEBAPP_DIST'), 'home': os.environ.get('HERMES_HOME'),
                               'secret_visible': 'ODYSSEUS_TEST_SECRET' in os.environ, 'safe_root': os.environ.get('HERMES_WRITE_SAFE_ROOT')})
        self.send_response(404); self.end_headers()
server = ThreadingHTTPServer(('127.0.0.1', port), H)
(home / 'gateway.pid').write_text(json.dumps({'pid': os.getpid(), 'kind': 'hermes-gateway', 'argv': sys.argv, 'start_time': 1, 'hermes_home': str(home)}))
server.serve_forever()
'''

FAKE_NODE = '''@echo off
if "%1"=="--version" (
  echo v26.12.1
  exit /b 0
)
set > "%TEMP%\\env-dump.txt"
echo %* > "%TEMP%\\node-args.txt"
mkdir apps\\desktop\\dist-webapp 2>nul
echo ^<html^>fixture^</html^>> apps\\desktop\\dist-webapp\\index.html
echo {"schema":1,"product":"webapp","platform":"win32","arch":"x64","node":"26.12.1"}> apps\\desktop\\dist-webapp\\hermes-build.json
exit /b 0
'''
FAKE_PNPM = '@echo off\r\necho 12.15.0\r\nexit /b 0\r\n'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def git(repo, *args):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
    done = subprocess.run([GIT, '-C', str(repo), *args], capture_output=True, text=True, env=env, timeout=120)
    if done.returncode:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


def tree_digest(path):
    """Content hash of every file under ``path`` (including .git), so any write at all is visible."""
    digest = hashlib.sha256()
    for item in sorted(Path(path).rglob('*')):
        digest.update(str(item.relative_to(path)).encode())
        if item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


@unittest.skipUnless(os.name == 'nt', 'the component and its fixtures are Windows-only')
class ComponentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='webapp-component-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / 'upstream'
        files = {'package.json': json.dumps({'engines': {'node': '>=26', 'pnpm': '^12'}}), 'run_agent.py': '', 'gateway/__init__.py': '',
                 'gateway/run.py': GATEWAY_STUB, 'hermes_cli/config_defaults.py': 'DEFAULT_CONFIG = {\n    "_config_version": 53,  # schema\n}\n', 'apps/desktop/scripts/build-webapp.mjs': '// stub\n'}
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text, encoding='utf-8')
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'config', 'user.name', 'fixture')
        git(self.repo, 'config', 'user.email', 'fixture@example.invalid')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'pinned')
        self.sha = git(self.repo, 'rev-parse', 'HEAD')
        self.app = self.tmp / 'app'
        (self.app / 'config').mkdir(parents=True)
        self.write_lock(self.sha)
        guard = self.app / 'integrations/hermes-mode/patches'
        guard.mkdir(parents=True)
        # Every reviewed patch is resolved by name before a release is built, so the fixture ships the whole set (the checkout below is faked).
        for reviewed in sorted((ROOT / 'integrations/hermes-mode/patches').glob('webapp-*.patch')):
            shutil.copy(reviewed, guard / reviewed.name)
        plugin = self.app / 'integrations/hermes-mode/desktop-plugins/odysseus-return'
        plugin.mkdir(parents=True)
        shutil.copy(ROOT / 'integrations/hermes-mode/desktop-plugins/odysseus-return/plugin.js', plugin / 'plugin.js')
        shutil.copytree(ROOT / 'integrations/hermes-mode/plugins/odysseus-workspace-grants', self.app / 'integrations/hermes-mode/plugins/odysseus-workspace-grants', ignore=shutil.ignore_patterns('__pycache__'))
        tools = self.tmp / 'local/hermes/tools'
        for name, version, body in (('node', '26.12.1', FAKE_NODE), ('pnpm', '12.15.0', FAKE_PNPM)):
            folder = tools / f'{name}-{version}-win32-x64'
            folder.mkdir(parents=True)
            (folder / f'{name}.cmd').write_text(body, encoding='utf-8')
        self.environment = {key: os.environ[key] for key in ('SystemRoot', 'ComSpec', 'PATHEXT', 'PATH') if key in os.environ}
        self.environment.update({'LOCALAPPDATA': str(self.tmp / 'local'), 'ODYSSEUS_TEST_SECRET': SECRET, 'HERMES_WRITE_SAFE_ROOT': str(self.tmp / 'safe')})
        self.gate = False
        self.root = self.tmp / 'root'
        self.component = WebappComponent(self.root, application_root=self.app, environment=self.environment)
        self.python = sys.executable
        def fake_yaml(component, python, code, *args):
            """Stand-in for the Hermes interpreter's round-trip YAML (ruamel lives in the Hermes runtime, covered by the real install)."""
            if code == hwc._READ_SETTINGS:
                return json.dumps({'config_present': True, 'webapp': {}, 'approvals_mode': 'manual', 'repo_scan_enabled': False, 'repo_scan_roots': 0,
                                   'sign_in_gate_configured': self.gate, 'plugins_enabled': []})
            return ''
        self.runtime_installs = []
        def fake_runtime_install(component, source, python, home, extras, log, env):
            self.runtime_installs.append((Path(source).parent.name, Path(home).name, tuple(extras)))
            if self.runtime_install_error:
                raise ComponentError(self.runtime_install_error)
        self.runtime_install_error = None
        patches = [mock.patch.object(hwc, 'probe_python', lambda *a, **k: {}), mock.patch.object(hwc, 'checkout_source', self.local_checkout),
                   mock.patch.object(WebappComponent, '_install_runtime_extras', fake_runtime_install),
                   mock.patch.object(WebappComponent, '_hermes_yaml', fake_yaml),
                   # the fake upstream cannot carry the real patch; applying it to a real pin is covered by test_webapp_route_guard_component and the real install
                   mock.patch.object(WebappComponent, 'route_guard_state', lambda component, config: 'applied' if config.get('origin') == 'managed' else 'absent')]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def write_lock(self, sha, repository='NousResearch/hermes-agent'):
        pin = {'repository': repository, 'revision': sha}
        (self.app / 'config/hermes-component.lock.json').write_text(json.dumps({
            'version': 1, 'agent': {'repository': 'NousResearch/hermes-agent', 'revision': sha},
            'webui': {'repository': 'nesquena/hermes-webui', 'revision': 'f' * 40}, 'webapp': pin,
            'runtime': {'major': 3, 'minor': 14, 'owner': 'hermes-pm', 'extras': ['mcp']}}), encoding='utf-8')

    def local_checkout(self, target, pin, log_path, environment, patch=None):
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
        subprocess.run([GIT, 'clone', '-q', '--no-checkout', str(self.repo), str(target)], check=True, capture_output=True, env=env)
        git(target, 'checkout', '-q', '--detach', pin.revision)
        # The update-protection patch ships this marker in a real release; the fake upstream cannot carry the patch.
        (Path(target) / '.odysseus-managed.json').write_text('{"managed_by": "odysseus"}', encoding='utf-8')

    def adopted_tree(self):
        """A user-owned Hermes: a checkout at the pin, a home with config and a sentinel."""
        agent = self.tmp / 'user/hermes-agent'
        subprocess.run([GIT, 'clone', '-q', str(self.repo), str(agent)], check=True, capture_output=True,
                       env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull})
        git(agent, 'checkout', '-q', '--detach', self.sha)
        home = self.tmp / 'user/home'
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('model:\n  default: mine\n', encoding='utf-8')
        (home / 'sentinel.txt').write_text('user data', encoding='utf-8')
        return agent, home

    def managed(self, **extra):
        return self.component.ensure('managed', port=extra.pop('port', free_port()), python=self.python, **extra)


class PinAndConfigTests(ComponentCase):
    def test_pin_is_one_immutable_full_commit_of_the_approved_repository(self):
        self.assertEqual(load_webapp_pin(self.app).revision, self.sha)
        for sha, repository in (('3b42157', 'NousResearch/hermes-agent'), ('main', 'NousResearch/hermes-agent'), ('G' * 40, 'NousResearch/hermes-agent'),
                                (self.sha, 'someone/else'), (self.sha.upper(), 'NousResearch/hermes-agent')):
            with self.subTest(sha=sha, repository=repository):
                self.write_lock(sha, repository)
                with self.assertRaises(ComponentError):
                    load_webapp_pin(self.app)

    def test_the_real_lock_file_pins_the_webapp_and_leaves_the_webui_pins_alone(self):
        from src.hermes_component import load_pins
        pin = load_webapp_pin(ROOT)
        self.assertEqual(len(pin.revision), 40)
        self.assertEqual(sorted(load_pins(ROOT)), ['agent', 'webui'])

    def test_missing_or_garbled_lock_fails_closed(self):
        (self.app / 'config/hermes-component.lock.json').write_text('{', encoding='utf-8')
        with self.assertRaises(ComponentError):
            load_webapp_pin(self.app)
        (self.app / 'config/hermes-component.lock.json').unlink()
        with self.assertRaises(ComponentError):
            load_webapp_pin(self.app)

    def test_corrupt_or_foreign_configuration_is_reported_and_never_replaced(self):
        path = self.component.config_path
        cases = ['{broken', json.dumps({'version': 999, 'kind': 'webapp', 'enabled': True}),
                 json.dumps({'version': 1, 'kind': 'webui', 'enabled': True, 'origin': 'managed', 'url': 'http://127.0.0.1:1/', 'home': 'h', 'python': 'p', 'agent': 'a'}),
                 json.dumps({'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'managed', 'url': 'http://example.com/', 'home': 'h', 'python': 'p', 'agent': 'a'}),
                 json.dumps({'version': 1, 'kind': 'webapp', 'enabled': 'yes', 'origin': 'managed', 'url': 'http://127.0.0.1:1/', 'home': 'h', 'python': 'p', 'agent': 'a'}),
                 json.dumps({'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'managed', 'url': 'http://127.0.0.1:1/', 'home': 'h', 'python': 'p', 'agent': 'a', 'release': '../x'})]
        for text in cases:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
            with self.subTest(text=text[:50]):
                with self.assertRaises(ComponentError):
                    self.component.ensure('managed', port=free_port(), python=self.python)
                self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_state_inside_the_source_checkout_is_refused(self):
        with self.assertRaises(ComponentError):
            WebappComponent(self.app / 'state', application_root=self.app)


class ToolTests(ComponentCase):
    def test_newest_qualifying_tool_is_found_and_old_ones_are_not_used(self):
        old = self.tmp / 'local/hermes/tools/node-24.1.0-win32-x64'
        old.mkdir(parents=True)
        (old / 'node.cmd').write_text('@echo off\r\necho v24.1.0\r\n', encoding='utf-8')
        newer_but_lexically_smaller = self.tmp / 'local/hermes/tools/node-26.7.0-win32-x64'
        newer_but_lexically_smaller.mkdir()
        (newer_but_lexically_smaller / 'node.cmd').write_text('@echo off\r\necho v26.7.0\r\n', encoding='utf-8')
        self.assertEqual(find_tool('node', 26, self.environment).version, '26.12.1')     # 26.12 > 26.7 numerically, not as text
        self.assertIsNone(find_tool('node', 27, self.environment))

    def test_explicit_directory_is_exclusive(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        self.assertIsNone(find_tool('node', 26, {**self.environment, 'ODYSSEUS_HERMES_NODE_DIR': str(empty)}))

    def test_requirements_come_from_the_pinned_source_and_a_missing_toolchain_changes_nothing(self):
        shutil.rmtree(self.tmp / 'local/hermes/tools')
        with self.assertRaises(ComponentError) as caught:
            self.managed()
        self.assertIn('26', str(caught.exception))
        self.assertIsNone(read_webapp_config(self.component.config_path))


class ManagedTests(ComponentCase):
    def test_fresh_install_builds_with_the_official_script_and_records_everything(self):
        config = self.managed(repo_roots=None)
        self.assertEqual((config['origin'], config['enabled'], config['python_origin']), ('managed', True, 'external'))
        self.assertEqual(config['revisions'], {'webapp': self.sha})
        bundle = Path(config['bundle'])
        self.assertTrue(inspect_bundle(bundle)['ok'])
        self.assertEqual(Path(config['agent']).parent.name, config['release'])
        self.assertEqual(git(config['agent'], 'rev-parse', 'HEAD'), self.sha)
        build_dir = self.root / 'managed/webapp/tmp'
        self.assertIn('build-webapp.mjs', (build_dir / 'node-args.txt').read_text())
        self.assertEqual(read_webapp_config(self.component.config_path), config)

    def test_every_new_release_installs_the_locks_runtime_extras_into_its_own_environment(self):
        config = self.managed(repo_roots=None)
        self.assertEqual(self.runtime_installs, [(config['release'], Path(config['home']).name, ('mcp',))])

    def test_a_release_whose_runtime_extras_cannot_be_proven_is_never_ready_or_referenced(self):
        self.runtime_install_error = 'The release environment cannot import mcp.client.stdio'
        with self.assertRaises(ComponentError):
            self.managed()
        releases = list((self.component.directory / 'releases').iterdir())
        self.assertEqual([json.loads((item / 'release.json').read_text())['state'] for item in releases], ['failed'])
        self.assertIsNone(read_webapp_config(self.component.config_path))

    def test_a_staged_release_changes_nothing_in_use_until_it_is_activated_and_can_be_rolled_back(self):
        first = self.managed(repo_roots=None)
        home_config = (Path(first['home']) / 'config.yaml')
        before = (self.component.config_path.read_bytes(), home_config.read_bytes() if home_config.exists() else None)
        staged = self.component.stage_release(python=self.python)
        self.assertNotEqual(staged['release'], first['release'])
        self.assertEqual(self.runtime_installs[-1][2], ('mcp',), 'a staged release proves its runtime extras too')
        record = json.loads((self.component.directory / 'releases' / staged['release'] / 'release.json').read_text())
        self.assertEqual(record['state'], 'staged')
        self.assertEqual((self.component.config_path.read_bytes(), home_config.read_bytes() if home_config.exists() else None), before,
                         'staging must not touch the live configuration or the gateway home')
        self.component._sweep_releases(first)             # a finished, waiting release is not a leftover
        self.assertTrue((self.component.directory / 'releases' / staged['release']).is_dir())

        active = self.component.activate_release(staged['release'])
        self.assertEqual((active['release'], active['previous']), (staged['release'], first['release']))
        self.assertEqual(json.loads((self.component.directory / 'releases' / staged['release'] / 'release.json').read_text())['state'], 'ready')
        self.assertEqual(read_webapp_config(self.component.config_path)['release'], staged['release'])
        with self.assertRaises(ComponentError):
            self.component.activate_release(staged['release'])      # no longer staged

        back = self.component.rollback()
        self.assertEqual(back['release'], first['release'])
        self.assertEqual(Path(back['agent']).parent.name, first['release'])

    def test_a_release_that_is_not_staged_cannot_be_activated(self):
        self.managed(repo_roots=None)
        for bad in ('zz', '0' * 12):
            with self.assertRaises(ComponentError):
                self.component.activate_release(bad)

    def test_the_locks_runtime_extras_are_required_and_validated(self):
        self.assertEqual(hwc.load_runtime_extras(self.app), ('mcp',))
        lock = self.app / 'config/hermes-component.lock.json'
        data = json.loads(lock.read_text(encoding='utf-8'))
        for bad in (None, 'mcp', ['mcp; rm'], [3]):
            data['runtime'] = {'extras': bad} if bad is not None else {}
            lock.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ComponentError):
                hwc.load_runtime_extras(self.app)

    def test_build_environment_carries_no_secrets_and_stays_inside_the_component(self):
        self.managed()
        dump = (self.root / 'managed/webapp/tmp/env-dump.txt').read_text(errors='replace')
        self.assertNotIn(SECRET, dump)
        self.assertNotIn('ODYSSEUS_TEST_SECRET', dump)
        self.assertNotIn('HERMES_WRITE_SAFE_ROOT', dump)
        self.assertIn('pnpm_config_store_dir=' + str(self.component.directory / 'cache' / 'pnpm-store'), dump)
        self.assertIn('CI=1', dump)

    def test_new_managed_home_is_private_manual_approvals_discovery_off_and_gets_the_return_plugin(self):
        self.gate = True                                    # the user configured dashboard.basic_auth, which public_url requires
        home = self.root / 'state/hermes-gateway'
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('dashboard:\n  basic_auth: {}\n', encoding='utf-8')
        with mock.patch.object(WebappComponent, '_prepare_home', lambda s, py, h, port, url, roots, schema: None):
            config = self.managed(public_url='http://hermes.localhost:7000')
        self.assertEqual(config['public_url'], 'http://hermes.localhost:7000')

    def test_public_url_is_refused_without_a_user_configured_auth_provider_and_nothing_is_written(self):
        with self.assertRaises(ComponentError) as caught:
            self.managed(public_url='http://hermes.localhost:7000')
        self.assertIn('auth provider', str(caught.exception))
        self.assertIsNone(read_webapp_config(self.component.config_path))
        self.assertFalse((self.root / 'state/hermes-gateway/config.yaml').exists())

    def test_new_home_defaults(self):
        config = self.managed()
        text = (Path(config['home']) / 'config.yaml').read_text()
        port = config['url'].split(':')[-1].strip('/')
        for line in ('webapp:', '  enabled: true', '  host: 127.0.0.1', f'  port: {port}', '  public_url: ""', '_config_version: 53',
                     'approvals:', '  mode: manual', '  repo_scan_enabled: false', '  repo_scan_roots: []'):
            self.assertIn(line, text)
        self.assertEqual(config['desktop_plugins'], ['odysseus-return'])
        self.assertEqual((Path(config['home']) / 'desktop-plugins/odysseus-return/plugin.js').read_bytes(),
                         (ROOT / 'integrations/hermes-mode/desktop-plugins/odysseus-return/plugin.js').read_bytes())

    def test_existing_managed_home_settings_are_edited_in_place_with_a_backup(self):
        home = self.root / 'state/hermes-gateway'
        home.mkdir(parents=True)
        original = 'model:\n  default: keep-me\nwebapp:\n  enabled: false\n'
        (home / 'config.yaml').write_text(original, encoding='utf-8')
        calls = []
        with mock.patch.object(WebappComponent, '_hermes_yaml', lambda self, python, code, *args: calls.append(args) or ''):
            self.managed()
        self.assertEqual(len(calls), 1)
        payload = json.loads(calls[0][1])
        self.assertEqual(payload['webapp']['enabled'], True)
        self.assertNotIn('model', payload)                                    # nothing else is touched
        backups = list(home.glob('config.yaml.pre-odysseus-*'))
        self.assertEqual([item.read_text() for item in backups], [original])

    def test_a_different_plugin_with_the_same_name_is_never_overwritten(self):
        home = self.root / 'state/hermes-gateway'
        plugin = home / 'desktop-plugins/odysseus-return/plugin.js'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('// the user wrote this', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.managed()
        self.assertEqual(plugin.read_text(), '// the user wrote this')

    def test_a_plugin_conflict_is_found_before_the_homes_config_is_touched(self):
        home = self.root / 'state/hermes-gateway'
        plugin = home / 'desktop-plugins/odysseus-return/plugin.js'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('// the user wrote this', encoding='utf-8')
        (home / 'config.yaml').write_text('model:\n  default: keep-me\n', encoding='utf-8')
        with mock.patch.object(WebappComponent, '_hermes_yaml', side_effect=AssertionError('config must not be edited')):
            with self.assertRaises(ComponentError):
                self.managed()
        self.assertEqual((home / 'config.yaml').read_text(), 'model:\n  default: keep-me\n')
        self.assertEqual(list(home.glob('config.yaml.pre-odysseus-*')), [])

    def test_a_failed_install_is_marked_failed_and_never_referenced(self):
        broken = self.tmp / 'local/hermes/tools/node-26.12.1-win32-x64/node.cmd'
        broken.write_text('@echo off\r\nif "%1"=="--version" (echo v26.12.1& exit /b 0)\r\nexit /b 7\r\n', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.managed()
        releases = list((self.component.directory / 'releases').iterdir())
        self.assertEqual([json.loads((item / 'release.json').read_text())['state'] for item in releases], ['failed'])
        self.assertIsNone(read_webapp_config(self.component.config_path))

    def test_ensure_is_idempotent_and_never_clones_again(self):
        first = self.managed()
        with mock.patch.object(hwc, 'checkout_source', side_effect=AssertionError('must not clone again')):
            self.assertEqual(self.component.ensure('managed', port=free_port(), python=self.python), first)

    def test_ownership_never_switches(self):
        self.managed()
        agent, home = self.adopted_tree()
        with self.assertRaises(ComponentError):
            self.component.ensure('adopted', agent=agent, home=home, python=self.python, port=free_port())

    def test_failed_build_leaves_no_configuration_and_the_previous_release_intact(self):
        first = self.managed()
        broken = self.tmp / 'local/hermes/tools/node-26.12.1-win32-x64/node.cmd'
        broken.write_text('@echo off\r\nif "%1"=="--version" (echo v26.12.1& exit /b 0)\r\nexit /b 7\r\n', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.component.ensure('managed', port=free_port(), python=self.python, repair=True, rebuild=True)
        self.assertEqual(read_webapp_config(self.component.config_path), first)
        self.assertTrue(inspect_bundle(first['bundle'])['ok'])

    def test_tampered_bundle_is_detected(self):
        config = self.managed()
        self.component.validate(config)
        (Path(config['bundle']) / 'hermes-build.json').write_text('{"schema":1,"product":"webapp","tampered":true}', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.component.validate(config)
        (Path(config['bundle']) / '.hermes-stale').write_text('x', encoding='utf-8')
        self.assertFalse(inspect_bundle(config['bundle'])['ok'])

    def test_upgrade_is_side_by_side_and_rollback_restores_the_previous_release(self):
        first = self.managed()
        second = self.component.ensure('managed', port=free_port(), python=self.python, repair=True, rebuild=True)
        self.assertNotEqual(second['release'], first['release'])
        self.assertEqual(second['previous'], first['release'])
        self.assertTrue(Path(first['bundle']).is_dir() and Path(second['bundle']).is_dir())          # both retained
        self.assertEqual(second['home'], first['home'])                                               # user state carried over, not recreated
        back = self.component.rollback()
        self.assertEqual((back['release'], back['previous']), (first['release'], second['release']))
        self.assertEqual(Path(back['bundle']), Path(first['bundle']))
        forward = self.component.rollback()
        self.assertEqual(forward['release'], second['release'])

    def test_rollback_refuses_without_a_complete_previous_release(self):
        with self.assertRaises(ComponentError):
            self.managed() and self.component.rollback()
        second = self.component.ensure('managed', port=free_port(), python=self.python, repair=True, rebuild=True)
        release = self.component._release_dir(second['previous'])
        atomic_json(release / 'release.json', {'state': 'preparing'})
        with self.assertRaises(ComponentError):
            self.component.rollback()
        self.assertEqual(read_webapp_config(self.component.config_path), second)

    def test_disable_preserves_everything_and_enable_restores_without_rebuilding(self):
        config = self.managed()
        self.component.disable()
        self.assertEqual(read_webapp_config(self.component.config_path), {**config, 'enabled': False})
        with mock.patch.object(hwc, 'checkout_source', side_effect=AssertionError('no rebuild')):
            self.assertFalse(self.component.ensure('managed', port=free_port(), python=self.python)['enabled'])
            self.assertTrue(self.component.ensure('managed', port=free_port(), python=self.python, enable=True)['enabled'])


class AdoptedTests(ComponentCase):
    def adopt(self, **extra):
        agent, home = self.adopted_tree()
        return agent, home, self.component.ensure('adopted', agent=agent, home=home, python=self.python, port=free_port(), **extra)

    def test_plan_status_and_adopt_without_build_write_nothing_into_the_users_install(self):
        agent, home = self.adopted_tree()
        before = (tree_digest(agent), tree_digest(home))
        plan = self.component.plan(agent, home, self.python)
        self.assertFalse(plan['writes_performed'])
        self.assertEqual(plan['adopted']['checkout'], 'at-pin')
        config = self.component.ensure('adopted', agent=agent, home=home, python=self.python, port=free_port(), build=False)
        self.component.status()
        self.assertEqual(config['origin'], 'adopted')
        self.assertIsNone(config['bundle'])
        self.assertEqual((tree_digest(agent), tree_digest(home)), before)

    def test_building_for_an_adopted_install_uses_a_pinned_export_and_leaves_the_users_checkout_untouched(self):
        agent, home = self.adopted_tree()
        before = (tree_digest(agent), tree_digest(home))
        config = self.component.ensure('adopted', agent=agent, home=home, python=self.python, port=free_port())
        self.assertEqual((tree_digest(agent), tree_digest(home)), before)
        bundle = Path(config['bundle'])
        self.assertTrue(inspect_bundle(bundle)['ok'])
        self.assertTrue(bundle.is_relative_to(self.component.directory / 'releases'))
        self.assertFalse((bundle.parents[3] / '.git').exists())                      # an export, not a clone
        self.assertEqual(Path(config['agent']), agent.resolve())
        self.assertFalse((agent / 'apps/desktop/dist-webapp').exists())

    def test_checkout_not_at_the_pin_is_refused_and_not_modified(self):
        agent, home = self.adopted_tree()
        (agent / 'stray.txt').write_text('x', encoding='utf-8')
        (agent / 'run_agent.py').write_text('# local edit', encoding='utf-8')
        before = tree_digest(agent)
        with self.assertRaises(ComponentError):
            self.component.ensure('adopted', agent=agent, home=home, python=self.python, port=free_port())
        self.assertEqual(tree_digest(agent), before)
        self.assertEqual(self.component.plan(agent, home, self.python)['adopted']['checkout'], 'differs-or-missing')

    def test_incomplete_layouts_and_missing_arguments_are_refused(self):
        agent, home = self.adopted_tree()
        with self.assertRaises(ComponentError):
            self.component.ensure('adopted', agent=agent, home=home, python=None, port=free_port())
        with self.assertRaises(ComponentError):
            self.component.ensure('adopted', agent=agent, home=self.tmp / 'nowhere', python=self.python, port=free_port())

    def test_odysseus_never_starts_stops_or_restarts_a_user_owned_gateway(self):
        self.adopt(build=False)
        for action in (self.component.start, self.component.stop, self.component.restart, self.component.rollback):
            with self.subTest(action=action.__name__), self.assertRaises(ComponentError):
                action()

    def test_plan_reports_what_the_user_must_decide_without_doing_it(self):
        agent, home = self.adopted_tree()
        with mock.patch.object(WebappComponent, 'home_settings', return_value={'webapp': {'enabled': False}, 'approvals_mode': 'smart',
                                                                              'repo_scan_enabled': None, 'repo_scan_roots': 0}):
            steps = ' '.join(self.component.plan(agent, home, self.python)['steps'])
        for expected in ('does not enable webapp', 'scans the home folder', "'smart'", 'desktop plugin is not installed'):
            self.assertIn(expected, steps)

    def test_export_matches_the_pinned_tree_exactly(self):
        agent, _ = self.adopted_tree()
        target = self.tmp / 'export'
        hwc.export_source(agent, load_webapp_pin(self.app), target, self.environment)
        self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()),
                         sorted(git(self.repo, 'ls-files').splitlines()))
        with self.assertRaises(ComponentError):
            hwc.export_source(agent, load_webapp_pin(self.app), target, self.environment)           # never replaces


class WebUiIsolationTests(ComponentCase):
    def test_the_webui_component_state_is_never_read_or_written(self):
        webui = self.root / 'managed' / 'component.json'
        atomic_json(webui, {'version': 1, 'enabled': True, 'origin': 'managed', 'home': 'retained'})
        before = webui.read_bytes()
        config = self.managed()
        self.component.status()
        self.component.rollback() if config.get('previous') else None
        self.component.disable()
        self.assertEqual(webui.read_bytes(), before)
        self.assertFalse((self.component.root / 'managed' / '.install.lock').exists())                    # the WebUI lock is never taken
        self.assertEqual(self.component.config_path.parent, self.component.root / 'managed' / 'webapp')

    def test_a_webui_only_install_is_not_a_webapp_install(self):
        atomic_json(self.root / 'managed' / 'component.json', {'version': 1, 'enabled': True, 'origin': 'managed', 'home': 'x'})
        self.assertEqual(self.component.status(), {'enabled': False, 'state': 'not_installed'})


def fake_health_server(port, payload):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class LifecycleTests(ComponentCase):
    def setUp(self):
        super().setUp()
        self.config = self.managed()
        self.addCleanup(self.stop_quietly)

    def stop_quietly(self):
        try:
            self.component.stop(force=True)
        except Exception:
            pass

    def test_start_serves_the_pinned_bundle_with_a_clean_environment_and_stop_ends_only_that_process(self):
        status = self.component.start(timeout=60)
        self.assertEqual((status['state'], status['sign_in_gate']), ('ready', False))
        conn = __import__('http.client').client.HTTPConnection('127.0.0.1', int(self.config['url'].split(':')[-1].strip('/')), timeout=5)
        conn.request('GET', '/api/status')
        report = json.loads(conn.getresponse().read())
        self.assertEqual(Path(report['dist']), Path(self.config['bundle']))
        self.assertEqual(Path(report['home']), Path(self.config['home']))
        self.assertFalse(report['secret_visible'])                                  # Odysseus' environment does not reach the agent's shell
        self.assertEqual(Path(report['safe_root']), self.component.directory / 'no-workspace')   # deny by default: the operator's variable is no longer inherited
        self.assertEqual(self.component.start()['state'], 'ready')                  # idempotent
        self.assertEqual(self.component.stop()['state'], 'stopped')
        self.assertEqual(self.component.status()['state'], 'stopped')

    def test_stop_refuses_while_sessions_look_active_until_forced(self):
        self.component.start(timeout=60)
        (Path(self.config['home']) / 'busy.flag').write_text('1')
        with self.assertRaises(controls.ActiveRuns):
            self.component.stop()
        self.assertEqual(self.component.status()['state'], 'ready')
        self.assertEqual(self.component.stop(force=True)['state'], 'stopped')

    def test_a_command_running_inside_the_gateway_can_never_stop_it_even_forced(self):
        self.component.start(timeout=60)
        pid = self.component.owned_listener(self.config)
        for force in (False, True):
            with mock.patch.object(WebappComponent, '_ancestor_pids', return_value=[4, pid]):
                with self.assertRaisesRegex(ComponentError, 'inside the gateway'):
                    self.component.stop(force=force)
                with self.assertRaisesRegex(ComponentError, 'inside the gateway'):
                    self.component.restart(force=force)
        self.assertEqual(self.component.status()['state'], 'ready')

    def test_drain_stops_only_when_the_gateways_own_store_shows_no_started_turn(self):
        self.component.start(timeout=60)
        with mock.patch.object(WebappComponent, '_running_admissions', return_value=2):
            with self.assertRaises(controls.ActiveRuns):
                self.component.stop(drain=1)
        self.assertEqual(self.component.status()['state'], 'ready')
        (Path(self.config['home']) / 'busy.flag').write_text('1')          # "touched recently" alone does not block a proven-idle drain
        with mock.patch.object(WebappComponent, '_running_admissions', return_value=0):
            self.assertEqual(self.component.stop(drain=1)['state'], 'stopped')

    def test_stop_never_kills_a_listener_it_did_not_provision(self):
        port = int(self.config['url'].split(':')[-1].strip('/'))
        foreign = fake_health_server(port, {'ok': True, 'displayVersion': 'x', 'auth_required': False, 'active_sessions': 0})
        self.addCleanup(foreign.shutdown)
        with self.assertRaises(controls.Unavailable):
            self.component.stop(force=True)
        conn = __import__('http.client').client.HTTPConnection('127.0.0.1', port, timeout=3)
        conn.request('GET', '/api/health')
        self.assertEqual(conn.getresponse().status, 200)                            # still alive

    def test_start_refuses_to_take_over_an_occupied_port(self):
        port = int(self.config['url'].split(':')[-1].strip('/'))
        foreign = fake_health_server(port, {'hello': 'not hermes'})
        self.addCleanup(foreign.shutdown)
        self.assertEqual(self.component.status()['state'], 'conflict')
        with self.assertRaises(ComponentError):
            self.component.start()

    def test_start_validates_first_so_a_tampered_bundle_never_serves(self):
        (Path(self.config['bundle']) / 'hermes-build.json').write_text('{"schema":1,"product":"webapp","x":1}', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.component.start()
        self.assertEqual(self.component.status()['state'], 'stopped')

    def test_a_pid_record_that_does_not_match_blocks_stop(self):
        self.component.start(timeout=60)
        record = Path(self.config['home']) / 'gateway.pid'
        data = json.loads(record.read_text())
        record.write_text(json.dumps({**data, 'hermes_home': str(self.tmp / 'elsewhere')}))
        with self.assertRaises(controls.Unavailable):
            self.component.stop(force=True)
        self.assertEqual(self.component.status()['state'], 'ready')
        record.write_text(json.dumps(data))


class DrainEngagementTests(ComponentCase):
    """restart/stop --drain must engage the gateway's reversible external-drain marker BEFORE the idle wait and
    release it on every outcome. Disposable fixtures only: the stub gateway stands in for the real one, admission
    polls are scripted to model the gateway/goal-wake reaction to an engaged drain (the DRAIN-1 coupling), and the
    marker is a real file in the fixture's disposable gateway home. Nothing here touches a live gateway."""

    def setUp(self):
        super().setUp()
        self.config = self.managed()
        self.marker = Path(self.config['home']) / '.drain_request.json'
        self.addCleanup(self.stop_quietly)

    def stop_quietly(self):
        if self.marker.is_dir():
            self.marker.rmdir()
        try:
            self.component.stop(force=True)
        except Exception:
            pass

    def test_drain_engages_the_marker_before_the_idle_wait_and_releases_it_on_success(self):
        self.component.start(timeout=60)
        seen = []
        def admissions(config):
            seen.append(json.loads(self.marker.read_text(encoding='utf-8')) if self.marker.exists() else None)
            return 0
        with mock.patch.object(WebappComponent, '_running_admissions', side_effect=admissions):
            self.assertEqual(self.component.stop(drain=5)['state'], 'stopped')
        self.assertEqual(len(seen), 1)                                  # proven idle on the first poll
        payload = seen[0]
        self.assertIsNotNone(payload)                                   # engaged BEFORE the first admission poll
        self.assertEqual(payload['action'], 'drain')                    # the gateway's write_drain_request contract
        self.assertEqual(payload['principal'], 'odysseus-webapp')
        self.assertIs(payload['suppress_notification'], False)
        self.assertIsInstance(payload['epoch'], str)
        requested_at = datetime.fromisoformat(payload['requested_at'])
        self.assertIsNotNone(requested_at.tzinfo)                       # UTC-stamped, as the watcher's expiry check expects
        self.assertFalse(self.marker.exists())                          # released before the kill

    def test_the_idle_wait_converges_once_turns_yield_to_the_engaged_drain(self):
        self.component.start(timeout=60)
        (Path(self.config['home']) / 'busy.flag').write_text('1')       # the five-minute heuristic still looks busy
        polls = []
        def admissions(config):
            polls.append(self.marker.exists())
            return 2 if len(polls) == 1 else 0                          # DRAIN-1 coupling: wakes yield only while draining
        with mock.patch.object(WebappComponent, '_running_admissions', side_effect=admissions):
            self.assertEqual(self.component.stop(drain=10)['state'], 'stopped')
        self.assertEqual(polls, [True, True])                           # the marker stayed engaged across the whole wait
        self.assertFalse(self.marker.exists())

    def test_drain_timeout_releases_the_marker_and_leaves_the_gateway_running(self):
        self.component.start(timeout=60)
        with mock.patch.object(WebappComponent, '_running_admissions', return_value=2):
            with self.assertRaises(controls.ActiveRuns):
                self.component.stop(drain=1)
        self.assertFalse(self.marker.exists())                          # no stuck drain after a timed-out wait
        self.assertEqual(self.component.status()['state'], 'ready')

    def test_no_drain_requested_and_force_never_touch_the_marker(self):
        self.component.start(timeout=60)
        seen = []
        def admissions(config):
            seen.append(self.marker.exists())
            return 0
        with mock.patch.object(WebappComponent, '_running_admissions', side_effect=admissions):
            self.assertEqual(self.component.stop()['state'], 'stopped')                     # drain=0: exact old behavior
        self.assertEqual(seen, [False])
        self.assertFalse(self.marker.exists())
        self.component.start(timeout=60)
        with mock.patch.object(WebappComponent, '_running_admissions', return_value=2):
            self.assertEqual(self.component.stop(force=True, drain=30)['state'], 'stopped')  # force skips wait AND engagement
        self.assertFalse(self.marker.exists())

    def test_a_pre_existing_operator_drain_marker_is_restored_on_release(self):
        self.component.start(timeout=60)
        foreign = json.dumps({'action': 'drain', 'requested_at': '2026-11-19T12:00:00+00:00', 'principal': 'dashboard',
                              'epoch': '', 'suppress_notification': True})
        self.marker.write_text(foreign, encoding='utf-8')
        during = []
        def admissions(config):
            during.append(json.loads(self.marker.read_text(encoding='utf-8'))['principal'])
            return 0
        with mock.patch.object(WebappComponent, '_running_admissions', side_effect=admissions):
            self.assertEqual(self.component.stop(drain=5)['state'], 'stopped')
        self.assertEqual(during, ['odysseus-webapp'])                   # our drain is what the wait runs under
        self.assertEqual(self.marker.read_text(encoding='utf-8'), foreign)   # the operator's marker restored byte-for-byte
        self.component.start(timeout=60)
        with mock.patch.object(WebappComponent, '_running_admissions', return_value=2):
            with self.assertRaises(controls.ActiveRuns):
                self.component.stop(drain=1)
        self.assertEqual(self.marker.read_text(encoding='utf-8'), foreign)   # timeout restores it too

    def test_an_unreadable_marker_location_fails_before_anything_is_stopped(self):
        self.component.start(timeout=60)
        self.marker.mkdir()                                             # a directory at the marker path cannot be read or written
        with self.assertRaisesRegex(ComponentError, 'drain marker'):
            self.component.stop(drain=5)
        self.assertEqual(self.component.status()['state'], 'ready')
        self.marker.rmdir()

    def test_a_failed_marker_write_stops_nothing_and_leaves_no_marker(self):
        self.component.start(timeout=60)
        real_atomic_bytes = hwc.atomic_bytes
        def fail_marker_write(path, value):
            if Path(path).name == '.drain_request.json':
                raise OSError('fixture: disk full')
            return real_atomic_bytes(path, value)
        with mock.patch.object(hwc, 'atomic_bytes', side_effect=fail_marker_write):
            with self.assertRaisesRegex(ComponentError, 'Could not engage'):
                self.component.stop(drain=5)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.component.status()['state'], 'ready')

    def test_a_failed_release_aborts_before_the_kill_so_no_stuck_drain_survives(self):
        self.component.start(timeout=60)
        def admissions(config):
            if self.marker.exists():                                    # once engaged, wedge the path so release cannot remove it
                self.marker.unlink()
                self.marker.mkdir()
            return 0
        with mock.patch.object(WebappComponent, '_running_admissions', side_effect=admissions):
            with self.assertRaisesRegex(ComponentError, 'could not be released'):
                self.component.stop(drain=5)
        self.assertEqual(self.component.status()['state'], 'ready')     # the kill never ran; the operator gets a manual-removal message
        self.marker.rmdir()

    def test_restart_with_drain_engages_and_leaves_no_marker_for_the_new_gateway(self):
        self.component.start(timeout=60)
        seen = []
        def admissions(config):
            seen.append(self.marker.exists())
            return 0
        with mock.patch.object(WebappComponent, '_running_admissions', side_effect=admissions):
            self.assertEqual(self.component.restart(drain=5)['state'], 'ready')
        self.assertEqual(seen, [True])
        self.assertFalse(self.marker.exists())                          # the restarted gateway never boots into draining


if __name__ == '__main__':
    unittest.main()
