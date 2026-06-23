"""Issue #4592 — built-in MCP startup must not leak tasks or subprocesses.

Two defects in src/builtin_mcp.py:
  * `register_builtin_servers` scheduled its python/npx connect coroutines with
    a bare `asyncio.create_task(...)` whose return value was dropped. asyncio
    keeps only a weak reference to such tasks, so the GC can collect one
    mid-flight and the server silently never registers.
  * `_is_npx_package_cached` killed its `npx --version` probe subprocess on
    `TimeoutError` but not on `CancelledError`, so a cancellation (e.g. app
    shutdown) orphaned the child.

Both are exercised here with the module loaded in isolation (the same loader
the existing npx-cache tests use), so no real servers or npx are involved.
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_builtin_mcp(monkeypatch):
    core = types.ModuleType("core")
    core.__path__ = []
    platform_compat = types.ModuleType("core.platform_compat")
    platform_compat.IS_WINDOWS = False
    platform_compat.which_tool = lambda name: None
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.platform_compat", platform_compat)

    spec = importlib.util.spec_from_file_location(
        "builtin_mcp_under_test",
        ROOT / "src" / "builtin_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def test_spawn_bg_holds_strong_ref_until_task_finishes(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    task = builtin_mcp._spawn_bg(work())
    await started.wait()
    # While the task is in flight it must be reachable from the module-level
    # set — that strong reference is what keeps the GC from collecting it.
    assert task in builtin_mcp._BG_TASKS

    release.set()
    await task
    await asyncio.sleep(0)  # let the done-callback run
    # Once finished it is discarded so the set doesn't grow without bound.
    assert task not in builtin_mcp._BG_TASKS


async def test_npx_probe_reaps_subprocess_on_cancel(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    # Force the code past the fast cache hit so it spawns the probe subprocess.
    monkeypatch.setattr(builtin_mcp, "_is_package_in_npx_cache", lambda spec: False)

    state = {"killed": False, "waited": False}
    started = asyncio.Event()

    class FakeProc:
        returncode = None

        async def communicate(self):
            started.set()
            await asyncio.sleep(3600)  # block until the probe is cancelled

        def kill(self):
            state["killed"] = True

        async def wait(self):
            state["waited"] = True

    async def fake_create(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", fake_create)

    task = asyncio.create_task(
        builtin_mcp._is_npx_package_cached("npx", "some-pkg@1.0.0", timeout_s=3600)
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The child was killed and reaped rather than orphaned.
    assert state["killed"] is True
    assert state["waited"] is True
