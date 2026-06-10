"""Isolated Docker code sandbox: command construction, guard rails, and (when
Docker is present) a real end-to-end run.

The unit tests are Docker-free — ``docker_available`` / ``image_exists`` are
monkeypatched and ``subprocess`` is never invoked. The integration tests only
run when Docker is installed and ``ODYSSEUS_SANDBOX_IT=1`` is set, so the suite
stays fast and hermetic by default.
"""
import os

import pytest

from src import sandbox_manager as sb


# ── command construction ──────────────────────────────────────────────────

def test_lang_runner_mapping():
    assert sb.LANG_RUNNERS["python"][1] == ["python3", "/sandbox/snippet.py"]
    assert sb.LANG_RUNNERS["node"][1] == ["node", "/sandbox/snippet.js"]
    assert sb.LANG_RUNNERS["bash"][1] == ["bash", "/sandbox/snippet.sh"]
    # aliases resolve to the same runner
    assert sb.LANG_RUNNERS["py"] == sb.LANG_RUNNERS["python"]
    assert sb.LANG_RUNNERS["js"] == sb.LANG_RUNNERS["node"]


def test_docker_run_argv_is_hardened():
    argv = sb._docker_run_argv(
        "c1", ["python3", "/sandbox/snippet.py"], "/host/dir",
        network=False, memory="256m", cpus="0.5", pids=128,
    )
    joined = " ".join(argv)
    assert "--rm" in argv
    assert "--network none" in joined          # no network by default
    assert "--read-only" in argv
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--memory 256m" in joined
    assert "--memory-swap 256m" in joined      # no swap
    assert "--cpus 0.5" in joined
    assert "--pids-limit 128" in joined
    assert "/host/dir:/sandbox:ro" in joined   # snippet mounted read-only
    assert argv[-2:] == ["python3", "/sandbox/snippet.py"]
    assert sb.IMAGE_NAME in argv


def test_docker_run_argv_network_opt_in():
    argv = sb._docker_run_argv(
        "c1", ["node", "/sandbox/snippet.js"], "/host/dir",
        network=True, memory="512m", cpus="1.0", pids=256,
    )
    assert "--network bridge" in " ".join(argv)


# ── run_sync guard rails (no Docker needed) ───────────────────────────────

def test_unsupported_language_short_circuits(monkeypatch):
    # Must not even check Docker for an unsupported language.
    monkeypatch.setattr(sb, "docker_available", lambda: pytest.fail("called docker"))
    out, ok = sb.run_sync("print(1)", language="ruby")
    assert ok is False
    assert "Unsupported language" in out


def test_empty_code_rejected():
    out, ok = sb.run_sync("   ", language="python")
    assert ok is False
    assert "No code" in out


def test_docker_unavailable_message(monkeypatch):
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    out, ok = sb.run_sync("print(1)", language="python")
    assert ok is False
    assert "Docker is not available" in out


def test_missing_image_without_autobuild(monkeypatch):
    monkeypatch.setattr(sb, "docker_available", lambda: True)
    monkeypatch.setattr(sb, "image_exists", lambda: False)
    out, ok = sb.run_sync("print(1)", language="python", auto_build=False)
    assert ok is False
    assert "not built" in out


# ── builtin action registration ───────────────────────────────────────────

def test_run_sandbox_action_registered():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO

    assert "run_sandbox" in BUILTIN_ACTIONS
    assert "run_sandbox" in BUILTIN_ACTION_INFO


async def test_run_sandbox_action_delegates(monkeypatch):
    import src.sandbox_manager as sbmod
    from src.builtin_actions import action_run_sandbox

    seen = {}

    async def fake_run(code, language="python", network=False, **kwargs):
        seen.update(code=code, language=language, network=network)
        return "ok", True

    monkeypatch.setattr(sbmod, "run", fake_run)
    out, ok = await action_run_sandbox("owner", script="print(1)", language="py", network=True)
    assert ok is True and out == "ok"
    assert seen == {"code": "print(1)", "language": "py", "network": True}


async def test_run_sandbox_action_requires_code():
    from src.builtin_actions import action_run_sandbox

    out, ok = await action_run_sandbox("owner", script="")
    assert ok is False
    assert "No code" in out


# ── real end-to-end (opt-in: Docker + ODYSSEUS_SANDBOX_IT=1) ──────────────

_RUN_IT = os.getenv("ODYSSEUS_SANDBOX_IT") == "1" and sb.docker_available()
_it = pytest.mark.skipif(not _RUN_IT, reason="set ODYSSEUS_SANDBOX_IT=1 with Docker to run")


@_it
def test_python_real_run():
    out, ok = sb.run_sync("print(6*7)", language="python", timeout=120)
    assert ok is True
    assert out == "42"


@_it
def test_node_real_run():
    out, ok = sb.run_sync("console.log(6*7)", language="node", timeout=120)
    assert ok is True
    assert out == "42"


@_it
def test_no_network_by_default():
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('NET')\n"
        "except Exception:\n"
        "    print('NO_NET')\n"
    )
    out, ok = sb.run_sync(code, language="python", timeout=120)
    assert "NO_NET" in out
