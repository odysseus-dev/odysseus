"""Windows-only, optional Hermes provisioning; no application-state fusion."""
import hashlib
import os
import json
import re
import subprocess
import tempfile
import shutil
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import uuid


_ALLOWED_REPOSITORIES = {'agent': 'NousResearch/hermes-agent', 'webui': 'nesquena/hermes-webui'}


@dataclass(frozen=True)
class SourcePin:
    name: str
    repository: str
    revision: str

    def __post_init__(self):
        if _ALLOWED_REPOSITORIES.get(self.name) != self.repository or not re.fullmatch(r'[0-9a-f]{40}', self.revision):
            raise ComponentError('Hermes sources require an approved repository and immutable full commit.')

    @property
    def url(self):
        return 'https://github.com/' + self.repository + '.git'


def _git(path, *args, check=True):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=60)
    if check and result.returncode:
        raise ComponentError('Cannot verify the Hermes source checkout.')
    return result


_PATCH_FLAGS = ('apply', '--ignore-whitespace', '--whitespace=nowarn')
_PATCHES = {'webui': 'integrations/hermes-mode/patches/webui-trusted-workspaces.patch'}


def patch_file(application_root, name):
    relative = _PATCHES.get(name)
    path = (Path(application_root) / relative).resolve() if relative else None
    return path if path and path.is_file() else None


def _patch_files(patch):
    return {line[6:].strip() for line in Path(patch).read_text(encoding='utf-8').splitlines() if line.startswith('+++ b/')}


def _patch_list(patch):
    """One reviewed patch (a path) or an ordered sequence of them, as a list; empty for none."""
    if not patch:
        return []
    return [Path(patch)] if isinstance(patch, (str, os.PathLike)) else [Path(item) for item in patch]


def _sequence_matches(path, patches, allowed):
    """True when the working tree is exactly the pin plus the whole patch sequence applied in order. Patches that touch the same file cannot each be reversed
    on the final tree, so the expected tree is rebuilt in a temporary index and compared with the tree on disk. Read-only for the source: the index files and any
    new git objects live in a temporary folder (the source's own object store is only an alternate)."""
    git_dir = _git(path, 'rev-parse', '--absolute-git-dir', check=False).stdout.strip()
    if not git_dir:
        return False
    scratch = tempfile.mkdtemp(prefix='odysseus-patch-check-')
    try:
        base = {**os.environ, 'GIT_OBJECT_DIRECTORY': str(Path(scratch) / 'objects'), 'GIT_ALTERNATE_OBJECT_DIRECTORIES': str(Path(git_dir) / 'objects')}
        (Path(scratch) / 'objects').mkdir()

        def run(index, *args):
            return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=120, env={**base, 'GIT_INDEX_FILE': str(Path(scratch) / index)})
        if run('expected', 'read-tree', 'HEAD').returncode:
            return False
        for item in patches:
            if run('expected', 'apply', '--cached', *_PATCH_FLAGS[1:], str(item)).returncode:
                return False
        # Only the files the patches name are compared (hashing a whole source tree would be slow and pointless); the caller has already shown that nothing else changed.
        if run('actual', 'read-tree', 'HEAD').returncode or run('actual', 'add', '--all', '--', *sorted(allowed)).returncode:
            return False
        trees = [run(index, 'write-tree') for index in ('expected', 'actual')]
        return all(t.returncode == 0 for t in trees) and trees[0].stdout.strip() == trees[1].stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def patch_state(path, patch):
    """'absent' (clean at the pin), 'applied' (exactly the reviewed patch, or the whole reviewed sequence), or 'foreign' (anything else)."""
    if _git(path, 'rev-parse', 'HEAD').stdout.strip() and not _git(path, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        return 'absent'
    patches = _patch_list(patch)
    if patches and all(_git(path, *_PATCH_FLAGS, '-R', '--check', str(item), check=False).returncode == 0 for item in patches):
        allowed = set().union(*(_patch_files(item) for item in patches))
        changed = {line.strip() for line in _git(path, 'diff', '--name-only').stdout.splitlines()}
        untracked = {line.strip() for line in _git(path, 'ls-files', '--others', '--exclude-standard').stdout.splitlines() if line.strip()}
        if changed <= allowed and untracked <= allowed:
            return 'applied'
    if len(patches) > 1:
        allowed = set().union(*(_patch_files(item) for item in patches))
        changed = {line.strip() for line in _git(path, 'diff', '--name-only').stdout.splitlines()}
        untracked = {line.strip() for line in _git(path, 'ls-files', '--others', '--exclude-standard').stdout.splitlines() if line.strip()}
        if changed <= allowed and untracked <= allowed and _sequence_matches(path, patches, allowed):
            return 'applied'
    return 'foreign'


def verify_checkout(path, pin, patch=None):
    path = Path(path)
    if _git(path, 'rev-parse', 'HEAD').stdout.strip() != pin.revision or patch_state(path, patch) == 'foreign':
        raise ComponentError('Hermes source differs from the reviewed pin; it will not be overwritten.')


def apply_patch(path, pin, patch):
    """Apply the reviewed patch to a checkout that is clean at the pin. Idempotent; refuses anything else."""
    patches = _patch_list(patch)
    if not patches:
        return 'none'
    verify_checkout(path, pin, patches)
    if patch_state(path, patches) == 'applied':
        return 'applied'
    applied = 0
    for item in patches:                       # in order: a later patch is checked against the tree the earlier ones produced
        if _git(path, *_PATCH_FLAGS, '--check', str(item), check=False).returncode:
            if applied:
                raise ComponentError(
                    'The reviewed Hermes patch does not apply to this source; '
                    f'{applied} of {len(patches)} patches were applied before the refusal at patch {applied + 1}. '
                    f'The checkout is left partially patched (patch state: {patch_state(path, patches)}) and fails verification; it will not be used.')
            raise ComponentError('The reviewed Hermes patch does not apply to this source; nothing was changed.')
        _git(path, *_PATCH_FLAGS, str(item))
        applied += 1
    verify_checkout(path, pin, patches)
    return 'applied'


class ComponentError(RuntimeError):
    pass


def atomic_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2) + '\n').encode('utf-8'))


