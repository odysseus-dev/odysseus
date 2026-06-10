"""Regression for issue #1508 — abandoning a Compare stream only closed the
client SSE reader while the model kept generating tokens server-side (LM Studio
etc.).

Compare runs are detached on the backend, so aborting the fetch (AbortController)
doesn't cancel them — the main chat Stop button POSTs /api/chat/stop/<sid> to do
that. Every place that abandons a compare stream must do the same:
  - the Stop buttons        (panes.js: stopPane / stopAll)
  - closing compare         (index.js: deactivate)
  - the per-pane idle timeout (stream.js)

The shared POST lives in a leaf module compare/backendStop.js so all three can
use it without circular imports; it's executed under node here (real fetch
behaviour), and the wiring into each of the three sites is guarded at the source
so a sibling-file path can't silently regress.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CMP = _REPO / "static/js/compare"
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not out_lines:
        raise AssertionError("node produced no stdout")
    return json.loads(out_lines[-1])


def test_backend_stop_posts_correct_endpoint(node_available):
    """backendStopSession must POST /api/chat/stop/<encoded sid> with credentials,
    and swallow network errors (a failed stop must never throw into the caller)."""
    script = textwrap.dedent("""
        const calls = [];
        globalThis.fetch = (url, opts) => { calls.push({ url, opts }); return Promise.reject(new Error('boom')); };
        const { backendStopSession } = await import('./static/js/compare/backendStop.js');
        let threw = null;
        try { await backendStopSession('sess id/42'); } catch (e) { threw = String(e); }
        console.log(JSON.stringify({
          threw,
          count: calls.length,
          url: calls[0] && calls[0].url,
          method: calls[0] && calls[0].opts && calls[0].opts.method,
          creds: calls[0] && calls[0].opts && calls[0].opts.credentials,
        }));
    """)
    out = _run_node(script)
    assert out["threw"] is None, "a failed stop must not throw (caught .catch)"
    assert out["count"] == 1
    assert out["url"] == "/api/chat/stop/sess%20id%2F42", "sid must be URL-encoded into the path"
    assert out["method"] == "POST"
    assert out["creds"] == "same-origin"


def test_backend_stop_noop_on_missing_sid(node_available):
    """No session id → no request at all (don't POST to /api/chat/stop/undefined)."""
    script = textwrap.dedent("""
        const calls = [];
        globalThis.fetch = (url, opts) => { calls.push(url); return Promise.resolve(); };
        const { backendStopSession } = await import('./static/js/compare/backendStop.js');
        backendStopSession('');
        backendStopSession(undefined);
        backendStopSession(null);
        console.log(JSON.stringify({ count: calls.length }));
    """)
    out = _run_node(script)
    assert out["count"] == 0


def test_all_three_abandon_paths_cancel_the_backend():
    """The #1508 leak is reachable from three sites; each must cancel server-side.
    A single-file check can't see the sibling paths, so guard all three."""
    panes = (_CMP / "panes.js").read_text(encoding="utf-8")
    index = (_CMP / "index.js").read_text(encoding="utf-8")
    stream = (_CMP / "stream.js").read_text(encoding="utf-8")

    # Stop buttons (panes.js): _backendStopPane delegates to the shared helper and
    # is invoked by both stopPane and stopAll.
    assert "backendStopSession(" in panes
    assert re.search(r"function _backendStopPane\(", panes)
    assert panes.count("_backendStopPane(") >= 3  # def + stopPane + stopAll

    # Closing compare (index.js: deactivate) cancels each in-flight pane's run.
    assert 'from "./backendStop.js"' in index or "from './backendStop.js'" in index
    dz = index[index.index("async function deactivate("):]
    dz = dz[: dz.index("state._abortControllers = [];") + 40]
    assert "backendStopSession(" in dz, "deactivate must cancel each pane's backend run (#1508)"

    # Idle timeout (stream.js) cancels the pane's run, not just the client reader.
    assert 'from "./backendStop.js"' in stream or "from './backendStop.js'" in stream
    assert re.search(r"_onIdleTimeout\s*=\s*\(\)\s*=>\s*{[^}]*backendStopSession\(", stream), \
        "idle timeout must cancel the backend run, not only ac.abort()"
