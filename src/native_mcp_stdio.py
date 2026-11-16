"""Observe SDK-owned stdio processes and prohibit its force-cleanup fallback.

A context-local owner scopes the adapter to McpManager connections. Unsupported
SDK internals become unknown evidence, not permission for automatic restart.
"""
import threading
import os
from importlib.metadata import version
from typing import Any, Awaitable, Callable, cast

from src.native_mcp_activity import CONNECTING

_LOCK = threading.Lock()


class OwnedProcess:
    def __init__(self, process, registry, owner, server):
        self.process, self.registry, self.owner, self.server = process, registry, owner, server
        job = getattr(process, '_job_object', None)
        if job:
            try:
                from mcp.os.win32.utilities import win32job
                info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                info['BasicLimitInformation']['LimitFlags'] &= ~win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
                flags = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                if flags['BasicLimitInformation']['LimitFlags'] & win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
                    raise RuntimeError('Owned MCP job retained force-cleanup behavior')
            except Exception:
                registry.unknown('stdio_job_cleanup_unknown', owner, server)
        self.record = registry.process_opened(owner, server, process)

    def __getattr__(self, name):
        return getattr(self.process, name)

    @property
    def returncode(self):
        popen = getattr(self.process, 'popen', None)
        return popen.poll() if popen is not None else self.process.returncode

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self.returncode is None:
            self.registry.unknown('stdio_cleanup_incomplete', self.owner, self.server)
            return False  # Never call Process.aclose() while live: it can kill.
        close = getattr(self.process, 'aclose', None)
        if close:
            await close()
        else:  # SDK's Windows SelectorEventLoop fallback has no aclose().
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    await stream.aclose()
        self.registry.process_closed(self.record)
        return False


def install(registry):
    if os.name != 'nt':
        return
    if version('mcp') != '1.30.0':
        registry.unknown('stdio_sdk_unsupported')
        return
    import mcp.client.stdio as sdk
    with _LOCK:
        existing = getattr(sdk, '_odysseus_owned_factory', None)
        if existing is not None:
            if (sdk._create_platform_compatible_process is not existing or
                    sdk._terminate_process_tree is not sdk._odysseus_owned_terminate):
                registry.unknown('stdio_observer_replaced')
            return
        factory: Any = getattr(sdk, '_create_platform_compatible_process', None)
        terminate: Any = getattr(sdk, '_terminate_process_tree', None)
        if not callable(factory) or not callable(terminate):
            registry.unknown('stdio_sdk_unsupported')
            return
        spawn = cast(Callable[..., Awaitable[Any]], factory)
        terminate_unowned = cast(Callable[..., Awaitable[Any]], terminate)

        async def create(*args, **kwargs):
            owner = CONNECTING.get()
            if owner is None:
                from src.native_mcp_activity import REGISTRY, AdmissionClosed
                REGISTRY.unknown('untracked_stdio_creation')
                if REGISTRY.snapshot()['draining']:
                    raise AdmissionClosed('Untracked stdio creation refused while draining')
                return await spawn(*args, **kwargs)
            current, manager, server = owner
            current.require_operation()
            try:
                process = await spawn(*args, **kwargs)
            except BaseException:
                current.unknown('stdio_process_creation_unknown', manager, server)
                raise
            return OwnedProcess(process, current, manager, server)

        async def no_force(process):
            if isinstance(process, OwnedProcess):
                process.registry.unknown('stdio_cleanup_timeout', process.owner, process.server)
                raise TimeoutError('Owned MCP stdio did not exit; force cleanup prohibited')
            return await terminate_unowned(process)

        sdk._create_platform_compatible_process = create
        sdk._terminate_process_tree = no_force
        sdk._odysseus_owned_factory = create
        sdk._odysseus_owned_terminate = no_force


def validate(registry):
    if registry.processes:
        import mcp.client.stdio as sdk
        if (getattr(sdk, '_odysseus_owned_factory', None) is not sdk._create_platform_compatible_process or
                getattr(sdk, '_odysseus_owned_terminate', None) is not sdk._terminate_process_tree):
            registry.unknown('stdio_observer_replaced')
