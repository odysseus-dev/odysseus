"""Local-only runtime ownership: reuse an external service, never stop it."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from src.application_modes import ModeConfig, ModeService, webui_start_command
from src.hermes_component import ComponentError, ComponentManager, component_lock, load_pins, plan_existing, probe_python, read_config, verify_checkout


def operator_environment():
    """Read existing user settings if absent from this process; never persist changes."""
    environment = dict(os.environ)
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                for name in ('HERMES_WRITE_SAFE_ROOT', 'HERMES_WEBUI_PYTHON', 'HERMES_HOME', 'HERMES_WEBUI_AGENT_DIR'):
                    if name not in environment:
                        try:
                            value, kind = winreg.QueryValueEx(key, name)
                            if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                                environment[name] = os.path.expandvars(value)
                        except FileNotFoundError:
                            continue
        except FileNotFoundError:
            pass
    return environment


def resolve_agent_python(agent, seed, environment):
    code = ('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
            'from pm.environments import selected_venv;'
            'p=selected_venv(Path(sys.argv[1]));'
            'print(p/("Scripts/python.exe" if sys.platform=="win32" else "bin/python"))')
    try:
        result = subprocess.run([str(seed), '-I', '-S', '-B', '-c', code, str(agent)], env=environment,
                                capture_output=True, text=True, timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('runtime selection')
        python = result.stdout.strip().splitlines()[-1]
        probe_python(python, require_agent=True)
        return python
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        raise ComponentError('The installed Agent runtime could not be resolved; specify a compatible install or repair it.') from exc


def discover_existing(application_root, environment, webui=None, agent=None, home=None, python=None, state_dir=None):
    root = Path(application_root)
    pins = load_pins(root)
    url = environment.get('ODYSSEUS_HERMES_MODE_URL', 'http://127.0.0.1:8787/')
    status = ModeService(ModeConfig(url)).status()
    default_home = Path(environment['LOCALAPPDATA']) / 'hermes' if environment.get('LOCALAPPDATA') else Path.home() / '.hermes'
    home = Path(home or environment.get('HERMES_HOME') or default_home)
    agent = Path(agent or environment.get('HERMES_WEBUI_AGENT_DIR') or home / 'hermes-agent')
    if not (agent / 'run_agent.py').is_file():
        if status.get('state') in ('ready', 'conflict', 'unavailable'):
            raise ComponentError('The Hermes port is occupied but its installation cannot be safely resolved. No takeover attempted.')
        return None
    verify_checkout(agent, pins['agent'])
    seed = python or environment.get('HERMES_WEBUI_PYTHON') or __import__('sys').executable
    runtime = resolve_agent_python(agent, seed, environment)
    extension_dir = environment.get('HERMES_WEBUI_EXTENSION_DIR')
    if status.get('state') == 'ready':
        # Process metadata only; never return provider keys, cookies or arbitrary argv.
        code = '''import json,sys,psutil
port=int(sys.argv[1])
connections=[c for c in psutil.net_connections('tcp') if c.status=='LISTEN' and c.laddr.port==port]
if not connections or any(c.laddr.ip not in ('127.0.0.1','::1') for c in connections):
 raise SystemExit('existing Hermes is not exclusively loopback-bound')
pids={c.pid for c in connections}
if len(pids)!=1: raise SystemExit('ambiguous backend process')
p=psutil.Process(pids.pop());e=p.environ()
keys=('HERMES_HOME','HERMES_WEBUI_STATE_DIR','HERMES_WEBUI_AGENT_DIR','HERMES_WEBUI_EXTENSION_DIR','HERMES_WEBUI_EXTENSION_MANIFEST')
print(json.dumps({'cwd':p.cwd(),'paths':{k:e[k] for k in keys if k in e}}))
'''
        try:
            result = subprocess.run([runtime, '-I', '-B', '-c', code, str(urlsplit(url).port)], env=environment,
                                    capture_output=True, text=True, timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('process identity')
            info = json.loads(result.stdout)
            paths = info['paths']
            webui = webui or info['cwd']
            home = Path(paths.get('HERMES_HOME') or home)
            state_dir = state_dir or paths.get('HERMES_WEBUI_STATE_DIR')
            agent = Path(paths.get('HERMES_WEBUI_AGENT_DIR') or agent)
            extension_dir = paths.get('HERMES_WEBUI_EXTENSION_DIR') or extension_dir
            if paths.get('HERMES_WEBUI_EXTENSION_MANIFEST'):
                raise ComponentError('Existing Hermes uses a custom manual extension manifest; review its integration explicitly before adopting it.')
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            raise ComponentError('Could not verify the existing Hermes process and data paths; nothing was restarted.') from exc
    elif status.get('state') != 'stopped':
        raise ComponentError('An unrelated or unhealthy listener occupies the Hermes port; nothing was replaced.')
    webui = Path(webui or environment.get('ODYSSEUS_HERMES_REPO') or root.parent / 'hermes-webui')
    if not (webui / 'server.py').is_file():
        return None
    plan = plan_existing(webui, agent, home, runtime, pins, state_dir, application_root=root)
    if extension_dir:
        plan['extension_dir'] = extension_dir
    return plan


def serve_component(root, application_root, environment):
    manager = ComponentManager(root, application_root=application_root, environment=environment)
    config = read_config(manager.config_path)
    if not config or not config['enabled']:
        raise ComponentError('Hermes mode is disabled or not installed.')
    manager.validate(config)
    service = ModeService(ModeConfig(config['url']))
    status = service.status()
    if status.get('state') == 'ready':
        return 0
    if status.get('state') != 'stopped':
        raise ComponentError('The Hermes port is occupied or unhealthy; no takeover attempted.')
    with component_lock(Path(root) / 'managed/service'):
        status = service.status()
        if status.get('state') == 'ready':
            return 0
        if status.get('state') != 'stopped':
            raise ComponentError('Hermes listener changed while starting; no takeover attempted.')
        child = dict(environment)
        for key, target in [('home', 'HERMES_HOME'), ('state_dir', 'HERMES_WEBUI_STATE_DIR'), ('agent', 'HERMES_WEBUI_AGENT_DIR'), ('extension_dir', 'HERMES_WEBUI_EXTENSION_DIR')]:
            child[target] = config[key]
        if config.get('runtime_dir'):
            child['HERMES_RUNTIME_DIR'] = config['runtime_dir']
        child.setdefault('HERMES_WEBUI_PYTHON', config['python'])
        child['HERMES_WEBUI_HOST'] = '127.0.0.1'
        child['HERMES_WEBUI_PORT'] = str(urlsplit(config['url']).port)
        command = list(webui_start_command(config['webui'], child['HERMES_WEBUI_PORT']))
        result = subprocess.run(command, env=child, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode
