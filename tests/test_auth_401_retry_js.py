"""Regression coverage for the `window.fetch` 401 interceptor in
`static/app.js` (auth-mobile fix, PR #5836).

The interceptor retries a transient 401 once (to ride out stale cookies on
mobile tab resume), deduplicates concurrent 401s, and defers the login
redirect while the user is typing. Two defects were fixed here:

1. When the retry succeeds (401 -> 2xx) the wrapper returned the *original*
   401 response instead of the successful `retry`, so every caller still saw
   an auth failure after the request had actually succeeded.
2. The wrapper retried *every* method, including durable POST/PATCH/DELETE
   requests, without any idempotency restriction, silently replaying
   state-changing work.

`app.js` pulls in browser-only globals and can't be imported standalone, so
the fetch-wrapping block is extracted from source and executed under node
with mocked `window`/`document`/`setTimeout`/original-fetch — the same
approach as test_local_endpoint_js.py / test_reply_recipients_js.py. Skips
when `node` is not installed rather than failing.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_APP_JS = _REPO / "static" / "app.js"
_HAS_NODE = shutil.which("node") is not None

_START_MARKER = "// Redirect to login on 401 from any fetch"
_FN_MARKER = "window.fetch = async function(...args) {"


def _extract_fetch_wrapper(src: str) -> str:
    """Return the 401-interceptor block: from its leading comment through the
    closing `};` of the `window.fetch = async function(...)` assignment."""
    start = src.index(_START_MARKER)
    idx = src.index(_FN_MARKER, start)
    brace = src.index("{", idx)
    depth = 1
    i = brace + 1
    quote = None
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if src[end:end + 1] == ";":
                    end += 1
                return src[start:end]
        i += 1
    raise AssertionError("unbalanced braces in window.fetch wrapper")


_HARNESS = textwrap.dedent(r"""
    const sequence = {sequence};
    const calls = [];
    let redirect = null;
    let __out = '';
    function mkRes(status) {{
      return {{ status, ok: status >= 200 && status < 300 }};
    }}
    const win = {{
      location: {{
        get href() {{ return redirect; }},
        set href(v) {{ redirect = v; }},
      }},
    }};
    win.fetch = async (...args) => {{
      calls.push(args.length);
      return mkRes(sequence[calls.length - 1]);
    }};
    globalThis.window = win;
    globalThis.document = {{ getElementById: () => null, activeElement: null }};
    globalThis.setTimeout = (fn) => {{ fn(); return 0; }};
    globalThis.console = {{ warn: () => {{}} }};
    {block}
    const __run = async () => {{
      const result = await win.fetch({url}, {init});
      __out = JSON.stringify({{
        status: result.status,
        calls: calls.length,
        redirect,
      }});
    }};
    __run().then(() => process.stdout.write(__out))
           .catch((e) => {{ console.error(e); process.exit(1); }});
""")


def _run_wrapper(sequence: list, url: str = "'/api/foo'", init: str = "undefined") -> dict:
    block = _extract_fetch_wrapper(_APP_JS.read_text(encoding="utf-8"))
    js = _HARNESS.format(
        sequence=json.dumps(sequence),
        block=block,
        url=url,
        init=init,
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, (
        f"node failed:\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}\n---\n{js}"
    )
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_retry_success_returns_retry_response_not_original_401():
    """401 -> retry 200: caller must receive the successful retry, not the 401."""
    out = _run_wrapper([401, 200])
    assert out["status"] == 200, (
        "Successful retry response must be returned to the caller; "
        f"got status {out['status']}"
    )
    assert out["calls"] == 2, "Transient 401 should be retried exactly once"
    assert out["redirect"] is None, "No redirect when the retry succeeds"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_retry_also_401_redirects_to_login():
    out = _run_wrapper([401, 401])
    assert out["status"] == 401
    assert out["calls"] == 2, "Failed retry should still have attempted once"
    assert out["redirect"] == "/login", "401 after retry must redirect to login"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("init,method", [
    ("{ method: 'POST' }", "POST"),
    ("{ method: 'PATCH' }", "PATCH"),
    ("{ method: 'DELETE' }", "DELETE"),
    ("{ method: 'PUT' }", "PUT"),
])
def test_unsafe_methods_are_not_silently_replayed(init, method):
    """A durable request that 401s must NOT be replayed; caller sees the 401."""
    out = _run_wrapper([401, 200], init=init)
    assert out["status"] == 401, f"{method} must not be auto-retried after a 401"
    assert out["calls"] == 1, f"{method} must be invoked exactly once (no replay)"
    assert out["redirect"] == "/login", f"{method} 401 should still redirect to login"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("init,method", [
    ("undefined", "GET"),
    ("{ method: 'HEAD' }", "HEAD"),
    ("{ method: 'OPTIONS' }", "OPTIONS"),
])
def test_safe_methods_are_retried_and_return_retry(init, method):
    out = _run_wrapper([401, 200], init=init)
    assert out["status"] == 200, f"{method} retry success must be returned"
    assert out["calls"] == 2, f"{method} should be retried once"
    assert out["redirect"] is None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_non_401_passes_through_without_retry_or_redirect():
    out = _run_wrapper([200])
    assert out["status"] == 200
    assert out["calls"] == 1
    assert out["redirect"] is None


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_auth_endpoint_401_is_not_retried_or_redirected():
    out = _run_wrapper([401], url="'/api/auth/status'")
    assert out["status"] == 401
    assert out["calls"] == 1, "Auth endpoints must be excluded from the retry path"
    assert out["redirect"] is None
