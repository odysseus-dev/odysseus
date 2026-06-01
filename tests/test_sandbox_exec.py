"""Optional sandboxed code execution.

bash/python tool calls run on the host by default (unchanged). When
ODYSSEUS_SANDBOX=1 and a container runtime exists, they run inside a hardened
throwaway container instead. These tests lock in: off-by-default, the runtime
gate, and the hardening flags.
"""

import importlib
import os

import src.tool_execution as te


def _reload(**env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(te)


def test_sandbox_off_by_default(monkeypatch):
    """No env var -> no sandbox, even if podman is on PATH (opt-in only)."""
    monkeypatch.setattr(te._shutil, "which", lambda x: "/usr/bin/podman")
    m = _reload(ODYSSEUS_SANDBOX=None)
    monkeypatch.setattr(m._shutil, "which", lambda x: "/usr/bin/podman")
    assert m._sandbox_runtime() is None


def test_sandbox_needs_a_runtime(monkeypatch):
    """Enabled but no podman/docker -> None (falls back to host, no crash)."""
    m = _reload(ODYSSEUS_SANDBOX="1")
    monkeypatch.setattr(m._shutil, "which", lambda x: None)
    assert m._sandbox_runtime() is None


def test_sandbox_picks_podman_then_docker(monkeypatch):
    m = _reload(ODYSSEUS_SANDBOX="1")
    monkeypatch.setattr(m._shutil, "which", lambda x: "/usr/bin/" + x if x == "podman" else None)
    assert m._sandbox_runtime() == "podman"
    monkeypatch.setattr(m._shutil, "which", lambda x: "/usr/bin/" + x if x == "docker" else None)
    assert m._sandbox_runtime() == "docker"


def test_wrapper_has_hardening_flags():
    m = _reload(ODYSSEUS_SANDBOX="1")
    argv = m._wrap_in_sandbox("podman", ["python", "-I", "-c", "print(1)"])
    joined = " ".join(argv)
    assert argv[:3] == ["podman", "run", "--rm"]
    assert "--network none" in joined          # no network by default
    assert "--read-only" in joined             # read-only root fs
    assert "--cap-drop ALL" in joined          # all capabilities dropped
    assert "no-new-privileges" in joined       # no privilege escalation
    assert "--memory" in argv and "--cpus" in argv and "--pids-limit" in argv
    assert "65534:65534" in joined             # non-root (nobody)
    # the user's code argv is preserved at the end
    assert argv[-4:] == ["python", "-I", "-c", "print(1)"]


def test_wrapper_optional_network():
    m = _reload(ODYSSEUS_SANDBOX="1")
    off = " ".join(m._wrap_in_sandbox("podman", ["bash", "-lc", "x"]))
    on = " ".join(m._wrap_in_sandbox("podman", ["bash", "-lc", "x"], network=True))
    assert "--network none" in off
    assert "--network bridge" in on


def _restore():
    os.environ.pop("ODYSSEUS_SANDBOX", None)
    importlib.reload(te)


def teardown_module(module):
    _restore()
