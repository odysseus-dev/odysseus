"""Tests for shell_routes.py helpers."""

import builtins
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from routes.shell_routes import _find_line_break


def test_shell_routes_import_without_posix_pty_modules(monkeypatch):
    """Native Windows has no fcntl/termios; importing routes must still work."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"fcntl", "pty"}:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cached_modules = {name: sys.modules.pop(name, None) for name in ("fcntl", "pty")}

    module_path = Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py"
    spec = importlib.util.spec_from_file_location("_shell_routes_without_pty", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
        for name, cached_module in cached_modules.items():
            if cached_module is not None:
                sys.modules[name] = cached_module

    assert module.PTY_SUPPORTED is False
    assert module._find_line_break(b"ok\n") == (2, 1)


async def test_generate_pty_reports_explicit_unsupported_error(monkeypatch):
    """Clients can distinguish unsupported PTY mode from process failures."""
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "PTY_SUPPORTED", False)
    monkeypatch.setattr(shell_routes, "_PTY_IMPORT_ERROR", ImportError("No module named 'termios'"))

    request = SimpleNamespace(is_disconnected=lambda: False)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in shell_routes._generate_pty("echo hi", 5, request)
    ]

    assert events == [
        {
            "stream": "stderr",
            "data": "PTY streaming is not supported on this platform: No module named 'termios'",
            "error": shell_routes.PTY_UNSUPPORTED_ERROR,
        },
        {"exit_code": -1, "error": shell_routes.PTY_UNSUPPORTED_ERROR},
    ]


class TestFindLineBreak:
    """Test line-break detection in byte buffers."""

    def test_newline(self):
        assert _find_line_break(b"hello\nworld") == (5, 1)

    def test_crlf(self):
        assert _find_line_break(b"hello\r\nworld") == (5, 2)

    def test_cr_only(self):
        assert _find_line_break(b"hello\rworld") == (5, 1)

    def test_no_breaks(self):
        assert _find_line_break(b"no breaks") == (-1, 0)

    def test_empty(self):
        assert _find_line_break(b"") == (-1, 0)

    def test_leading_newline(self):
        assert _find_line_break(b"\n") == (0, 1)

    def test_leading_cr(self):
        assert _find_line_break(b"\r") == (0, 1)

    def test_leading_crlf(self):
        assert _find_line_break(b"\r\n") == (0, 2)

    def test_multiple_newlines(self):
        """Should find the first one."""
        assert _find_line_break(b"a\nb\nc") == (1, 1)

    def test_cr_before_newline_not_adjacent(self):
        """\\r at pos 2, \\n at pos 5 — not CRLF, should return \\r pos."""
        assert _find_line_break(b"ab\rcd\n") == (2, 1)

    def test_newline_before_cr(self):
        """\\n comes before \\r — should return \\n."""
        assert _find_line_break(b"ab\ncd\r") == (2, 1)