def safe_directory(path, create=False):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ComponentError('Component paths must not traverse symlinks or Windows junctions.')
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def register_return_extension(state_dir, assets_dir, odysseus_url, extension_dir=None):
    from src.application_modes import ModeConfig
    ModeConfig(odysseus_url)
    state_dir = safe_directory(state_dir, create=True)
    root = safe_directory(extension_dir or state_dir / 'extensions', create=True)
    registry = state_dir / 'extension-install-manifest.json'
    before = registry.read_bytes() if registry.exists() else None
    try:
        if before is not None and len(before) > 128 * 1024:
            raise ValueError('oversized')
        data = json.loads(before) if before is not None else {'version': 1, 'installed': {}}
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('installed'), dict):
            raise ValueError('schema')
    except (ValueError, RecursionError) as exc:
        raise ComponentError('Existing extension registry is unreadable; it was not replaced.') from exc
    package = safe_directory(root / 'odysseus-mode')
    owner = package / 'odysseus-owner.json'
    assets = {name: (Path(assets_dir) / name).read_bytes() for name in ('return-to-odysseus.js', 'modes.css')}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in assets.items()}
    if package.exists():
        try:
            ownership = json.loads(owner.read_text(encoding='utf-8'))
            if ownership.get('owner') != 'odysseus-hermes-mode' or not isinstance(ownership.get('hashes'), dict):
                raise ValueError('owner')
            for name in assets:
                target = package / name
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() not in {ownership['hashes'].get(name), hashes[name]}:
                    raise ValueError('modified assets')
        except (OSError, ValueError, AttributeError) as exc:
            raise ComponentError('An unowned or customized return package exists; it was not overwritten.') from exc
    else:
        package.mkdir()
        atomic_json(owner, {'owner': 'odysseus-hermes-mode', 'hashes': hashes})
    for name, value in assets.items():
        target = package / name
        if not target.exists() or target.read_bytes() != value:
            atomic_bytes(target, value)
    atomic_json(package / 'connection.json', {'odysseus_url': odysseus_url})
    atomic_json(package / 'manifest.json', {'id': 'odysseus-mode', 'name': 'Odysseus Mode', 'scripts': ['return-to-odysseus.js'], 'stylesheets': ['modes.css']})
    atomic_json(owner, {'owner': 'odysseus-hermes-mode', 'hashes': hashes})
    data['installed']['odysseus-mode'] = {'version': '1', 'files': list(assets) + ['connection.json', 'manifest.json', 'odysseus-owner.json'], 'installed_at': data['installed'].get('odysseus-mode', {}).get('installed_at', 'odysseus-managed')}
    after = registry.read_bytes() if registry.exists() else None
    if after != before:
        raise ComponentError('Extension registry changed concurrently; retry registration without replacing it.')
    atomic_json(registry, data)
    return {'extension_dir': str(root), 'package': str(package)}


def read_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError('oversized')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or type(data.get('enabled')) is not bool:
            raise ValueError('schema')
        return data
    except (OSError, ValueError, RecursionError) as exc:
        raise ComponentError('Component configuration is unreadable or unsupported; it was not replaced.') from exc


