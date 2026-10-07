"""Captured spawns exercise the production runner without signalling fake PIDs."""
import asyncio
import json
import os
from types import SimpleNamespace

from src import containment


def capture_owned_spawn(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_REPORT_ONLY)
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path.parent / (tmp_path.name + "-control") / "grants.json")
    monkeypatch.setattr(containment, "_pgid_of", lambda pid: pid)

    async def fake_exec(*argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs, command=argv[-1])
        if "--info-fd" in argv:
            fd = int(argv[argv.index("--info-fd") + 1])
            os.write(fd, json.dumps({"child-pid": 99999998}).encode())
        stdout = asyncio.StreamReader()
        if "ody-boundary" in argv:
            stdout.feed_data((argv[argv.index("ody-boundary") + 1] + "\n").encode())
        stdout.feed_data(b"ok")
        stdout.feed_eof()
        stderr = asyncio.StreamReader()
        stderr.feed_eof()
        async def wait():
            return 0
        async def drain():
            return None
        writer = SimpleNamespace(write=lambda data: None, drain=drain,
                                 close=lambda: captured.update(stdin_closed=True))
        return SimpleNamespace(pid=99999999, stdout=stdout, stderr=stderr,
                               stdin=writer, returncode=0, wait=wait)

    async def release(*args, **kwargs):
        return containment.ReleaseOutcome(dead=True, escalated=False)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    real_capture = containment.process_ownership.capture
    monkeypatch.setattr(containment.process_ownership, "capture", lambda pid:
        {"pid": pid, "start_token": "captured-fake"} if pid in (99999998, 99999999) else real_capture(pid))
    if hasattr(containment.os, "pidfd_open"):
        monkeypatch.delattr(containment.os, "pidfd_open")
    monkeypatch.setattr(containment, "_release_awaited", release)
    return captured
