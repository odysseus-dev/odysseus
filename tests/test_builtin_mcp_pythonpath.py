"""Issue #5116 — built-in Python MCP servers must not drop an inherited PYTHONPATH.

``src/builtin_mcp.py`` and ``src/mcp_manager.py`` previously built the subprocess
environment as ``{"PYTHONPATH": base_dir}``. Because ``McpManager._connect_stdio``
merges that as ``{**os.environ, **env}``, the single ``PYTHONPATH`` key replaced
the inherited value wholesale — so source/Nix/container launches that rely on
environment-provided import paths lost them and the built-in server failed to
import (surfacing as e.g. "MCP server not connected: email").

``_builtin_python_env`` now prepends the app root to the inherited ``PYTHONPATH``
and de-duplicates while preserving order. The helper is exercised here in
isolation (same loader the bg-task tests use), so no real servers are spawned.
"""

import importlib.util
import os
import sys
import types
from pathlib import Path


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
        "builtin_mcp_pythonpath_under_test",
        ROOT / "src" / "builtin_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_prepends_app_root_to_empty_inherited_pythonpath(monkeypatch):
    """With no inherited PYTHONPATH the app root is the sole entry."""
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = builtin_mcp._builtin_python_env("/opt/odysseus")

    assert env == {"PYTHONPATH": "/opt/odysseus"}


def test_preserves_inherited_pythonpath_entries(monkeypatch):
    """Inherited entries must survive — they carry env-provided import paths."""
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    inherited = os.pathsep.join(["/nix/store/extra/lib", "/opt/venv/site-packages"])
    monkeypatch.setenv("PYTHONPATH", inherited)

    env = builtin_mcp._builtin_python_env("/opt/odysseus")

    parts = env["PYTHONPATH"].split(os.pathsep)
    # App root first so it wins on import conflicts ...
    assert parts[0] == "/opt/odysseus"
    # ... but every inherited entry is still present.
    assert "/nix/store/extra/lib" in parts
    assert "/opt/venv/site-packages" in parts
    assert len(parts) == 3


def test_dedupes_app_root_already_present(monkeypatch):
    """If the app root is already on PYTHONPATH, it is not duplicated."""
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/opt/odysseus", "/other"]))

    env = builtin_mcp._builtin_python_env("/opt/odysseus")

    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts.count("/opt/odysseus") == 1
    assert parts[0] == "/opt/odysseus"
    assert "/other" in parts


def test_drops_empty_and_duplicate_segments(monkeypatch):
    """Empty segments from a trailing/leading separator are ignored."""
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/a", "", "/b", "/a"]))

    env = builtin_mcp._builtin_python_env("/root")

    assert env["PYTHONPATH"].split(os.pathsep) == ["/root", "/a", "/b"]
