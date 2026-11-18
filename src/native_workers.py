"""Durable native producer index, not task history or process-signal authority."""
from __future__ import annotations

from functools import lru_cache
import asyncio
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4

from core.atomic_io import atomic_write_json, store_transaction
from core.platform_compat import pid_alive
from src import process_ownership as ownership

_ASYNC_OBSERVERS = set()


def _identity_verdict(identity):
    verdict = ownership.verify(identity.get('pid'), identity.get('start_token'))
    # Windows retains creation times for exited processes with open handles.
    if verdict == ownership.OWNED and not pid_alive(identity['pid']):
        return ownership.GONE
    return verdict


def _keep(task):
    _ASYNC_OBSERVERS.add(task)
    task.add_done_callback(_ASYNC_OBSERVERS.discard)


class AdmissionClosed(RuntimeError):
    pass


class WorkerStateUnknown(RuntimeError):
    pass


class WorkerRegistry:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.witness = self.path.with_suffix('.initialized')
        self.manager = ownership.capture(os.getpid())

    def _read(self, *, create=False):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            if not create or self.witness.exists():
                raise WorkerStateUnknown('Native worker inventory missing') from None
            generation = uuid4().hex
            data = {'schema_version': 1, 'generation': generation, 'workers': {}, 'epochs': {}, 'fence': None}
            atomic_write_json(str(self.witness), {'generation': generation})
            atomic_write_json(str(self.path), data)
        except (ValueError, OSError) as exc:
            raise WorkerStateUnknown('Native worker inventory unreadable') from exc
        try:
            witness = json.loads(self.witness.read_text(encoding='utf-8'))
            if (type(data.get('schema_version')) is not int or data['schema_version'] != 1
                    or not isinstance(data.get('workers'), dict)
                    or witness.get('generation') != data.get('generation')
                    or not isinstance(data.get('generation'), str)
                    or any(not isinstance(row, dict) for row in data['workers'].values())):
                raise ValueError('Invalid native worker inventory')
            if 'fence' not in data or not isinstance(data.get('epochs'), dict):
                raise ValueError('Missing producer epoch/fence')
            for key, row in data['workers'].items():
                if (row.get('id') != key or row.get('state') not in {
                        'intended', 'running', 'completed', 'failed', 'cancelled', 'unknown'}
                        or not isinstance(row.get('manager'), dict)
                        or not isinstance(row.get('source'), str)
                        or not isinstance(row.get('source_id'), str)
                        or type(row.get('cancel_requested')) is not bool
                        or (row.get('identity') is not None and not isinstance(row['identity'], dict))
                        or (row['state'] in {'completed', 'failed', 'cancelled'}
                            and type(row.get('exit_code')) is not int
                            and not (row['state'] == 'cancelled' and row['cancel_requested']
                                     and row.get('exit_code') is None
                                     and row.get('completion_basis') == 'verified-stop'))):
                    raise ValueError('Invalid worker record')
            fence = data['fence']
            if fence is not None and (not isinstance(fence, dict)
                    or not isinstance(fence.get('token'), str)
                    or not isinstance(fence.get('manager'), dict)):
                raise ValueError('Invalid producer fence')
        except (ValueError, OSError, AttributeError, TypeError) as exc:
            raise WorkerStateUnknown('Native worker inventory invalid') from exc
        return data

    def _transaction(self, action, *, create=False):
        @store_transaction(lambda: self.path)
        def apply():
            return action(self._read(create=create))
        return apply()

    def initialize(self):
        """Bind a real new producer epoch; never attest pre-existing legacy work."""
        if not self.manager.get('start_token'):
            raise WorkerStateUnknown('Producer process identity unavailable')
        def initialize(data):
            fence = data['fence']
            if fence is not None:
                owner = fence['manager']
                verdict = _identity_verdict(owner)
                if verdict not in {ownership.GONE, ownership.FOREIGN}:
                    raise AdmissionClosed('Previous producer fence owner is live or unknown')
                # Release a dead epoch's admission lease, not its workers.
                # Their identities/history are retained and reconciled below.
                data.setdefault('retired_fences', []).append({
                    'manager': owner, 'released_by': self.manager, 'released_at': time.time()})
                data['fence'] = None
            key = str(self.manager['pid']) + ':' + self.manager['start_token']
            data['epochs'].setdefault(key, {'identity': self.manager, 'initialized_at': time.time()})
            atomic_write_json(str(self.path), data)
        self._transaction(initialize, create=True)

    def spawn(self, source, source_id, factory):
        if not self.manager.get('start_token'):
            raise WorkerStateUnknown('Producer process identity unavailable')
        if not all(isinstance(value, str) and 0 < len(value) <= 192 for value in (source, source_id)):
            raise ValueError('Invalid producer linkage')
        generation = uuid4().hex
        def launch(data):
            if data['fence'] is not None:
                raise AdmissionClosed('Native worker admission is fenced')
            row = {'id': generation, 'source': source, 'source_id': source_id,
                   'manager': self.manager, 'state': 'intended', 'created_at': time.time(),
                   'identity': None, 'exit_code': None, 'cancel_requested': False}
            data['workers'][generation] = row
            atomic_write_json(str(self.path), data)
            try:
                proc = factory()
            except BaseException as exc:
                row.update(state='unknown', error_type=type(exc).__name__)
                atomic_write_json(str(self.path), data)
                raise
            row.update(identity=ownership.capture(proc.pid), state='running')
            if not row['identity'].get('start_token'):
                row['state'] = 'unknown'
            atomic_write_json(str(self.path), data)
            return proc, row['identity']
        proc, identity = self._transaction(launch, create=True)
        proc._odysseus_worker_generation = generation
        def observe():
            try:
                self.finish(generation, identity, proc.wait())
            except (OSError, WorkerStateUnknown, ValueError):
                # Losing observation cannot grant control or fabricate completion.
                return
        try:
            threading.Thread(target=observe, name='native-worker-exit', daemon=True).start()
        except RuntimeError:
            pass  # The durable identity remains observable; do not kill the child.
        return proc

    async def spawn_async(self, source, source_id, factory):
        if not self.manager.get('start_token'):
            raise WorkerStateUnknown('Producer process identity unavailable')
        if not all(isinstance(value, str) and 0 < len(value) <= 192 for value in (source, source_id)):
            raise ValueError('Invalid producer linkage')
        generation = uuid4().hex
        def reserve(data):
            if data['fence'] is not None:
                raise AdmissionClosed('Native worker admission is fenced')
            data['workers'][generation] = {
                'id': generation, 'source': source, 'source_id': source_id,
                'manager': self.manager, 'state': 'intended', 'created_at': time.time(),
                'identity': None, 'exit_code': None, 'cancel_requested': False}
            atomic_write_json(str(self.path), data)
        self._transaction(reserve, create=True)

        async def attach(proc):
            identity = ownership.capture(proc.pid)
            def bind(data):
                row = data['workers'][generation]
                row.update(identity=identity, state='running' if identity.get('start_token') else 'unknown')
                atomic_write_json(str(self.path), data)
            self._transaction(bind)
            proc._odysseus_worker_generation = generation
            async def observe():
                try:
                    self.finish(generation, identity, await proc.wait())
                except (OSError, WorkerStateUnknown, ValueError):
                    return
            _keep(asyncio.create_task(observe()))
            return proc

        async def create():
            try:
                return await attach(await factory())
            except BaseException as exc:
                def uncertain(data):
                    data['workers'][generation].update(state='unknown', error_type=type(exc).__name__)
                    atomic_write_json(str(self.path), data)
                self._transaction(uncertain)
                raise
        # Cancellation of a caller never abandons an OS spawn already admitted.
        spawning = asyncio.create_task(create())
        _keep(spawning)
        try:
            return await asyncio.shield(spawning)
        except asyncio.CancelledError:
            spawning.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            raise

    def request_cancel(self, generation):
        def cancel(data):
            row = data['workers'][generation]
            if row['state'] not in {'completed', 'failed', 'cancelled'}:
                row['cancel_requested'] = True
                atomic_write_json(str(self.path), data)
        self._transaction(cancel)

    def request_cancel_source(self, source, source_id, *, confirmed_stop=False):
        if not self.path.is_file():
            return False  # Legacy tasks do not acquire invented publications.
        def cancel(data):
            rows = [row for row in data['workers'].values()
                    if row['source'] == source and row['source_id'] == source_id]
            if not rows:
                return False
            row = max(rows, key=lambda item: item['created_at'])
            if row['state'] in {'completed', 'failed', 'cancelled'}:
                return False
            row['cancel_requested'] = True
            if confirmed_stop and row['identity'] and _identity_verdict(row['identity']) in {
                    ownership.GONE, ownership.FOREIGN}:
                row.update(state='cancelled', exit_code=None, ended_at=time.time(),
                           completion_basis='verified-stop')
            atomic_write_json(str(self.path), data)
            return True
        return self._transaction(cancel)

    def finish(self, generation, identity, exit_code):
        if type(exit_code) is not int:
            raise ValueError('An observed integer exit code is required')
        def finish(data):
            row = data['workers'].get(generation)
            if row is None or row['identity'] != identity:
                raise WorkerStateUnknown('Native worker identity changed')
            row.update(state=('cancelled' if row['cancel_requested'] else
                              'completed' if exit_code == 0 else 'failed'),
                       exit_code=exit_code, ended_at=time.time(), completion_basis='child-wait')
            atomic_write_json(str(self.path), data)
        self._transaction(finish)

    def confirm_stopped(self, source, source_id, identity):
        """Record confirmed direct-process death, never an invented exit code."""
        if not self.path.is_file():
            return False
        def confirm(data):
            rows = [row for row in data['workers'].values()
                    if row['source'] == source and row['source_id'] == source_id
                    and row['identity'] == identity and row['cancel_requested']
                    and row['state'] not in {'completed', 'failed', 'cancelled'}]
            if not rows or _identity_verdict(identity) not in {ownership.GONE, ownership.FOREIGN}:
                return False
            for row in rows:
                row.update(state='cancelled', exit_code=None, ended_at=time.time(),
                           completion_basis='verified-stop')
            atomic_write_json(str(self.path), data)
            return True
        return self._transaction(confirm)

    def fence(self):
        self._read()  # Never manufacture a missing inventory just to fence it.
        token = uuid4().hex
        def fence(data):
            if data['fence'] is not None:
                raise AdmissionClosed('Native worker admission is already fenced')
            data['fence'] = {'token': token, 'manager': self.manager}
            atomic_write_json(str(self.path), data)
        self._transaction(fence)
        return token

    def resume(self, token):
        def resume(data):
            if data['fence'] != {'token': token, 'manager': self.manager}:
                raise AdmissionClosed('Native worker fence ownership changed')
            data['fence'] = None
            atomic_write_json(str(self.path), data)
        self._transaction(resume)

    def snapshot(self):
        try:
            data = self._read()
        except WorkerStateUnknown as exc:
            return {'initialized': False, 'unknown': True, 'reason': str(exc), 'workers': [],
                    'legacy_inventory_verified': False, 'signal_authorized': False}
        rows = []
        for stored in data['workers'].values():
            row = dict(stored)
            if row['state'] not in {'completed', 'failed', 'cancelled'}:
                identity = row.get('identity') or {}
                verdict = _identity_verdict(identity)
                if row['state'] == 'intended' and not identity and _identity_verdict(
                        row['manager']) == ownership.OWNED:
                    row['state'] = 'intended'
                elif verdict == ownership.OWNED:
                    manager = row['manager']
                    owner = _identity_verdict(manager)
                    row['state'] = 'running' if owner == ownership.OWNED else 'orphaned' if owner in {
                        ownership.GONE, ownership.FOREIGN} else 'unknown'
                else:
                    row['state'] = 'unknown'
                row['process_ownership'] = verdict
            rows.append(row)
        return {'initialized': True, 'unknown': any(row['state'] == 'unknown' for row in rows),
                'draining': data['fence'] is not None, 'workers': rows,
                'legacy_inventory_verified': False, 'signal_authorized': False}


