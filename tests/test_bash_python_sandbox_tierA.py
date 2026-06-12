"""Tier-A bash/python sandbox: env allowlist, scratch workdir, resource limits.

End-to-end where it matters: we actually spawn a child with the sandbox env +
preexec and assert a secret in os.environ does NOT reach it, and that rlimits are
applied. See docs/security/bash-python-sandbox-scoping.md (F-7 / R-1 Tier A).
"""

import os
import subprocess
import sys

import pytest

from src.tool_execution import (
    _sandbox_env,
    _sandbox_preexec,
    _SANDBOX_PREEXEC,
    _SANDBOX_ENV_ALLOW,
    _AGENT_SCRATCH,
    _SANDBOX_RLIMIT_CPU_SECONDS,
)


# ── env allowlist ───────────────────────────────────────────────────────

def test_secrets_are_dropped(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-me")
    monkeypatch.setenv("ODYSSEUS_ADMIN_PASSWORD", "hunter2")
    monkeypatch.setenv("SEARXNG_SECRET", "s3cr3t")
    env = _sandbox_env(_AGENT_SCRATCH)
    assert "OPENAI_API_KEY" not in env
    assert "ODYSSEUS_ADMIN_PASSWORD" not in env
    assert "SEARXNG_SECRET" not in env


def test_benign_vars_and_home_present(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = _sandbox_env("/tmp/scratchhome")
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/tmp/scratchhome"
    assert env["TERM"] == "xterm-256color"


def test_allowlist_has_no_secret_names():
    """Guard against someone adding a secret var to the allowlist by mistake."""
    suspicious = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    for name in _SANDBOX_ENV_ALLOW:
        assert not any(s in name.upper() for s in suspicious), name


def test_opt_in_passthrough(monkeypatch):
    monkeypatch.setenv("MY_PROJECT_TOKEN", "abc")
    monkeypatch.setattr("src.settings.get_setting",
                        lambda k: ["MY_PROJECT_TOKEN"] if k == "tool_env_passthrough" else None)
    env = _sandbox_env(_AGENT_SCRATCH)
    assert env.get("MY_PROJECT_TOKEN") == "abc"


# ── real-subprocess: the secret truly doesn't reach the child ────────────

@pytest.mark.skipif(os.name != "posix", reason="POSIX preexec/env semantics")
def test_child_process_cannot_see_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    env = _sandbox_env(_AGENT_SCRATCH)
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import os; print(os.environ.get('OPENAI_API_KEY', 'ABSENT'))"],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert out.stdout.strip() == "ABSENT"


# ── real-subprocess: resource limits are applied ─────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="setrlimit is POSIX-only")
def test_rlimits_applied_in_child():
    env = _sandbox_env(_AGENT_SCRATCH)
    code = ("import resource;"
            "print(resource.getrlimit(resource.RLIMIT_CPU)[0]);"
            "print(resource.getrlimit(resource.RLIMIT_FSIZE)[0])")
    out = subprocess.run(
        [sys.executable, "-I", "-c", code],
        env=env, capture_output=True, text=True, timeout=20,
        preexec_fn=_sandbox_preexec,
    )
    cpu_soft, fsize_soft = out.stdout.split()
    assert int(cpu_soft) == _SANDBOX_RLIMIT_CPU_SECONDS
    assert int(fsize_soft) == 512 * 1024 * 1024


def test_preexec_selected_per_platform():
    if os.name == "posix":
        assert callable(_SANDBOX_PREEXEC)
    else:
        assert _SANDBOX_PREEXEC is None
