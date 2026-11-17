"""Typed local runtime control; no shell, installers, downloads or implicit GPU use.

A server-owned launch receipt, not an HTTP response or browser task record,
authorizes stopping only the process tree this controller actually launched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shlex
import socket
import subprocess
from threading import Thread
import time
import uuid

from core.atomic_io import atomic_write_json, atomic_write_text, store_transaction
from core.platform_compat import detached_popen_kwargs, kill_process_tree, pid_alive
from src.process_lifecycle import pgid_of
from src.process_ownership import start_token, verify
from src.gguf_context import planning_metadata


def available_memory_bytes() -> int | None:
    """Available host RAM, without NVML, GPU probes or external commands."""
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
                (name, ctypes.c_uint64) for name in ('total', 'available', 'total_page', 'avail_page',
                                                   'total_virtual', 'avail_virtual', 'extended')]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        return int(value.available) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)) else None
    try:
        for line in Path('/proc/meminfo').read_text(encoding='ascii').splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def build_launch_plan(*, engine: str, executable: str, port: int, model_path: str = '',
                      context_length: int = 4096, gpu_layers: int = 0,
                      model_context_limit: int | None = None, kv_bytes_per_token: int | None = None,
                      available_memory_bytes: int | None = None) -> dict:
    if engine not in {'llamacpp', 'ollama'}:
        raise ValueError('Only standalone llama.cpp or Ollama can be managed; Python bindings are separate')
    exe = Path(executable).expanduser().resolve()
    name = 'llama-server' if engine == 'llamacpp' else 'ollama'
    names = {name + '.exe'} if os.name == 'nt' else {name}
    if not exe.is_file() or exe.name.lower() not in names:
        raise ValueError('Select an existing standalone runtime executable')
    if not 1024 <= port <= 65535:
        raise ValueError('port must be between 1024 and 65535')
    if not 128 <= context_length <= 1048576 or not 0 <= gpu_layers <= 999:
        raise ValueError('Invalid context length or GPU layer count')
    if model_context_limit is not None and model_context_limit < 128:
        raise ValueError('Invalid model context limit')
    if kv_bytes_per_token is not None and kv_bytes_per_token <= 0:
        raise ValueError('KV memory estimate must be positive')
    context = min(context_length, model_context_limit or context_length)
    warnings = []
    memory_validation = 'unknown'
    argv = [str(exe)]
    env = {}
    model = ''
    declared_context = None
    kv_estimate = None
    if engine == 'llamacpp':
        source = Path(model_path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() != '.gguf':
            raise ValueError('Select an existing GGUF file')
        with source.open('rb') as handle:
            if handle.read(4) != b'GGUF':
                raise ValueError('Selected file does not have a GGUF header')
        model = str(source)
        metadata = planning_metadata(source)
        declared_context = metadata.get('declared_context_length')
        kv_estimate = metadata.get('kv_cache_estimate')
        derived_kv = kv_estimate['bytes_per_token'] if kv_estimate else 0
        planning_kv = max(kv_bytes_per_token or 0, derived_kv)
        if declared_context is not None:
            if declared_context < 128:
                raise ValueError('GGUF declared context is below the supported planning minimum')
            context = min(context, declared_context)
        if available_memory_bytes and gpu_layers == 0:
            usable = max(0, available_memory_bytes - max(2 * 1024**3, available_memory_bytes // 5))
            remaining = usable - source.stat().st_size
            if remaining <= 0:
                raise ValueError('Model exceeds the available RAM planning margin')
            if planning_kv:
                context = min(context, remaining // planning_kv)
                if context < 128:
                    raise ValueError('Insufficient memory for the requested KV cache')
                if kv_bytes_per_token and kv_bytes_per_token >= derived_kv:
                    memory_validation = 'estimated'
        if kv_estimate:
            kv_estimate['bytes_at_planned_context'] = context * derived_kv
            kv_estimate['used_for_memory_limit'] = bool(available_memory_bytes and gpu_layers == 0)
            warnings.append('GGUF dense F16 KV estimate is an assumption; runtime cache layout/type and scratch are unverified')
        if memory_validation == 'unknown':
            warnings.append('KV/VRAM memory fit is unknown; explicit acknowledgement is required to start')
        argv += ['--model', model, '--host', '127.0.0.1', '--port', str(port),
                 '--ctx-size', str(context), '--n-gpu-layers', str(gpu_layers)]
    else:
        if gpu_layers:
            raise ValueError('Ollama does not accept llama.cpp GPU layer flags')
        argv += ['serve']
        env = {'OLLAMA_HOST': f'127.0.0.1:{port}', 'OLLAMA_CONTEXT_LENGTH': str(context),
               'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
               'OLLAMA_NOPRUNE': '1', 'OLLAMA_NO_CLOUD': '1'}
        warnings.append('Daemon start only: no Ollama model is pulled or loaded')
        memory_validation = 'not-applicable'
    plan = {'engine': engine, 'executable': str(exe), 'model_path': model, 'port': port,
            'context_length': context, 'gpu_layers': gpu_layers, 'argv': argv, 'env': env,
            'model_context_limit': model_context_limit, 'kv_bytes_per_token': kv_bytes_per_token,
            'memory_validation': memory_validation, 'warnings': warnings,
            'base_url': f'http://127.0.0.1:{port}'}
    if declared_context is not None:
        plan['declared_context_length'] = declared_context
    if kv_estimate is not None:
        plan['kv_cache_estimate'] = kv_estimate
    plan['fingerprint'] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


def _spawn_runtime(plan: dict, log):
    # Preserve runtime/library/cache paths, not provider credentials or proxy
    # configuration from the parent app. No environment values are logged.
    keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'USERPROFILE', 'HOME',
            'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH',
            'OLLAMA_MODELS', 'CUDA_PATH', 'CUDA_VISIBLE_DEVICES'}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env.update(plan['env'])
    env['PYTHONUTF8'] = '1'
    return subprocess.Popen(plan['argv'], stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, cwd=str(Path(plan['executable']).parent),
                            env=env, **detached_popen_kwargs())


class LocalServingController:
    def __init__(self, state_path: Path, log_dir: Path):
        self.state_path = Path(state_path)
        self.log_dir = Path(log_dir)
        self._children = {}

    def _receipt(self, sid: str) -> dict | None:
        import re
        if not re.fullmatch(r'serve-[0-9a-f]{8}', sid):
            return None
        try:
            receipt = json.loads((self.log_dir / f'{sid}.serving.json').read_text(encoding='utf-8'))
            if receipt.get('schema_version') != 1 or receipt.get('source') != 'odysseus-direct' or receipt.get('session_id') != sid:
                return None
            pid = int((self.log_dir / f'{sid}.pid').read_text(encoding='utf-8'))
            token = (self.log_dir / f'{sid}.pid.token').read_text(encoding='utf-8').strip()
            if pid <= 0 or pid == os.getpid() or receipt['pid'] != pid or receipt['start_token'] != token or not token:
                return None
            plan = receipt.get('plan')
            if not isinstance(plan, dict) or plan.get('engine') not in {'llamacpp', 'ollama'}:
                return None
            port = plan.get('port')
            if not isinstance(port, int) or not 1024 <= port <= 65535 or plan.get('base_url') != f'http://127.0.0.1:{port}':
                return None
            if not re.fullmatch(r'[0-9a-f]{64}', str(plan.get('fingerprint', ''))):
                return None
            if os.name != 'nt' and receipt.get('pgid') != pid:
                return None
            return receipt
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def recorded_plan(self, sid: str) -> dict | None:
        receipt = self._receipt(sid)
        return dict(receipt['plan']) if receipt else None

    def _observe_exit(self, sid, child, token):
        try:
            code = child.wait()
            receipt = self._receipt(sid)
            if receipt and receipt['pid'] == child.pid and receipt['start_token'] == token:
                atomic_write_json(str(self.log_dir / f'{sid}.exit.json'), {
                    'schema_version': 1, 'source': 'odysseus-child-exit', 'session_id': sid,
                    'pid': child.pid, 'start_token': token, 'exit_code': code,
                    'observed_at': time.time(),
                })
        except (OSError, ValueError):
            logging.getLogger(__name__).warning('Runtime exit evidence unavailable for %s', sid)

    def exit_details(self, sid: str) -> dict | None:
        receipt = self._receipt(sid)
        if not receipt or self.control_permission(sid):
            return None
        try:
            event = json.loads((self.log_dir / f'{sid}.exit.json').read_text(encoding='utf-8'))
            if (event['schema_version'] != 1 or event['source'] != 'odysseus-child-exit'
                    or event['session_id'] != sid or event['pid'] != receipt['pid']
                    or event['start_token'] != receipt['start_token']):
                return None
            code, observed = event['exit_code'], event['observed_at']
            if (type(code) is not int or type(observed) not in (int, float)
                    or not math.isfinite(observed) or observed <= 0):
                return None
            return {'exit_code': code, 'observed_at': observed}
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return None

    def control_permission(self, sid: str) -> bool:
        receipt = self._receipt(sid)
        return bool(receipt and pid_alive(receipt['pid']) and verify(receipt['pid'], receipt['start_token']) == 'owned')

    def restart_permission(self, sid: str) -> bool:
        receipt = self._receipt(sid)
        return bool(receipt and (not pid_alive(receipt['pid']) or
                                verify(receipt['pid'], receipt['start_token']) == 'owned'))

    def _state(self):
        if not self.state_path.exists():
            return {'tasks': []}
        state = json.loads(self.state_path.read_text(encoding='utf-8'))
        if not isinstance(state, dict) or not isinstance(state.get('tasks', []), (list, dict)):
            raise ValueError('Unreadable Cookbook task state')
        if isinstance(state.get('tasks'), dict):
            state['tasks'] = list(state['tasks'].values())
        return state

    def start(self, plan: dict, *, allow_unestimated_context: bool = False) -> dict:
        if plan['memory_validation'] == 'unknown' and not allow_unestimated_context:
            raise ValueError('Explicit acknowledgement of unknown memory fit is required')

        @store_transaction(lambda: self.state_path)
        def apply():
            self.log_dir.mkdir(parents=True, exist_ok=True)
            for file in self.log_dir.glob('serve-*.serving.json'):
                sid = file.name.removesuffix('.serving.json')
                receipt = self._receipt(sid)
                if receipt and receipt['plan']['port'] == plan['port'] and self.control_permission(sid):
                    if receipt['plan']['fingerprint'] != plan['fingerprint']:
                        raise ValueError('port is already owned by a different managed runtime')
                    return {'ok': True, 'session_id': sid, 'state': 'observed', 'reused': True, 'base_url': plan['base_url']}
            with socket.socket() as check:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    check.bind(('127.0.0.1', plan['port']))
                except OSError as exc:
                    raise ValueError('port is busy; external listeners are never replaced or adopted') from exc
            state = self._state()
            sid = 'serve-' + uuid.uuid4().hex[:8]
            log_path = self.log_dir / f'{sid}.log'
            with log_path.open('ab') as log:
                child = _spawn_runtime(plan, log)
            try:
                token = start_token(child.pid)
                if not token or not pid_alive(child.pid):
                    raise ValueError('Runtime exited before a live ownership receipt could be recorded')
                receipt = {'schema_version': 1, 'source': 'odysseus-direct', 'session_id': sid,
                           'pid': child.pid, 'start_token': token, 'pgid': pgid_of(child.pid),
                           'plan': plan, 'started_at': time.time()}
                atomic_write_text(str(self.log_dir / f'{sid}.pid'), str(child.pid))
                atomic_write_text(str(self.log_dir / f'{sid}.pid.token'), token)
                atomic_write_json(str(self.log_dir / f'{sid}.serving.json'), receipt)
                command = shlex.join(plan['argv'])
                if plan['engine'] == 'ollama':
                    command = f"OLLAMA_HOST=127.0.0.1:{plan['port']} " + command
                state.setdefault('tasks', []).append({'id': sid, 'sessionId': sid, 'type': 'serve',
                    'name': Path(plan['model_path']).name or 'Managed Ollama', 'status': 'running',
                    'platform': 'windows' if os.name == 'nt' else 'linux', 'remoteHost': '',
                    'ts': int(time.time() * 1000), 'output': '',
                    'payload': {'repo_id': plan['model_path'] or 'ollama', '_cmd': command,
                                '_managedServing': True, 'backend': plan['engine']}})
                atomic_write_json(str(self.state_path), state, indent=2)
                self._children[sid] = child
            except Exception:
                # This is a direct handle to our just-created child, not a
                # rediscovered PID. Clean up only this failed launch.
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
                raise
            warnings = list(plan['warnings'])
            try:
                Thread(target=self._observe_exit, args=(sid, child, token), daemon=True).start()
            except RuntimeError:
                warnings.append('Exit observation unavailable; exit status will remain unknown.')
            return {'ok': True, 'session_id': sid, 'state': 'starting', 'reused': False,
                    'base_url': plan['base_url'], 'warnings': warnings}
        return apply()

    def restart(self, sid: str, *, allow_unestimated_context: bool = False) -> dict:
        receipt = self._receipt(sid)
        if not receipt:
            raise ValueError('No controller launch receipt; ownership cannot authorize restart')
        if pid_alive(receipt['pid']) and verify(receipt['pid'], receipt['start_token']) != 'owned':
            raise ValueError('Process ownership is unknown or stale; no restart was attempted')
        previous = receipt['plan']
        # Rebuild from typed inputs, never replay persisted argv or environment.
        try:
            plan = build_launch_plan(
                **{key: previous[key] for key in ('engine', 'executable', 'port', 'model_path',
                                                 'context_length', 'gpu_layers')},
                model_context_limit=previous.get('model_context_limit'),
                kv_bytes_per_token=previous.get('kv_bytes_per_token'),
                available_memory_bytes=available_memory_bytes(),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid saved runtime recipe; no restart was attempted') from exc
        if plan['memory_validation'] == 'unknown' and not allow_unestimated_context:
            raise ValueError('Explicit acknowledgement of unknown memory fit is required before restart')
        stopped = self.stop(sid)
        if not stopped['process_stopped']:
            return {**stopped, 'previous_session_id': sid}
        try:
            result = self.start(plan, allow_unestimated_context=allow_unestimated_context)
        except (ValueError, OSError) as exc:
            return {'ok': False, 'state': 'restart-failed', 'previous_session_id': sid,
                    'process_stopped': True, 'error': str(exc)}
        return {**result, 'previous_session_id': sid, 'process_stopped': True}

    def stop(self, sid: str) -> dict:
        @store_transaction(lambda: self.state_path)
        def apply():
            receipt = self._receipt(sid)
            if not receipt:
                raise ValueError('No controller launch receipt; ownership cannot authorize stop')
            alive = pid_alive(receipt['pid'])
            if alive and verify(receipt['pid'], receipt['start_token']) != 'owned':
                raise ValueError('Process ownership is unknown or stale; no signal was sent')
            state = self._state()
            tasks = state.get('tasks', [])
            task = next((t for t in tasks if isinstance(t, dict) and t.get('sessionId') == sid), None)
            if task is not None and alive:
                task.update(status='cancel_requested', _cancelRequestedAt=time.time())
                atomic_write_json(str(self.state_path), state, indent=2)
            outcome = kill_process_tree(receipt['pid'], start_token=receipt['start_token'],
                                        pgid=receipt.get('pgid'), require_identity=True) if alive else None
            dead = not alive or bool(outcome and outcome.dead)
            if task is not None and dead:
                task['status'] = 'stopped'
                atomic_write_json(str(self.state_path), state, indent=2)
            child = self._children.pop(sid, None) if dead else None
            if child is not None:
                child.wait(timeout=5)
            return {'ok': dead, 'session_id': sid, 'process_stopped': dead,
                    'state': 'stopped' if dead else 'stopping',
                    'teardown': outcome.to_dict() if outcome else None}
        return apply()
