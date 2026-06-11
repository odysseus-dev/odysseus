import asyncio
import importlib.util
from pathlib import Path


_SERVICE_PATH = Path(__file__).resolve().parents[1] / "services" / "shell" / "service.py"
_SPEC = importlib.util.spec_from_file_location("_shell_service_under_test", _SERVICE_PATH)
shell_service = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(shell_service)
ShellService = shell_service.ShellService


class _FakeStream:
    def __init__(self, lines):
        self._lines = [line.encode() for line in lines]

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeProcess:
    def __init__(self):
        self.stdout = _FakeStream(["hello\n"])
        self.stderr = _FakeStream([])
        self.returncode = 0

    async def wait(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_shell_stream_uses_running_loop_for_deadline(monkeypatch):
    async def fake_create_subprocess_shell(*args, **kwargs):
        return _FakeProcess()

    def fail_get_event_loop():
        raise AssertionError("stream should use the active running loop")

    monkeypatch.setattr(
        shell_service.asyncio,
        "create_subprocess_shell",
        fake_create_subprocess_shell,
    )
    monkeypatch.setattr(shell_service.asyncio, "get_event_loop", fail_get_event_loop)

    async def collect_events():
        service = ShellService()
        return [event async for event in service.stream("unused", timeout=5)]

    events = asyncio.run(collect_events())

    assert events == [
        {"stream": "stdout", "data": "hello"},
        {"exit_code": 0},
    ]
