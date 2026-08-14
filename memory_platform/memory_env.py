#!/usr/bin/env python3
"""memory_env.py — portable path resolution for the memory system.

Every path in the package is derived from a small set of roots that can be
overridden via environment variables, so the same code runs on ANY machine
(no hardcoded home paths, no machine-specific assumptions):

  MEMORY_CONFIG_DIR    config root (default ~/.config/opencode for back-compat)
  MEMORY_MEMORY_DIR    memory data root (default <config>/memory)
  MEMORY_PYTHON        interpreter to run memory scripts (default: the
                       interpreter currently running this module, else
                       `python3` from PATH)
  MEMORY_SCRIPTS_DIR   where the scripts live (default <config>/scripts)
  MEMORY_STORE_DB      explicit store path (default <memory>/store/memory.db)

The older OPENCODE_* names are still honoured as fallbacks so existing
installations keep working unchanged.

Rules:
  - Never hardcode a user path; always go through these helpers.
  - Scripts invoked as `python <script>` will use sys.executable for child
    processes (same venv), which is portable by construction.
  - A plugin/harness can set MEMORY_PYTHON if it needs a specific interpreter.
"""

import os
import shutil
import sys


def expand(p):
    """expanduser + expandvars, for env values that may contain ~ or $HOME."""
    if not p:
        return p
    return os.path.expanduser(os.path.expandvars(p))


def config_dir():
    return expand(os.environ.get(
        "MEMORY_CONFIG_DIR",
        os.environ.get(
            "OPENCODE_CONFIG_DIR",
            os.path.join(os.path.expanduser("~"), ".config", "opencode"))))


def memory_dir():
    return expand(os.environ.get(
        "MEMORY_MEMORY_DIR",
        os.environ.get(
            "OPENCODE_MEMORY_DIR",
            os.path.join(config_dir(), "memory"))))


def scripts_dir():
    return expand(os.environ.get(
        "MEMORY_SCRIPTS_DIR",
        os.path.join(config_dir(), "scripts")))


def store_db():
    return expand(os.environ.get(
        "MEMORY_STORE_DB",
        os.path.join(memory_dir(), "store", "memory.db")))


def python_bin():
    """The interpreter used to run memory scripts in subprocesses.

    Prefers the dedicated memory venv (has sqlite-vec, FTS5 deps) when present;
    falls back to the interpreter currently running this module, then `python3`.
    This keeps the store working whether invoked from cron (system python) or
    from a plugin (memory venv)."""
    env = os.environ.get("MEMORY_PYTHON")
    if env:
        return expand(env)
    venv = os.path.expanduser("~/.venvs/memory/bin/python3")
    if os.path.exists(venv) and os.access(venv, os.X_OK):
        return venv
    return sys.executable or shutil.which("python3") or "python3"


def harness_bin():
    """The host harness binary, resolved from PATH with an env override."""
    env = os.environ.get("HARNESS_BIN")
    if env:
        return expand(env)
    return shutil.which("odysseus") or shutil.which("opencode") or "opencode"
