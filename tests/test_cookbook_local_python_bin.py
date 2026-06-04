"""Native-Windows installs the Python interpreter as ``python``, not
``python3``. The Cookbook's local downloader / serve / pip-fallback
runners are executed via Git Bash on Windows (the script written out
by ``shell_routes._generate_win_detached``), so a runner that
hard-codes ``python3`` fails with ``python3: command not found`` on a
default Windows install. The user has to manually create a
``python3.exe`` symlink to ``python.exe`` to get past first launch
(reported in #2341).

Resolve the binary name at script-generation time instead. The
``_local_python_bin()`` helper returns ``python`` on Windows and
``python3`` everywhere else; the ``_pip_install_fallback_chain``
default consumes that helper, so a default-arg caller in
``cookbook_routes`` (e.g. the ``huggingface_hub`` self-install line
on the local download path) produces the right invocation per
platform without each call site having to know about it.

These tests pin both halves of the fix at the import level so a
later commit that hard-codes ``python3`` back into the default
breaks the build instead of breaking real Windows users.
"""

from __future__ import annotations

import importlib

import routes.cookbook_helpers as ch


def _reload_with(monkeypatch, is_windows: bool):
    """Reload ``routes.cookbook_helpers`` with ``IS_WINDOWS`` patched on
    its dependency module. The module reads ``IS_WINDOWS`` once at
    import time to build ``_LOCAL_PIP_DEFAULT``, so the constant has to
    be rebuilt under the new flag for the platform switch to be visible
    in the function default. ``importlib.reload`` is enough — no test
    relies on object identity across reload."""
    import core.platform_compat as cp
    monkeypatch.setattr(cp, "IS_WINDOWS", is_windows, raising=False)
    return importlib.reload(ch)


def test_local_python_bin_is_python3_on_posix(monkeypatch):
    mod = _reload_with(monkeypatch, is_windows=False)
    assert mod._local_python_bin() == "python3"


def test_local_python_bin_is_plain_python_on_windows(monkeypatch):
    mod = _reload_with(monkeypatch, is_windows=True)
    assert mod._local_python_bin() == "python", (
        "Native Windows ships the v3 interpreter as `python.exe`. A "
        "`python3` invocation hits 'command not found' under Git Bash."
    )


def test_pip_install_default_uses_local_python_bin_on_windows(monkeypatch):
    """A default-arg caller (e.g. cookbook_routes.py:446 installing
    huggingface_hub on local download) must produce ``python -m pip
    install`` on Windows, not ``python3 -m pip install``."""
    mod = _reload_with(monkeypatch, is_windows=True)
    chain = mod._pip_install_fallback_chain("huggingface_hub")
    assert "python -m pip install" in chain
    assert "python3 -m pip install" not in chain


def test_pip_install_default_uses_python3_on_posix(monkeypatch):
    """Linux / macOS keep ``python3 -m pip`` — Homebrew and most distros
    ship the v3 interpreter under that name and ``python`` is either
    absent or pinned to v2."""
    mod = _reload_with(monkeypatch, is_windows=False)
    chain = mod._pip_install_fallback_chain("huggingface_hub")
    assert "python3 -m pip install" in chain


def test_pip_install_explicit_python_cmd_is_not_rewritten(monkeypatch):
    """A caller that explicitly passes ``python_cmd='pip'`` (the remote-
    runner path at cookbook_routes.py:549/554/1063) must NOT be touched
    by the Windows alias. Remote runners run on the Linux GPU box, so
    ``pip``/``python3`` are the right tokens there even when the
    Odysseus host is Windows."""
    mod = _reload_with(monkeypatch, is_windows=True)
    chain = mod._pip_install_fallback_chain("llama-cpp-python", python_cmd="pip")
    assert "pip install" in chain
    # Neither `python -m pip` nor `python3 -m pip` got substituted in for
    # the explicit `pip` argument.
    assert " -m pip install" not in chain


def test_pip_install_venv_check_uses_local_python_bin_on_windows(monkeypatch):
    """The not-in-venv fallback runs a small ``python -c`` probe to decide
    whether ``--user`` is allowed. The else branch — used when the
    caller passes a ``python_cmd`` that isn't one of the well-known
    ``python3 -m pip`` / ``pip`` / ``pip3`` shapes — must also pick the
    local binary, otherwise the probe itself hits the same not-found
    error on Windows that the parent fix was trying to avoid."""
    mod = _reload_with(monkeypatch, is_windows=True)
    chain = mod._pip_install_fallback_chain("hf_transfer", python_cmd="uv pip")
    # The venv-check expression embedded in the chain runs the local
    # Python — on Windows that's `python`, not `python3`.
    assert 'python -c "import sys' in chain
    assert 'python3 -c "import sys' not in chain
