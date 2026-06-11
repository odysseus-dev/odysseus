import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys
import types


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


def test_npx_package_from_args_prefers_package_after_y_flag(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    assert builtin_mcp._npx_package_from_args(
        ["-y", "@playwright/mcp@latest", "--headless"]
    ) == "@playwright/mcp@latest"


def test_npx_cache_check_falls_back_when_async_subprocess_is_unsupported(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    async def unsupported_exec(*args, **kwargs):
        raise NotImplementedError("subprocess transport unavailable")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout=b"1.2.3\n", stderr=b"")

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unsupported_exec)
    monkeypatch.setattr(builtin_mcp.subprocess, "run", fake_run)

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx.cmd",
            "@playwright/mcp@latest",
            timeout_s=2,
        )
    ) is True
    assert captured["args"] == [
        "npx.cmd",
        "--no-install",
        "@playwright/mcp@latest",
        "--version",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["timeout"] == 2


def test_npx_cache_check_fallback_treats_timeout_as_cache_miss(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    async def unsupported_exec(*args, **kwargs):
        raise NotImplementedError("subprocess transport unavailable")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unsupported_exec)
    monkeypatch.setattr(builtin_mcp.subprocess, "run", fake_run)

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx.cmd",
            "@playwright/mcp@latest",
            timeout_s=2,
        )
    ) is False