def probe_python(python, require_agent=False):
    script = 'import sys,json;print(json.dumps({"version":list(sys.version_info[:3]),"executable":sys.executable}))'
    if require_agent:
        script = 'import openai,ruamel.yaml,cryptography,psutil;assert sys.version_info[:2] == (3,14);' + script
        script = 'import sys;' + script
    try:
        result = subprocess.run([str(python), '-I', '-B', '-c', script], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError('probe failed')
        info = json.loads(result.stdout)
        if tuple(info['version'][:2]) < (3, 11):
            raise ValueError('unsupported Python')
        return info
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        raise ComponentError('Python is missing, unsupported or lacks Hermes dependencies; run component repair.') from exc


def plan_existing(webui, agent, home, python, pins, state_dir=None, application_root=None):
    webui, agent, home = (Path(value).resolve() for value in (webui, agent, home))
    for name, path in [('webui', webui), ('agent', agent)]:
        verify_checkout(path, pins[name], patch_file(application_root, name) if application_root else None)
    if not (webui / 'server.py').is_file() or not (agent / 'run_agent.py').is_file():
        raise ComponentError('Existing Hermes layout is incomplete; it was not modified.')
    probe_python(python, require_agent=True)
    return {
        'version': 1, 'enabled': True, 'origin': 'external',
        'webui': str(webui), 'agent': str(agent), 'python': str(python),
        'home': str(home), 'state_dir': str(Path(state_dir).resolve() if state_dir else home / 'webui'),
        'revisions': {name: pin.revision for name, pin in pins.items()},
    }


def load_pins(application_root):
    try:
        data = json.loads((Path(application_root) / 'config/hermes-component.lock.json').read_text(encoding='utf-8'))
        if data.get('version') != 1:
            raise ValueError('schema')
        return {name: SourcePin(name, data[name]['repository'], data[name]['revision']) for name in _ALLOWED_REPOSITORIES}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ComponentError('Hermes component pins are missing or invalid.') from exc


def private_directory(path):
    path = safe_directory(path, create=True)
    marker = path / '.odysseus-private'
    if marker.exists():
        return path
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
        match = re.search(r'S-1-5-[0-9-]+', identity)
        if not match:
            raise ComponentError('Cannot establish private Windows component permissions.')
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*' + match.group(0) + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], check=True, capture_output=True)
    else:
        path.chmod(0o700)
    marker.write_text('Odysseus private component state\n', encoding='utf-8')
    return path


@contextmanager
def component_lock(directory):
    directory = private_directory(directory)
    with (directory / '.install.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ComponentError('Another Hermes installer is already running; no duplicate installation started.') from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_install(command, log_path, environment, cwd=None):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('ab') as log:
        try:
            result = subprocess.run(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, timeout=1800,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComponentError('Hermes setup failed or timed out; inspect the private component install log.') from exc
    if result.returncode:
        raise ComponentError('Hermes setup failed; inspect the private component install log. Existing state was retained.')


def checkout_source(target, pin, log_path, environment, patch=None):
    git = shutil.which('git')
    if not git:
        raise ComponentError('Git for Windows is required to provision verified Hermes sources.')
    target = Path(target)
    if target.exists():
        raise ComponentError('A source destination already exists; it will not be replaced.')
    env = {**environment, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never', 'GIT_LFS_SKIP_SMUDGE': '1'}
    run_install([git, '-c', 'core.longpaths=true', 'clone', '--filter=blob:none', '--no-checkout', '--no-tags', pin.url, str(target)], log_path, env)
    run_install([git, '-C', str(target), '-c', 'core.longpaths=true', '-c', 'core.hooksPath=' + str(target / '.git/no-hooks'), 'checkout', '--detach', pin.revision], log_path, env)
    run_install([git, '-C', str(target), 'fsck', '--strict'], log_path, env)
    verify_checkout(target, pin)
    apply_patch(target, pin, patch)


class ComponentManager:
    def __init__(self, root, application_root=None, environment=None):
        self.root = safe_directory(root)
        self.application_root = Path(application_root).resolve() if application_root else Path(__file__).resolve().parents[1]
        if application_root and self.root.is_relative_to(self.application_root):
            raise ComponentError('Persistent Hermes component state must be outside the Odysseus source checkout.')
        self.environment = dict(os.environ if environment is None else environment)
        self.config_path = self.root / 'managed' / 'component.json'

    def disable(self):
        with component_lock(self.config_path.parent):
            config = read_config(self.config_path) or {'version': 1, 'enabled': True}
            atomic_json(self.config_path, {**config, 'enabled': False})

    def ensure(self, repair=False, fresh=False, existing=None, enable=False):
        with component_lock(self.config_path.parent):
            config = read_config(self.config_path)
            if config and not config['enabled'] and not enable:
                return config
            if fresh and config:
                raise ComponentError('Fresh provisioning cannot replace an existing component configuration.')
            if config and not repair:
                self.validate(config)
                self.apply_patches(config)
                if enable and not config['enabled']:
                    config = {**config, 'enabled': True}
                    atomic_json(self.config_path, config)
                return config
            candidate = existing if existing is not None else self._install_generation(config)
            pins = load_pins(self.application_root)
            self.validate(candidate, require_extension=False)
            self.apply_patches(candidate)
            from src.application_modes import ModeConfig
            odysseus_url = self.environment.get('ODYSSEUS_MODE_RETURN_URL', 'http://127.0.0.1:7000/')
            hermes_url = self.environment.get('ODYSSEUS_HERMES_MODE_URL', 'http://127.0.0.1:8787/')
            ModeConfig(odysseus_url)
            ModeConfig(hermes_url)
            result = register_return_extension(candidate['state_dir'], self.application_root / 'integrations/hermes-mode', odysseus_url, candidate.get('extension_dir'))
            candidate = {**candidate, 'version': 1, 'enabled': True, 'url': hermes_url,
                         'return_url': odysseus_url, 'extension_dir': result['extension_dir'],
                         'extension_package': result['package'], 'revisions': {name: pin.revision for name, pin in pins.items()}}
            atomic_json(self.config_path, candidate)
            return candidate

    def apply_patches(self, config):
        """Bring each checkout to the reviewed pin plus the reviewed patch, only ever from a clean pin."""
        pins = load_pins(self.application_root)
        return {name: apply_patch(safe_directory(config[name]), pins[name], patch_file(self.application_root, name)) for name in pins}

    def refresh_extension(self):
        """Re-register the packaged return extension for the saved component; changes nothing else."""
        with component_lock(self.config_path.parent):
            config = read_config(self.config_path)
            if not config or not config['enabled']:
                raise ComponentError('Hermes mode is disabled or not installed.')
            self.validate(config, require_extension=False)
            result = register_return_extension(config['state_dir'], self.application_root / 'integrations/hermes-mode',
                                               config['return_url'], config.get('extension_dir'))
            config = {**config, 'extension_dir': result['extension_dir'], 'extension_package': result['package']}
            atomic_json(self.config_path, config)
            return config

    def validate(self, config, require_extension=True):
        pins = load_pins(self.application_root)
        try:
            for name in pins:
                verify_checkout(safe_directory(config[name]), pins[name], patch_file(self.application_root, name))
            probe_python(config['python'], require_agent=True)
            for name in ('home', 'state_dir'):
                path = safe_directory(config[name])
                if path.is_relative_to(self.application_root):
                    raise ComponentError('Hermes user state must not be stored under the source checkout.')
            if require_extension:
                package = safe_directory(config['extension_package'])
                if not (package / 'manifest.json').is_file() or not (package / 'connection.json').is_file():
                    raise ComponentError('The Hermes return extension is missing; run component repair.')
        except (KeyError, TypeError) as exc:
            raise ComponentError('Hermes component configuration is incomplete; run component repair.') from exc

    def _install_generation(self, previous=None):
        pins = load_pins(self.application_root)
        generation = private_directory(self.root / 'managed/releases' / uuid.uuid4().hex[:12])
        log = generation / 'install.log'
        atomic_json(generation / 'installation.json', {'state': 'preparing', 'pins': {name: pin.revision for name, pin in pins.items()}})
        home = safe_directory(previous['home'] if previous and previous.get('home') else self.root / 'state/hermes')
        state_dir = safe_directory(previous['state_dir'] if previous and previous.get('state_dir') else home / 'webui')
        env = {**self.environment, 'HERMES_HOME': str(home), 'HERMES_WEBUI_STATE_DIR': str(state_dir),
               'HERMES_RUNTIME_DIR': str(self.root / 'managed/runtime')}
        seed = self.environment.get('HERMES_WEBUI_PYTHON') or sys.executable
        probe_python(seed)
        for name, pin in pins.items():
            checkout_source(generation / name, pin, log, env, patch_file(self.application_root, name))
        agent = generation / 'agent'
        run_install([seed, '-B', '-m', 'pm.build_env', '--source', str(agent), '--out', str(agent / 'venv'),
                     '--cache', str(self.root / 'managed/cache')], log, env, cwd=agent)
        python = agent / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
        probe_python(python, require_agent=True)
        private_directory(home)
        private_directory(state_dir)
        config = {'version': 1, 'enabled': True, 'origin': 'managed', 'webui': str(generation / 'webui'),
                  'agent': str(agent), 'python': str(python), 'home': str(home), 'state_dir': str(state_dir),
                  'runtime_dir': env['HERMES_RUNTIME_DIR']}
        atomic_json(generation / 'installation.json', {'state': 'ready', 'pins': {name: pin.revision for name, pin in pins.items()}})
        return config
