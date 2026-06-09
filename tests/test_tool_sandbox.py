"""Unit tests for the optional container sandbox (src/tool_sandbox.py).

Phase 1 of docs/sandbox-hardening-roadmap.md. These run on any OS with no Docker:
the spawn/stream layer is monkeypatched, so nothing actually launches a container.
Coroutines are driven via asyncio.run() to avoid a pytest-asyncio dependency.
"""

import asyncio

import pytest

from src import tool_sandbox


# --------------------------------------------------------------------------- #
# argv builder: every hardening flag must be present, no host env may leak.
# --------------------------------------------------------------------------- #

def _env_pairs(argv):
    """Extract the values of every `-e KEY=VALUE` flag in argv."""
    out = []
    for i, tok in enumerate(argv):
        if tok == "-e" and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def test_argv_has_all_hardening_flags():
    argv = tool_sandbox._build_docker_argv("python", "odysseus-tool-test", "print(1)")
    joined = " ".join(argv)

    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--user" in argv and "10001:10001" in argv
    assert "--read-only" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "no-new-privileges:true" in argv
    assert "--pids-limit" in argv
    assert "--memory" in argv and "--memory-swap" in argv
    assert "--cpus" in argv
    assert "--network" in argv
    assert "--restart" in argv
    assert "/tmp:rw,noexec,nosuid,size=" in joined          # tmpfs noexec/nosuid
    assert "--ulimit" in argv
    assert any(a.startswith("nofile=") for a in argv)
    assert any(a.startswith("nproc=") for a in argv)


def test_memory_equals_memory_swap_disables_swap():
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")
    mem = argv[argv.index("--memory") + 1]
    swap = argv[argv.index("--memory-swap") + 1]
    assert mem == swap  # equal => swap disabled (spec section 4.4)


