"""Docker entrypoint Ollama pre-flight: probe-target resolution.

The entrypoint warns when host Ollama is unreachable from the container
(the #1 Docker self-host footgun: forgetting OLLAMA_HOST=0.0.0.0). The
probe URL must mirror app.py /api/runtime resolution exactly:
OLLAMA_BASE_URL, then OLLAMA_URL, then the in-Docker default — with any
/v1 suffix and trailing slash stripped before hitting /api/tags.

These tests extract the real resolution snippet from entrypoint.sh and
run it under /bin/sh, so the assertions cannot drift from the shipped
script.
"""
import os
import re
import shutil
import subprocess

import pytest

ENTRYPOINT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docker",
    "entrypoint.sh",
)


def _resolution_snippet() -> str:
    """Pull the three ollama_base/ollama_root expansion lines verbatim."""
    with open(ENTRYPOINT, "r", encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(
        r'(ollama_base="\$\{OLLAMA_BASE_URL.*?ollama_root="\$\{ollama_root%/v1\}")',
        src,
        re.DOTALL,
    )
    assert m, "resolution lines not found in entrypoint.sh — did the script change?"
    return m.group(1) + '\nprintf %s "$ollama_root"\n'


def _resolve(env: dict) -> str:
    sh = shutil.which("sh")
    assert sh, "POSIX sh required for this test"
    proc = subprocess.run(
        [sh, "-c", _resolution_snippet()],
        env={**{k: v for k, v in os.environ.items()
                if k not in ("OLLAMA_BASE_URL", "OLLAMA_URL")}, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


@pytest.mark.parametrize(
    "env,expected",
    [
        # In-Docker default when nothing is set.
        ({}, "http://host.docker.internal:11434"),
        # OLLAMA_URL used when no OLLAMA_BASE_URL.
        ({"OLLAMA_URL": "http://1.2.3.4:11434/v1"}, "http://1.2.3.4:11434"),
        # OLLAMA_BASE_URL wins over OLLAMA_URL.
        (
            {"OLLAMA_BASE_URL": "http://base:11434/v1", "OLLAMA_URL": "http://url:11434"},
            "http://base:11434",
        ),
        # No /v1 suffix.
        ({"OLLAMA_BASE_URL": "http://x:11434"}, "http://x:11434"),
        # /v1 stripped.
        ({"OLLAMA_BASE_URL": "http://x:11434/v1"}, "http://x:11434"),
        # Trailing slash after /v1 stripped too.
        ({"OLLAMA_BASE_URL": "http://x:11434/v1/"}, "http://x:11434"),
    ],
)
def test_ollama_probe_target(env, expected):
    assert _resolve(env) == expected


def test_probe_hits_native_tags_endpoint():
    """The warning path must probe Ollama's /api/tags liveness endpoint."""
    with open(ENTRYPOINT, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert '"$ollama_root/api/tags"' in src
    # Non-fatal: must not be guarded by `set -e` aborting startup.
    assert "OLLAMA_HOST=0.0.0.0:11434 ollama serve" in src
