"""Regression for PR #4535 — Docker host-Ollama Stop/Kill must clean up the
HOST daemon, not container loopback.

When Odysseus runs inside Docker it cannot run `ollama serve` in the container;
the runner instead probes the host daemon over the host gateway and logs
`Ollama detected on host at host.docker.internal:<port>`, keeping the task alive.

`_ollamaBaseUrlForTask` (cookbookOllamaUrl.js) resolves the daemon URL that
Stop/Kill use for model unload and endpoint deletion. Before the fix it only
recognised the native `Ollama API ready on port N: http://...` log shape and
otherwise fell back to `http://127.0.0.1:<port>` — so a Docker host-Ollama task
was cleaned up against container loopback, leaving the host daemon loaded and
the host.docker.internal endpoint behind.

Pure function → executed under node here (cookbookRunning.js pulls in
browser-only modules and can't load).
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO, capture_output=True, timeout=15, text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out:
        raise AssertionError("node produced no stdout")
    return json.loads(out[-1])


def test_docker_host_ollama_resolves_to_host_gateway(node_available):
    """The Docker host-Ollama log line resolves cleanup to host.docker.internal,
    NOT container loopback."""
    script = textwrap.dedent("""
        const { _ollamaBaseUrlForTask } = await import('./static/js/cookbookOllamaUrl.js');
        const task = { payload: { _cmd: 'ollama serve' } };
        const out = '[odysseus] Ollama detected on host at host.docker.internal:11434 — using host Ollama.';
        const url = _ollamaBaseUrlForTask(task, out);
        console.log(JSON.stringify({ url }));
    """)
    res = _run_node(script)
    assert res["url"] == "http://host.docker.internal:11434", res
    assert "127.0.0.1" not in res["url"]


def test_docker_host_ollama_honours_custom_port(node_available):
    """A user-pinned Ollama port survives into the cleanup URL."""
    script = textwrap.dedent("""
        const { _ollamaBaseUrlForTask } = await import('./static/js/cookbookOllamaUrl.js');
        const task = { payload: { _cmd: 'ollama serve' } };
        const out = 'Ollama detected on host at host.docker.internal:11435 — using host Ollama.';
        const url = _ollamaBaseUrlForTask(task, out);
        console.log(JSON.stringify({ url }));
    """)
    res = _run_node(script)
    assert res["url"] == "http://host.docker.internal:11435", res


def test_native_ready_url_still_wins(node_available):
    """The native 'ready on port' URL still takes precedence (unchanged path)."""
    script = textwrap.dedent("""
        const { _ollamaBaseUrlForTask } = await import('./static/js/cookbookOllamaUrl.js');
        const task = { payload: { _cmd: 'ollama serve' } };
        const out = 'Ollama API ready on port 11434: http://127.0.0.1:11434/';
        const url = _ollamaBaseUrlForTask(task, out);
        console.log(JSON.stringify({ url }));
    """)
    res = _run_node(script)
    assert res["url"] == "http://127.0.0.1:11434", res


def test_plain_serve_falls_back_to_loopback(node_available):
    """No host-gateway/ready log (native local serve) → loopback fallback as before."""
    script = textwrap.dedent("""
        const { _ollamaBaseUrlForTask } = await import('./static/js/cookbookOllamaUrl.js');
        const task = { payload: { _cmd: 'OLLAMA_HOST=127.0.0.1:11436 ollama serve' } };
        const url = _ollamaBaseUrlForTask(task, 'Starting ollama server...');
        console.log(JSON.stringify({ url }));
    """)
    res = _run_node(script)
    assert res["url"] == "http://127.0.0.1:11436", res