def test_argv_passes_only_explicit_env_no_host_leak(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_FAKE_SECRET", "supersecret-should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgres://leak")
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")

    # No host secret may appear anywhere in the argv.
    assert not any("supersecret-should-not-leak" in a for a in argv)
    assert not any("postgres://leak" in a for a in argv)

    # The only -e values are the curated sandbox env keys.
    pairs = _env_pairs(argv)
    keys = {p.split("=", 1)[0] for p in pairs}
    assert keys == set(tool_sandbox._SANDBOX_ENV.keys())
    assert "DATABASE_URL" not in keys


def test_argv_python_uses_image_and_isolated_flags():
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")
    # Trailing args: <image> -I -c <code>
    assert argv[-3:] == ["-I", "-c", "print(1)"]
    assert argv[-4] == tool_sandbox._cfg("tool_sandbox_image")


def test_argv_bash_uses_shell_image_and_c_flag():
    argv = tool_sandbox._build_docker_argv("bash", "n", "echo hi")
    assert argv[-2:] == ["-c", "echo hi"]
    assert argv[-3] == tool_sandbox._cfg("tool_sandbox_shell_image")


def test_runtime_flag_omitted_for_runc_present_for_runsc(monkeypatch):
    # Default is runc => no --runtime (runc is the daemon default).
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")
    assert "--runtime" not in argv

    monkeypatch.setenv("TOOL_SANDBOX_RUNTIME", "runsc")
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")
    assert "--runtime" in argv
    assert argv[argv.index("--runtime") + 1] == "runsc"


def test_seccomp_profile_referenced_when_repo_artifact_present():
    # The repo ships docker/sandbox/seccomp-tenant.json, so the flag should appear.
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")
    assert any(a.startswith("seccomp=") and a.endswith("seccomp-tenant.json") for a in argv)


def test_overrides_apply(monkeypatch):
    monkeypatch.setenv("TOOL_SANDBOX_MEMORY", "256m")
    monkeypatch.setenv("TOOL_SANDBOX_PIDS", "64")
    monkeypatch.setenv("TOOL_SANDBOX_NETWORK", "tenant-net")
    argv = tool_sandbox._build_docker_argv("python", "n", "print(1)")
    assert argv[argv.index("--memory") + 1] == "256m"
    assert argv[argv.index("--pids-limit") + 1] == "64"
    assert argv[argv.index("--network") + 1] == "tenant-net"


# --------------------------------------------------------------------------- #
# sandbox_enabled() gating: only on when ALL conditions hold.
# --------------------------------------------------------------------------- #

def _all_conditions_met(monkeypatch):
    monkeypatch.setenv("TOOL_SANDBOX", "container")
    monkeypatch.delenv("ODYSSEUS_DESKTOP", raising=False)
    monkeypatch.setattr(tool_sandbox, "_docker_available", lambda: True)


def test_enabled_when_all_conditions_met(monkeypatch):
    _all_conditions_met(monkeypatch)
    assert tool_sandbox.sandbox_enabled() is True


def test_disabled_when_flag_off(monkeypatch):
    _all_conditions_met(monkeypatch)
    monkeypatch.setenv("TOOL_SANDBOX", "off")
    assert tool_sandbox.sandbox_enabled() is False


def test_disabled_on_desktop(monkeypatch):
    _all_conditions_met(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_DESKTOP", "1")
    assert tool_sandbox.sandbox_enabled() is False


def test_disabled_when_docker_absent(monkeypatch):
    _all_conditions_met(monkeypatch)
    monkeypatch.setattr(tool_sandbox, "_docker_available", lambda: False)
    assert tool_sandbox.sandbox_enabled() is False


# --------------------------------------------------------------------------- #
# run_sandboxed(): result shaping, with the spawn/stream layer mocked.
# --------------------------------------------------------------------------- #

class _DummyProc:
    pass


def _mock_stream(monkeypatch, result):
    async def fake_exec(*a, **k):
        return _DummyProc()

    async def fake_stream(proc, *, timeout, progress_cb=None):
        return result

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("src.tool_execution._run_subprocess_streaming", fake_stream)


def test_run_sandboxed_success(monkeypatch):
    _mock_stream(monkeypatch, ("hello\n", "", 0, False))
    out = asyncio.run(tool_sandbox.run_sandboxed("python", "print('hello')", timeout=5))
    assert out == {"output": "hello", "exit_code": 0}


def test_run_sandboxed_merges_stderr(monkeypatch):
    _mock_stream(monkeypatch, ("out", "a warning", 0, False))
    out = asyncio.run(tool_sandbox.run_sandboxed("bash", "echo out 1>&2", timeout=5))
    assert out["exit_code"] == 0
    assert "STDERR: a warning" in out["output"]


def test_run_sandboxed_timeout_reaps_container(monkeypatch):
    _mock_stream(monkeypatch, ("partial", "", None, True))
    removed = {}

    async def fake_remove(name):
        removed["name"] = name

    monkeypatch.setattr(tool_sandbox, "_force_remove", fake_remove)
    out = asyncio.run(tool_sandbox.run_sandboxed("python", "while True: pass", timeout=3))
    assert out["exit_code"] == 124
    assert "timed out" in out["error"]
    assert removed.get("name", "").startswith("odysseus-tool-")  # container was reaped


def test_run_sandboxed_rejects_oversized_code():
    big = "x" * (tool_sandbox._MAX_CODE_BYTES + 1)
    out = asyncio.run(tool_sandbox.run_sandboxed("python", big, timeout=5))
    assert out["exit_code"] == 1
    assert "too large" in out["error"]


def test_run_sandboxed_rejects_unknown_tool():
    out = asyncio.run(tool_sandbox.run_sandboxed("rm-rf", "whatever", timeout=5))
    assert out["exit_code"] == 1


def test_run_sandboxed_emits_audit_log_without_code(monkeypatch, caplog):
    import logging

    _mock_stream(monkeypatch, ("ok", "", 0, False))
    with caplog.at_level(logging.INFO, logger="src.tool_sandbox"):
        asyncio.run(tool_sandbox.run_sandboxed("python", "print('SECRET_CODE')", timeout=5))
    assert "spawning python sandbox" in caplog.text   # audit line emitted
    assert "SECRET_CODE" not in caplog.text            # code/command never logged


# --------------------------------------------------------------------------- #
# Delegation path: _direct_fallback routes to run_sandboxed when enabled.
# --------------------------------------------------------------------------- #

def test_direct_fallback_delegates_when_enabled(monkeypatch):
    from src import tool_execution

    sentinel = {"output": "SANDBOXED", "exit_code": 0}

    async def fake_run(tool, content, *, timeout, progress_cb=None):
        return sentinel

    monkeypatch.setattr(tool_sandbox, "sandbox_enabled", lambda: True)
    monkeypatch.setattr(tool_sandbox, "run_sandboxed", fake_run)

    for tool in ("bash", "python"):
        out = asyncio.run(tool_execution._direct_fallback(tool, "echo hi"))
        assert out == sentinel


def test_direct_fallback_skips_sandbox_when_disabled(monkeypatch):
    from src import tool_execution

    async def boom(*a, **k):
        raise AssertionError("run_sandboxed must not be called when disabled")

    monkeypatch.setattr(tool_sandbox, "sandbox_enabled", lambda: False)
    monkeypatch.setattr(tool_sandbox, "run_sandboxed", boom)

    # read_file never touches the sandbox; proves disabled path doesn't route there
    # and returns a normal tool result dict.
    out = asyncio.run(tool_execution._direct_fallback("read_file", "/nonexistent/path/xyz"))
    assert isinstance(out, dict) and "exit_code" in out
