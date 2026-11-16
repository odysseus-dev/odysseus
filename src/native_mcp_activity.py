"""Process-local native MCP admission and completion evidence; no signals."""
from collections import Counter
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import math
import time
from typing import Any
import weakref

CURRENT: ContextVar[tuple[Any, int] | None] = ContextVar('odysseus_native_mcp_operation', default=None)
CONNECTING: ContextVar[tuple[Any, str, str] | None] = ContextVar('odysseus_native_mcp_connection', default=None)


class AdmissionClosed(RuntimeError):
    pass


class Registry:
    def __init__(self):
        self.lock = threading.RLock()
        self.managers = weakref.WeakValueDictionary()
        self.initialized = False
        self.draining = False
        self.connections_closed = False
        self.generation = 0
        self.sequence = 0
        self.operations = {}
        self.uncertain = set()
        self.processes = {}

    def register(self, manager):
        with self.lock:
            self.sequence += 1
            owner = str(self.sequence)
            self.managers[owner] = manager
            self.initialized = True
            if self.draining:
                self.uncertain.add(('manager_created_during_drain', owner, ''))
            return owner

    @contextmanager
    def operation(self, owner, server, kind='tool'):
        with self.lock:
            if self.draining:
                raise AdmissionClosed('Native MCP admission is fenced for restart')
            self.sequence += 1
            ticket = self.sequence
            self.connections_closed = False
            self.operations[ticket] = (owner, server, kind)
        context = CURRENT.set((self, ticket))
        try:
            yield ticket
        finally:
            CURRENT.reset(context)
            with self.lock:
                self.operations.pop(ticket, None)

    def require_operation(self):
        current = CURRENT.get()
        with self.lock:
            if not current or current[0] is not self or current[1] not in self.operations:
                self.uncertain.add(('untracked_dispatch', '', ''))
                raise RuntimeError('Untracked native MCP dispatch')
            return self.operations[current[1]]

    def unknown(self, reason, owner='', server=''):
        with self.lock:
            self.uncertain.add((reason, owner, server))

    def fence(self):
        with self.lock:
            if self.draining:
                if self.connections_closed and not self.operations and not self.uncertain:
                    return self.generation
                raise AdmissionClosed('Native MCP is already draining')
            self.draining = True
            self.generation += 1
            return self.generation

    def resume(self, generation):
        with self.lock:
            if self.generation == generation:
                self.draining = False  # Unknown completion is never erased by reopening admission.

    def snapshot(self):
        with self.lock:
            return {'initialized': self.initialized, 'draining': self.draining,
                    'connections_closed': self.connections_closed,
                    'in_flight': len(self.operations), 'unknown': len(self.uncertain),
                    'tool_calls_in_flight': sum(row[2] == 'tool' for row in self.operations.values()),
                    'connection_scopes_in_flight': sum(row[2] == 'connection' for row in self.operations.values()),
                    'by_server': dict(Counter(row[1] for row in self.operations.values())),
                    'unknown_reasons': sorted({row[0] for row in self.uncertain})}

    def process_opened(self, owner, server, process):
        try:
            from src import windows_restart_os as win
            identity = win.identity(process.pid)
            if identity is None:
                raise RuntimeError('No live process incarnation')
            key = (owner, server, process.pid, identity['start_token'])
            with self.lock:
                self.processes[key] = {'identity': identity, 'children': {}, 'closed': False, 'process': process}
            return key
        except (OSError, RuntimeError, ImportError):
            self.unknown('stdio_process_identity_unknown', owner, server)
            return None

    def process_closed(self, key):
        with self.lock:
            if key in self.processes:
                self.processes[key]['closed'] = True

    def accounted_process_ids(self):
        """Only direct-spawn provenance and fresh kernel incarnations, never names."""
        from src import windows_restart_os as win
        from src.windows_app_restart import descendants
        parents = win.process_parents()
        accounted = set()
        with self.lock:
            for key, record in self.processes.items():
                root = record['identity']
                try:
                    if key[0] not in self.managers and not record['closed']:
                        self.unknown('stdio_owner_lost', key[0], key[1])
                    current = win.identity(root['pid'])
                    if current == root or (current is None and record['closed']):
                        seeds = {root['pid'], *record['children']}
                        candidates = set().union(*(descendants(seed, parents) for seed in seeds))
                        for pid in candidates:
                            identity = win.identity(pid)
                            if identity:
                                if pid in record['children'] and record['children'][pid] != identity:
                                    self.unknown('stdio_child_incarnation_changed', key[0], key[1])
                                    continue
                                if int(identity['start_token'].split(':')[1]) < int(root['start_token'].split(':')[1]):
                                    self.unknown('stdio_ancestry_ambiguous', key[0], key[1])
                                    continue
                                record['children'][pid] = identity
                                accounted.add(pid)
                        if win.identity(root['pid']) != current:
                            self.unknown('stdio_incarnation_changed', key[0], key[1])
                    elif not record['closed']:
                        self.unknown('stdio_process_lost', key[0], key[1])
                    for pid, identity in record['children'].items():
                        if win.identity(pid) == identity:
                            accounted.add(pid)
                except OSError:
                    self.unknown('stdio_process_visibility_unknown', key[0], key[1])
        return accounted

    async def wait_idle(self, timeout):
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError('MCP drain timeout must be finite, positive and at most 300 seconds')
        deadline = time.monotonic() + timeout
        while True:
            state = self.snapshot()
            if not state['initialized'] or state['unknown']:
                return {'ok': False, 'state': 'mcp-activity-unknown', 'mcp': state}
            if not state['in_flight']:
                return {'ok': True, 'state': 'mcp-idle', 'mcp': state}
            if time.monotonic() >= deadline:
                return {'ok': False, 'state': 'mcp-drain-timeout', 'mcp': state}
            await asyncio.sleep(min(.02, max(0, deadline - time.monotonic())))

    async def close_connections(self, timeout):
        deadline = time.monotonic() + timeout
        self.accounted_process_ids()  # Freeze child incarnations before stdin closure.
        if self.snapshot()['unknown']:
            return False
        try:
            pending = asyncio.gather(*(manager.disconnect_all() for manager in list(self.managers.values())))
            done, _ = await asyncio.wait({pending}, timeout=max(0, deadline - time.monotonic()))
            if not done:
                self.unknown('mcp_connection_close_timeout')
                pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                return False
            await pending
        except (Exception, asyncio.CancelledError):
            self.unknown('mcp_connection_close_incomplete')
            return False
        while True:
            remaining = self.accounted_process_ids()
            state = self.snapshot()
            if state['unknown']:
                return False
            if not remaining:
                self.connections_closed = True
                return True
            if time.monotonic() >= deadline:
                self.unknown('mcp_process_exit_timeout')
                return False
            await asyncio.sleep(.02)


REGISTRY = Registry()