@lru_cache(maxsize=8)
def registry_for(directory):
    return WorkerRegistry(Path(directory) / 'native_workers' / 'index.json')


def default_registry():
    from src.constants import PROCESS_RESOURCES_DIR
    return registry_for(str(Path(PROCESS_RESOURCES_DIR).resolve()))


async def initialize_producers(*, registry=None):
    """Enroll ordinary app startup too; invalid history/fences abort startup."""
    registry = registry if registry is not None else default_registry()
    await asyncio.to_thread(registry.initialize)


def tracked_popen(source, source_id, command, *, registry=None, **kwargs):
    registry = registry if registry is not None else default_registry()
    from core.platform_compat import native_python_command
    if isinstance(command, (list, tuple)):
        command, env = native_python_command(command, kwargs.get('env'))
        kwargs['env'] = env
    return registry.spawn(source, source_id, lambda: subprocess.Popen(command, **kwargs))


async def tracked_exec(*args, source='shell', source_id=None, registry=None, **kwargs):
    registry = registry if registry is not None else default_registry()
    from core.platform_compat import native_python_command
    args, env = native_python_command(args, kwargs.get('env'))
    kwargs['env'] = env
    return await registry.spawn_async(source, source_id or uuid4().hex,
        lambda: asyncio.create_subprocess_exec(*args, **kwargs))


async def tracked_shell(*args, source='shell', source_id=None, registry=None, **kwargs):
    registry = registry if registry is not None else default_registry()
    return await registry.spawn_async(source, source_id or uuid4().hex,
        lambda: asyncio.create_subprocess_shell(*args, **kwargs))
