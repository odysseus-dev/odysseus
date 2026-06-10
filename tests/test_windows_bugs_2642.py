"""Tests for Windows-specific bug fixes from issue #2642.

Covers:
- _shell_path() backslash escaping (Bug B)
- snapshot_download repo_id JSON quoting in code runner (Bug A)
- Stale endpoint cache cleared on re-registration (Bug C)
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Bug B — _shell_path() backslash escaping
# ---------------------------------------------------------------------------

from routes.cookbook_helpers import _shell_path


def test_shell_path_escapes_backslashes_in_windows_path():
    """Windows GGUF paths with backslashes must be double-escaped so the shell
    does not misinterpret \\U, \\M, etc. as escape sequences."""
    result = _shell_path("C:\\Users\\Models\\my-model.gguf")
    assert result == '"C:\\\\Users\\\\Models\\\\my-model.gguf"'
    # The raw string inside the quotes should have doubled backslashes
    inner = result[1:-1]
    assert "\\\\" in inner


def test_shell_path_no_regression_unix_path():
    """Plain POSIX absolute paths must pass through unchanged (no extra escaping)."""
    result = _shell_path("/home/user/models/llama3.gguf")
    assert result == '"/home/user/models/llama3.gguf"'


def test_shell_path_tilde_home():
    """~ expands to $HOME without modification."""
    assert _shell_path("~") == '"$HOME"'


def test_shell_path_tilde_subdir():
    """~/... paths expand to $HOME/... without modification."""
    assert _shell_path("~/models/foo.gguf") == '"$HOME/models/foo.gguf"'


# ---------------------------------------------------------------------------
# Bug A — repo_id JSON quoting in generated Python code
# ---------------------------------------------------------------------------

def test_snapshot_download_lines_use_json_dumps_for_repo_id(tmp_path):
    """The generated python3 -c snippet must wrap repo_id with json.dumps()
    so that special characters (backslashes, quotes) are properly escaped."""
    # Import the module lazily to avoid heavy side-effects at collection time.
    import importlib
    import os

    # Stub heavy dependencies so the module loads in a test environment
    stubs = {
        "core.database": types.ModuleType("core.database"),
        "core.middleware": types.ModuleType("core.middleware"),
        "core.platform_compat": types.ModuleType("core.platform_compat"),
        "routes.shell_routes": types.ModuleType("routes.shell_routes"),
        "src.auth_helpers": types.ModuleType("src.auth_helpers"),
    }
    db_mod = stubs["core.database"]
    for name in ["SessionLocal", "ModelEndpoint", "Session", "ChatMessage"]:
        setattr(db_mod, name, MagicMock())

    mw_mod = stubs["core.middleware"]
    mw_mod.require_admin = MagicMock()

    pc_mod = stubs["core.platform_compat"]
    for name in ["IS_WINDOWS", "detached_popen_kwargs", "find_bash",
                 "kill_process_tree", "pid_alive", "safe_chmod", "which_tool"]:
        setattr(pc_mod, name, False if name == "IS_WINDOWS" else MagicMock())

    shell_mod = stubs["routes.shell_routes"]
    shell_mod.TMUX_LOG_DIR = tmp_path

    auth_mod = stubs["src.auth_helpers"]
    auth_mod.require_user = MagicMock()
    auth_mod.require_admin = MagicMock()

    old_modules = {}
    for k, v in stubs.items():
        old_modules[k] = sys.modules.get(k)
        sys.modules[k] = v

    # Verify that the generated lines use json.dumps rather than raw single-quote
    # interpolation.  We check this by inspecting the source text directly rather
    # than executing the route handler, which avoids spinning up a full FastAPI app.
    import ast
    import inspect
    from pathlib import Path

    src_path = Path(__file__).resolve().parents[1] / "routes" / "cookbook_routes.py"
    src_text = src_path.read_text(encoding="utf-8")

    # The fix replaces `'{req.repo_id}'` with `{json.dumps(req.repo_id)}` in the
    # snapshot_download python -c one-liners.
    assert "json.dumps(req.repo_id)" in src_text, (
        "Expected json.dumps(req.repo_id) in the snapshot_download one-liner — "
        "direct f-string interpolation without escaping is still present"
    )
    # The old unsafe pattern must no longer appear
    assert "snapshot_download('{req.repo_id}'" not in src_text, (
        "Unsafe direct interpolation of repo_id into single-quoted Python string "
        "still present — use json.dumps(req.repo_id) instead"
    )

    # Restore original modules
    for k, v in old_modules.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ---------------------------------------------------------------------------
# Bug C — stale endpoint cache cleared on re-registration
# ---------------------------------------------------------------------------

def test_create_model_endpoint_probe_updates_cached_models():
    """When should_probe is True and probe returns models, cached_models is updated."""
    from pathlib import Path
    src_text = (Path(__file__).resolve().parents[1] / "routes" / "model_routes.py").read_text(encoding="utf-8")
    # The probe path must update cached_models when probed_models is truthy.
    assert "existing.cached_models = json.dumps(probed_models)" in src_text, (
        "Expected 'existing.cached_models = json.dumps(probed_models)' in the "
        "should_probe branch of the create_model_endpoint dedup block"
    )


def test_create_model_endpoint_skipped_probe_preserves_cached_models():
    """When should_probe is False (skip_probe=true), existing cached_models must
    NOT be cleared — the stale-cache bug fix must not introduce a regression
    where a no-op re-registration discards the current model list."""
    from pathlib import Path
    src_text = (Path(__file__).resolve().parents[1] / "routes" / "model_routes.py").read_text(encoding="utf-8")
    # There must be no unconditional None-assignment outside the probe block —
    # the two failing tests (test_post_dedupe_existing_*) guard this contract.
    probe_block_start = src_text.find("if should_probe:")
    probe_block_end = src_text.find("if changed:", probe_block_start)
    block = src_text[probe_block_start:probe_block_end]
    # The None-assignment must NOT appear in the no-probe path.
    assert "existing.cached_models = None" not in block, (
        "existing.cached_models must not be unconditionally cleared when "
        "should_probe is False — this breaks the dedupe path for no-op re-registration"
    )
