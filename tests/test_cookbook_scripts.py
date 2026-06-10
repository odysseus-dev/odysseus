"""Smoke tests for the Cookbook / GPU setup-diagnostic scripts.

These are the cheapest possible "does it even load" guards — they catch a script
broken by a bad edit (shell syntax error, import error, argparse misconfig)
before a user hits it during setup. They run nothing destructive: only `bash -n`
(parse, don't execute), `--help`, and the read-only `cached` command pointed at
an empty cache.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_DOCKER_GPU = REPO_ROOT / "scripts" / "check-docker-gpu.sh"
ODYSSEUS_COOKBOOK = REPO_ROOT / "scripts" / "odysseus-cookbook"

_HAS_BASH = shutil.which("bash") is not None
_needs_bash = pytest.mark.skipif(not _HAS_BASH, reason="bash not available")


def _run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, **kwargs)


# ── scripts/check-docker-gpu.sh ──────────────────────────────────────────────


@_needs_bash
def test_check_docker_gpu_passes_bash_syntax_check():
    """`bash -n` parses the whole script without executing it."""
    r = _run(["bash", "-n", str(CHECK_DOCKER_GPU)])
    assert r.returncode == 0, r.stderr


@_needs_bash
def test_check_docker_gpu_help_exits_clean():
    r = _run(["bash", str(CHECK_DOCKER_GPU), "--help"])
    assert r.returncode == 0
    assert "check-docker-gpu.sh" in r.stdout


@_needs_bash
def test_check_docker_gpu_rejects_unknown_flag():
    r = _run(["bash", str(CHECK_DOCKER_GPU), "--definitely-not-a-flag"])
    assert r.returncode != 0
    assert "Unknown option" in r.stderr


# ── scripts/odysseus-cookbook ────────────────────────────────────────────────


def test_odysseus_cookbook_help_exits_clean():
    r = _run([sys.executable, str(ODYSSEUS_COOKBOOK), "--help"])
    assert r.returncode == 0
    assert "odysseus-cookbook" in r.stdout


@pytest.mark.parametrize(
    "subcmd",
    ["list", "gpus", "cached", "hf-latest", "download", "serve", "kill", "state", "state-set"],
)
def test_odysseus_cookbook_subcommand_help_exits_clean(subcmd):
    """Every advertised subcommand must build its parser and print help."""
    r = _run([sys.executable, str(ODYSSEUS_COOKBOOK), subcmd, "--help"])
    assert r.returncode == 0, r.stderr
    assert subcmd in r.stdout


def test_odysseus_cookbook_cached_is_read_only_json(tmp_path):
    """`cached` against an empty HF_HOME returns valid JSON with no models —
    a fast, side-effect-free check that the cache-scan path runs end to end."""
    env = dict(os.environ)
    env["HF_HOME"] = str(tmp_path)
    r = _run([sys.executable, str(ODYSSEUS_COOKBOOK), "cached"], env=env)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["models"] == []
    assert payload["hub_path"].endswith("hub")
