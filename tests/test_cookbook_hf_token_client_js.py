"""The explicit HF-token save, executed under node — issue #6361.

`static/js/cookbook-hf-token.js` is deliberately DOM-free with an injected
fetch, so these run the real transport rather than matching its text: what gets
sent, and what a failure is allowed to claim. cookbook.js pulls in browser
globals so its DOM handler cannot be imported here (the same trade-off as
tests/test_cookbook_cpu_only_serve.py), so its wiring — and the redaction the
new route replaces — are guarded at source level in
tests/test_cookbook_hf_token_explicit_save.py.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")

TOKEN = "hf_Abcd1234Efgh5678"


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _run(fetch_stub: str, token: str) -> dict:
    return _node_eval(
        f"""
        const calls = [];
        const fetchImpl = {fetch_stub};
        const {{ saveHfToken, HF_TOKEN_ENDPOINT }} =
          await import('./static/js/cookbook-hf-token.js');
        const result = await saveHfToken({json.dumps(token)}, {{ fetchImpl }});
        console.log(JSON.stringify({{ calls, endpoint: HF_TOKEN_ENDPOINT, result }}));
        """
    )


RESPONSE_OK = (
    "async (url, init) => { calls.push([url, init]); return { ok: true, status: 200, "
    "json: async () => ({ ok: true, hfTokenConfigured: true, hfTokenMasked: 'hf_A...5678' }) }; }"
)


def test_sends_the_trimmed_token_once_to_the_dedicated_endpoint():
    out = _run(RESPONSE_OK, f"  {TOKEN}\n")
    assert len(out["calls"]) == 1
    url, init = out["calls"][0]
    assert url == "/api/cookbook/hf-token" == out["endpoint"]
    assert init["method"] == "POST"
    # Same-origin cookies are how the admin gate identifies the caller.
    assert init["credentials"] == "same-origin"
    assert json.loads(init["body"]) == {"token": TOKEN}


def test_reports_what_the_server_stored():
    out = _run(RESPONSE_OK, TOKEN)
    assert out["result"] == {"ok": True, "configured": True, "masked": "hf_A...5678"}


def test_http_failure_is_not_a_save():
    out = _run(
        "async (url, init) => { calls.push([url, init]); "
        "return { ok: false, status: 500, json: async () => ({}) }; }",
        TOKEN,
    )
    assert out["result"]["ok"] is False
    assert "500" in out["result"]["error"]
    assert len(out["calls"]) == 1


def test_network_exception_is_not_a_save():
    out = _run("async () => { throw new Error('Failed to fetch'); }", TOKEN)
    assert out["result"] == {"ok": False, "error": "Failed to fetch"}


def test_rejected_body_reports_the_server_reason():
    out = _run(
        "async (url, init) => { calls.push([url, init]); return { ok: true, status: 200, "
        "json: async () => ({ ok: false, error: 'Invalid token characters' }) }; }",
        "hf bad token",
    )
    assert out["result"] == {"ok": False, "error": "Invalid token characters"}


def test_a_save_the_server_calls_unconfigured_is_not_shown_as_configured():
    # The ✓ tracks the answer, not the attempt: this is what the field claimed
    # on base for a request that carried no token at all.
    out = _run(
        "async (url, init) => { calls.push([url, init]); return { ok: true, status: 200, "
        "json: async () => ({ ok: true, hfTokenConfigured: false, hfTokenMasked: '' }) }; }",
        TOKEN,
    )
    assert out["result"] == {"ok": True, "configured": False, "masked": ""}


def test_a_response_without_ok_is_a_failure():
    # A proxy login page answers 200 with JSON that never said ok: configured —
    # which is exactly the false success this issue is about.
    out = _run(
        "async (url, init) => { calls.push([url, init]); return { ok: true, status: 200, "
        "json: async () => ({ hfTokenConfigured: true }) }; }",
        TOKEN,
    )
    assert out["result"]["ok"] is False


def test_an_unparseable_body_is_a_failure():
    out = _run(
        "async (url, init) => { calls.push([url, init]); return { ok: true, status: 200, "
        "json: async () => { throw new Error('Unexpected token <'); } }; }",
        TOKEN,
    )
    assert out["result"]["ok"] is False


def test_blank_field_sends_nothing():
    # No clear path: an emptied field must not reach the server at all.
    out = _run(RESPONSE_OK, "   ")
    assert out["calls"] == []
    assert out["result"]["ok"] is False


def test_missing_fetch_fails_instead_of_throwing():
    out = _run("null", TOKEN)
    assert out["result"] == {"ok": False, "error": "No fetch available"}
