"""Opt-in standalone Hermes connection; never merges application state."""
from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
import re


@dataclass(frozen=True)
class ModeConfig:
    url: str
    command: tuple[str, ...] = ()
    environment: dict[str, str] | None = None
    log_dir: Path | None = None
    backend: str = 'webui'
    launcher: tuple[str, ...] = ()
    launcher_cwd: Path | None = None
    component_root: Path | None = None            # set when the gateway is the managed Webapp component
    component_environment: dict[str, str] | None = None
    ownership: str = ''                           # 'managed' (Odysseus may start/stop it) or 'adopted' (it never does)

    @classmethod
    def gateway_from_environment(cls, source):
        """The official Hermes Gateway/Webapp, started and stopped outside Odysseus.

        ODYSSEUS_HERMES_MODE_URL       exact http://127.0.0.1:PORT/ of the Webapp listener
        ODYSSEUS_HERMES_GATEWAY_HOME   the Hermes home whose gateway serves it (same OS user)
        ODYSSEUS_HERMES_GATEWAY_PYTHON the Hermes interpreter, used only to ask the gateway for a one-use launch link
        ODYSSEUS_HERMES_GATEWAY_AGENT_DIR  the Hermes agent checkout that interpreter imports hermes_cli from"""
        url, home = source.get('ODYSSEUS_HERMES_MODE_URL'), source.get('ODYSSEUS_HERMES_GATEWAY_HOME')
        python, agent = source.get('ODYSSEUS_HERMES_GATEWAY_PYTHON'), source.get('ODYSSEUS_HERMES_GATEWAY_AGENT_DIR')
        if not (url and home and python and agent):
            raise ValueError('The gateway backend needs ODYSSEUS_HERMES_MODE_URL, _GATEWAY_HOME, _GATEWAY_PYTHON and _GATEWAY_AGENT_DIR')
        if not (Path(home).is_dir() and Path(python).is_file() and Path(agent).is_dir()):
            raise ValueError('A configured gateway path does not exist')
        from src.runtime_paths import get_app_root
        child_env = {key: value for key, value in source.items() if key not in ('PYTHONPATH', 'PYTHONHOME') and not key.startswith('ODYSSEUS_')}
        child_env['HERMES_HOME'] = str(Path(home))
        child_env['PYTHONPATH'] = str(Path(agent))
        command = (python, '-B', str(Path(get_app_root()) / 'scripts/hermes-webapp-launch.py'), '--home', str(Path(home)))
        return cls(url=url, environment=child_env, backend='gateway', launcher=command, launcher_cwd=Path(agent))

    @classmethod
    def webapp_from_environment(cls, source):
        """The official Webapp installed by scripts/hermes-webapp-component.py. Managed: Odysseus starts it on demand and may stop it.
        Adopted: the user's own gateway, which Odysseus only talks to. Not installed or disabled: Projects mode is simply absent."""
        from src.hermes_webapp_component import WebappComponent
        from src.runtime_paths import get_app_root, get_hermes_component_root
        root = get_hermes_component_root(source)
        if not root:
            return None
        component = WebappComponent(root, application_root=get_app_root(), environment=dict(source))
        saved = component.config()
        if not saved or not saved['enabled']:
            return None
        child_env = {key: value for key, value in source.items() if key not in ('PYTHONPATH', 'PYTHONHOME') and not key.startswith('ODYSSEUS_')}
        child_env['HERMES_HOME'] = str(Path(saved['home']))
        child_env['PYTHONPATH'] = str(Path(saved['agent']))
        command = (saved['python'], '-B', str(Path(get_app_root()) / 'scripts/hermes-webapp-launch.py'), '--home', str(Path(saved['home'])))
        return cls(url=saved['url'], environment=child_env, backend='gateway', launcher=command, launcher_cwd=Path(saved['agent']),
                   component_root=Path(root), component_environment=dict(source), ownership=saved['origin'])

    @classmethod
    def from_environment(cls, environment=None):
        source = os.environ if environment is None else environment
        if source.get('ODYSSEUS_HERMES_DISABLED') == '1':
            return None
        if source.get('ODYSSEUS_HERMES_BACKEND') == 'gateway':
            return cls.gateway_from_environment(source)
        if source.get('ODYSSEUS_HERMES_BACKEND') == 'webapp':
            return cls.webapp_from_environment(source)
        from src.hermes_component import read_config
        from src.runtime_paths import get_app_root, get_hermes_component_root
        root = get_hermes_component_root(source)
        saved = read_config(Path(root) / 'managed/component.json') if root else None
        if saved and not saved['enabled']:
            return None
        url = source.get('ODYSSEUS_HERMES_MODE_URL')
        if not url:
            if not saved:
                return None
            child_env = dict(source)
            for key, target in [('home', 'HERMES_HOME'), ('state_dir', 'HERMES_WEBUI_STATE_DIR'), ('agent', 'HERMES_WEBUI_AGENT_DIR')]:
                child_env[target] = saved[key]
            if saved.get('runtime_dir'):
                child_env['HERMES_RUNTIME_DIR'] = saved['runtime_dir']
            child_env.setdefault('HERMES_WEBUI_PYTHON', saved['python'])
            command = (saved['python'], '-B', str(Path(get_app_root()) / 'scripts/hermes-component.py'), 'serve', '--root', str(root))
            return cls(saved['url'], command, child_env, Path(root) / 'managed/logs')
        config = cls(url=url)
        if source.get('ODYSSEUS_HERMES_ALLOW_START') != '1':
            return config
        repo = Path(source['ODYSSEUS_HERMES_REPO']).resolve()
        child_env = dict(source)
        for source_key, child_key in [
            ('ODYSSEUS_HERMES_HOME', 'HERMES_HOME'),
            ('ODYSSEUS_HERMES_STATE_DIR', 'HERMES_WEBUI_STATE_DIR'),
            ('ODYSSEUS_HERMES_EXTENSION_DIR', 'HERMES_WEBUI_EXTENSION_DIR'),
        ]:
            if source_key in source:
                child_env[child_key] = source[source_key]
        child_env['HERMES_WEBUI_HOST'] = '127.0.0.1'
        child_env['HERMES_WEBUI_PORT'] = str(urlsplit(url).port)
        if source.get('ODYSSEUS_HERMES_EXTENSION_DIR'):
            child_env['HERMES_WEBUI_EXTENSION_SCRIPT_URLS'] = '/extensions/return-to-odysseus.js'
            child_env['HERMES_WEBUI_EXTENSION_STYLESHEET_URLS'] = '/extensions/modes.css'
        command = ('powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(repo / 'start.ps1'), '-BindHost', '127.0.0.1', '-Port', child_env['HERMES_WEBUI_PORT'])
        return cls(url=url, command=command, environment=child_env, log_dir=Path(source['ODYSSEUS_DATA_DIR']) / 'logs')

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if not parsed.port or self.url != f'http://127.0.0.1:{parsed.port}/':
            raise ValueError('Hermes mode requires an exact http://127.0.0.1:PORT/ URL')


PROJECTS_HOST = 'hermes.localhost'
_LAUNCH_TIMEOUT = 20
STATUS_TTL = 2.0


RESUME_ID = re.compile(r'[A-Za-z0-9._-]{1,200}')


def with_resume(url, session_id):
    """Ask the Webapp's own launch page to open on ``/?odysseus_resume=<id>``. The ticket stays in the fragment; the
    odysseus-resume Desktop plugin turns the parameter into host.openSession(). A malformed id changes nothing."""
    if not isinstance(session_id, str) or not RESUME_ID.fullmatch(session_id):
        return url
    parts = urlsplit(url)
    fragment = [(key, value) for key, value in parse_qsl(parts.fragment, keep_blank_values=True) if key != 'query']
    fragment.append(('query', '?' + urlencode({'odysseus_resume': session_id})))
    return parts._replace(fragment=urlencode(fragment)).geturl()


def browser_launch_url(raw, gateway_url, browser_port):
    """The launch link as the browser must open it: always http://hermes.localhost:<Odysseus port>/..., never the
    gateway's own address. Anything that is not the configured gateway (or already that browser address) is refused,
    so a faulty launcher can never turn this route into an open redirect."""
    if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
        raise RuntimeError('The Webapp returned an unusable launch link')
    parts = urlsplit(raw)
    try:
        port = parts.port
    except ValueError:
        port = None
    gateway = urlsplit(gateway_url)
    ours = parts.hostname == '127.0.0.1' and port == gateway.port or parts.hostname == PROJECTS_HOST and port == browser_port
    if parts.scheme != 'http' or parts.username or parts.password or not ours or not browser_port:
        raise RuntimeError('The Webapp returned a launch link for another address')
    return f"http://{PROJECTS_HOST}:{browser_port}{parts.path or '/'}" + (f'?{parts.query}' if parts.query else '') + (f'#{parts.fragment}' if parts.fragment else '')


def _port_is_free(port):
    try:
        with socket.socket() as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


class ModeService:
    def __init__(self, config, error=None):
        self.config = config
        self.error = error
        self.proxy = False
        self.grants = None                     # set by install_hermes_proxy in gateway mode when access grants are on
        self._lock = threading.Lock()
        self._process = None
        self._starter = None                   # background thread running the component's start (managed gateway only)
        self._start_error = None
        self._component_cache = None
        self._status_lock = threading.Lock()   # single-flight: concurrent status reads share one probe instead of each spawning their own
        self._status_cache = None              # (monotonic time, result)

    @property
    def managed(self):
        return bool(self.config and self.config.component_root)

    def _component(self):
        if self._component_cache is None:
            from src.hermes_webapp_component import WebappComponent
            from src.runtime_paths import get_app_root
            self._component_cache = WebappComponent(self.config.component_root, application_root=get_app_root(), environment=self.config.component_environment)
        return self._component_cache

    def _start_managed(self):
        component = self._component()
        self._invalidate_status()
        with self._lock:
            raw = component.status().get('state')           # decide on the component's own answer, not on the 'starting'/'error' decoration
            current = self._managed_status()
            if raw == 'ready':
                return current
            if self._starter is not None and self._starter.is_alive():
                return {**current, 'state': 'starting'}
            if self.config.ownership != 'managed':
                raise RuntimeError('This Hermes gateway is user-owned (adopted); Odysseus never starts it. Start it yourself.')
            if raw != 'stopped':
                raise RuntimeError('The Hermes port is in use by something that is not a ready Webapp gateway; no takeover was attempted.')
            self._start_error = None

            def run():
                try:
                    component.start(wait=True, timeout=180)
                except Exception as exc:                    # recorded for the status line; never raised into the request thread
                    self._start_error = str(exc)[:300] or 'The gateway failed to start.'

            self._starter = threading.Thread(target=run, name='hermes-webapp-start', daemon=True)
            self._starter.start()
            return {**current, 'state': 'starting'}

    def _shared_managed_status(self):
        """_managed_status() for read-only status requests: identical concurrent reads wait for one probe, and its answer is reused for
        STATUS_TTL seconds. Lifecycle actions invalidate it, and _start_managed/_managed_status always probe afresh."""
        cached = self._status_cache
        if cached and time.monotonic() - cached[0] < STATUS_TTL:
            return dict(cached[1])
        with self._status_lock:
            cached = self._status_cache
            if cached and time.monotonic() - cached[0] < STATUS_TTL:
                return dict(cached[1])
            started = time.monotonic()
            result = self._managed_status()
            result['probe_ms'] = int((time.monotonic() - started) * 1000)
            result['checked_at'] = time.time()
            self._status_cache = (time.monotonic(), result)
            return dict(result)

    def _invalidate_status(self):
        self._status_cache = None

    def _managed_status(self):
        info = self._component().status()
        result = {'enabled': True, 'url': self.config.url, 'owned': self.config.ownership == 'managed', 'managed': self.config.ownership == 'managed',
                  'ownership': self.config.ownership, 'backend': self.config.backend, 'route_guard': info.get('route_guard'), 'revision': info.get('revision')}
        if self.proxy:
            from src.hermes_proxy import hostname_resolves
            result['proxy'] = hostname_resolves()
        state = info.get('state')
        result['state'] = state if state in ('ready', 'stopped', 'conflict', 'unavailable') else 'unavailable'
        if 'sign_in_gate' in info:
            result['sign_in_gate'] = info['sign_in_gate']
        starting = self._starter is not None and self._starter.is_alive()
        if state == 'ready':
            self._start_error = None
            runs = self._component().active_sessions()
            result['active_runs'] = runs
            result['busy'] = bool(runs)
        elif starting:
            result['state'] = 'starting'
        elif self._start_error:
            result['state'] = 'error'
            result['error'] = self._start_error
        return result

    def start(self):
        if self.managed and not self.error:
            return self._start_managed()
        with self._lock:
            if self.error:
                raise RuntimeError(self.error)
            current = self.status()
            if current.get('state') == 'ready':
                return current
            if not self.config or current.get('state') != 'stopped':
                raise RuntimeError('Hermes listener is unavailable or belongs to another service; no restart attempted')
            if self._process is not None and self._process.poll() is None:
                return {**current, 'state': 'starting'}
            if not self.config.command or not self.config.log_dir:
                raise RuntimeError('Optional Hermes startup is not configured')
            self.config.log_dir.mkdir(parents=True, exist_ok=True)
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0
            with (self.config.log_dir / 'hermes-launch.log').open('ab') as log:
                self._process = subprocess.Popen(
                    list(self.config.command), env=self.config.environment or os.environ.copy(),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=flags, start_new_session=os.name != 'nt',
                )
            return {**current, 'state': 'starting'}

    def _identity(self):
        """Hermes's /health payload when the configured port answers as Hermes, else None."""
        connection = http.client.HTTPConnection('127.0.0.1', urlsplit(self.config.url).port, timeout=2)
        try:
            connection.request('GET', '/health')
            body = connection.getresponse().read(65537)
            health = json.loads(body) if len(body) <= 65536 else None
        except (OSError, http.client.HTTPException, ValueError):
            return None
        finally:
            connection.close()
        ok = isinstance(health, dict) and isinstance(health.get('sessions'), int) and isinstance(health.get('server_started_at'), (int, float))
        return health if ok else None

    def can_start(self):
        if self.managed:
            return self.config.ownership == 'managed'
        return bool(self.config and self.config.command and self.config.log_dir)

    def _gateway_status(self, result, port):
        """The official Webapp: /api/health is public and says whether its sign-in gate is engaged."""
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
        except (OSError, http.client.HTTPException, ValueError):
            result['state'] = 'stopped' if _port_is_free(port) else 'unavailable'
        finally:
            connection.close()
        return result

    def launch(self, browser_port, resume=None):
        """Ask the running gateway for a one-use sign-in link and return it as the browser must open it.

        The link is a credential: it is never logged, stored or put in an error message here."""
        if not self.config or self.config.backend != 'gateway' or not self.config.launcher:
            raise RuntimeError('Projects mode is not configured')
        if self.status().get('state') != 'ready':
            raise RuntimeError('The official Hermes Webapp is not running; start it first')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            done = subprocess.run(list(self.config.launcher), cwd=self.config.launcher_cwd, env=self.config.environment, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=_LAUNCH_TIMEOUT, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError('The Webapp did not answer the launch request') from exc
        try:
            answer = json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            answer = None
        if done.returncode != 0 or not isinstance(answer, dict) or answer.get('ok') is not True:
            reason = answer.get('error') if isinstance(answer, dict) and isinstance(answer.get('error'), str) else 'the launch helper failed'
            raise RuntimeError('The Webapp refused the launch request: ' + reason[:200])
        return with_resume(browser_launch_url(answer.get('url'), self.config.url, browser_port), resume)

    def stop(self, force=False):
        """Stop Hermes only when the port answers as Hermes; refuse while runs are active unless ``force``."""
        from src import service_controls as controls
        if self.managed and not self.error:
            if self.config.ownership != 'managed':
                raise controls.Unavailable('This Hermes gateway is user-owned (adopted); Odysseus never stops it.')
            if self._starter is not None and self._starter.is_alive():
                raise controls.Unavailable('Hermes is still starting; wait for it to finish before stopping it.')
            self._start_error = None
            self._invalidate_status()
            try:
                self._component().stop(force=force)             # raises ActiveRuns unless force; only ever the process Odysseus provisioned
            finally:
                self._invalidate_status()
            return self._managed_status()
        with self._lock:
            if self.error or not self.config:
                raise RuntimeError('Hermes is not configured here.')
            if self.config.backend == 'gateway':
                raise controls.Unavailable('The official Hermes gateway is managed outside Odysseus; stop it there.')
            if not controls.supported():
                raise controls.Unavailable('Stopping Hermes from Odysseus is only supported on Windows.')
            port = urlsplit(self.config.url).port
            health = self._identity()
            if health is None:
                if not _port_is_free(port):
                    raise RuntimeError('Something other than Hermes is using the Hermes port; nothing was stopped.')
            else:
                runs = health.get('active_runs') or 0
                if runs and not force:
                    raise controls.ActiveRuns(runs)
                controls.stop_hermes_listener(port)
                deadline = time.monotonic() + 20
                while not _port_is_free(port) and time.monotonic() < deadline:
                    time.sleep(0.25)
                if not _port_is_free(port):
                    raise RuntimeError('Hermes did not release its port; check it manually.')
            if self._process is not None and self._process.poll() is None:
                controls.kill_tree(self._process.pid)
            self._process = None
            return self.status()

    def restart(self, force=False):
        if self.managed and not self.error:
            self.stop(force)
            return self.start()
        if not self.can_start():
            from src import service_controls as controls
            raise controls.Unavailable('Optional Hermes startup is not configured, so it could not be started again.')
        self.stop(force)
        return self.start()

    def status(self):
        if self.error:
            return {'enabled': True, 'state': 'unavailable', 'error': self.error}
        if self.config is None:
            return {'enabled': False}
        port = urlsplit(self.config.url).port
        result = {'enabled': True, 'url': self.config.url, 'owned': False, 'backend': self.config.backend}
        if self.proxy:
            from src.hermes_proxy import hostname_resolves
            result['proxy'] = hostname_resolves()
        if self.managed:
            return self._shared_managed_status()
        if self.config.backend == 'gateway':
            return self._gateway_status(result, port)
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/health')
            response = connection.getresponse()
            body = response.read(65537)
            health = json.loads(body) if len(body) <= 65536 else None
            identity = isinstance(health, dict) and isinstance(health.get('sessions'), int) and isinstance(health.get('server_started_at'), (int, float))
            ready = identity and response.status == 200 and health.get('status') == 'ok'
            result['state'] = 'ready' if ready else ('unavailable' if identity else 'conflict')
            if identity:
                result['active_runs'] = health.get('active_runs')
                result['server_started_at'] = health['server_started_at']
        except (OSError, http.client.HTTPException, ValueError):
            result['state'] = 'stopped' if _port_is_free(port) else 'unavailable'
        finally:
            connection.close()
        return result


def create_mode_service(environment=None):
    from src.hermes_component import ComponentError
    try:
        return ModeService(ModeConfig.from_environment(environment))
    except (ComponentError, ValueError, KeyError, OSError):
        return ModeService(None, 'Hermes setup is unavailable. Run scripts/hermes-component.py repair; Normal Mode remains available.')


def require_local(request):
    """Loopback peer, loopback hostname, no proxy headers."""
    from fastapi import HTTPException
    forwarded = any(name in request.headers for name in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip'))
    if forwarded or not request.client or request.client.host not in ('127.0.0.1', '::1') or request.url.hostname not in ('127.0.0.1', 'localhost'):
        raise HTTPException(403, 'Application modes are local-only')


def require_browser_admin(request):
    """A same-origin action by a direct local browser administrator; never the Agent, an API token or a tool identity."""
    from fastapi import HTTPException
    from core.middleware import require_admin
    require_local(request)
    origin = f'{request.url.scheme}://{request.url.netloc}'
    if request.headers.get('origin') != origin:
        raise HTTPException(403, 'Same-origin browser action required')
    if getattr(request.state, 'current_user', None) in ('internal-tool', 'api', 'demo', 'system') or 'authorization' in request.headers or 'x-odysseus-internal-token' in request.headers:
        raise HTTPException(403, 'A direct local browser administrator is required')
    require_admin(request)


def enter_url(grants, request, launch_url, port):
    """Turn the gateway's one-use launch link into an opaque Odysseus ticket URL.

    The launch link (a Hermes credential) is kept server-side in the ticket and only reappears in the redirect that the proxy issues to the browser,
    so it never passes through Odysseus JavaScript. The ticket is bound to this Odysseus session, which is re-read from the cookie and checked against
    the identity the middleware established; a mismatch means a different caller, and nothing is issued."""
    from fastapi import HTTPException
    from src.hermes_grants import ENTER_PATH, GrantError
    token = request.cookies.get('odysseus_session')
    user = getattr(request.state, 'current_user', None)
    parts = urlsplit(launch_url)
    target = (parts.path or '/') + (f'?{parts.query}' if parts.query else '') + (f'#{parts.fragment}' if parts.fragment else '')
    try:
        ticket = grants.create_ticket(token, user, target)
    except GrantError as exc:
        raise HTTPException(403, 'Projects access could not be granted for this session') from exc
    return f'http://{PROJECTS_HOST}:{port}{ENTER_PATH}?ticket={ticket}'


def make_router(service):
    from fastapi import APIRouter, HTTPException, Request

    from fastapi.responses import JSONResponse
    from src import service_controls as controls

    router = APIRouter(prefix='/api/application-modes', tags=['application-modes'])
    local_only = require_local

    def guarded(action):
        """Run a service-control action and map its failures to recoverable HTTP answers."""
        try:
            return action()
        except controls.ActiveRuns as exc:
            return JSONResponse(status_code=409, content={'detail': str(exc), 'active_runs': exc.count, 'force_required': True})
        except controls.Unavailable as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'The action failed; nothing further was attempted') from exc

    @router.get('')
    def status(request: Request):
        local_only(request)
        result = service.status()
        if isinstance(result, dict) and result.get('enabled'):
            available = controls.availability(getattr(service, 'can_start', lambda: False)() is True)
            if result.get('backend') == 'gateway' and not result.get('managed'):
                available = {**available, 'hermes_stop': False, 'hermes_restart': False}
            available = {**available, 'hermes_start': bool(result.get('managed'))}
            result = {**result, 'controls': available}
        return result

    @router.post('/projects/launch')
    def projects_launch(request: Request, resume: str | None = None):
        """Mint the one-use Webapp sign-in link for an authenticated Odysseus administrator in this browser."""
        require_browser_admin(request)
        port = request.url.port
        try:
            url = service.launch(port, resume=resume) if resume else service.launch(port)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if getattr(service, 'grants', None) is not None:
            url = enter_url(service.grants, request, url, port)
        return JSONResponse({'url': url}, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer'})

    @router.post('/hermes/stop')
    def stop(request: Request, force: bool = False):
        require_browser_admin(request)
        return guarded(lambda: service.stop(force=force))

    @router.post('/hermes/restart')
    def restart(request: Request, force: bool = False):
        require_browser_admin(request)
        return guarded(lambda: service.restart(force=force))

    @router.post('/odysseus/restart')
    def restart_odysseus(request: Request):
        require_browser_admin(request)
        # Protect the Hermes listener when it runs inside the launcher tree (legacy layout). Derive its
        # port from the configured Hermes URL; fall back to the production default when not derivable.
        try:
            gateway_port = urlsplit(service.config.url).port
        except (AttributeError, TypeError, ValueError):
            gateway_port = None
        return guarded(lambda: {key: value for key, value in controls.schedule_odysseus_restart(gateway_port=gateway_port).items() if key != 'helper'})

    @router.post('/hermes/start')
    def start(request: Request):
        require_browser_admin(request)
        try:
            return service.start()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Hermes launch failed; inspect the private launch log') from exc

    return router
