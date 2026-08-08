"""Focused browser-side regression coverage for authoritative email opens."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_INBOX_JS = _REPO / "static" / "js" / "emailInbox.js"
_LIBRARY_JS = _REPO / "static" / "js" / "emailLibrary.js"
_HAS_NODE = shutil.which("node") is not None


def _extract_between(source: str, signature: str, next_marker: str) -> str:
    start = source.index(signature)
    end = source.index(next_marker, start)
    return source[start:end].rstrip()


def test_library_unread_preview_has_one_authoritative_request_and_rollback():
    source = _LIBRARY_JS.read_text(encoding="utf-8")
    function = _extract_between(source, "async function _toggleCardPreview", "\n/**\n * Wrap a probable signature block")

    assert function.count("/api/email/read/") == 1
    assert "/api/email/mark-read/" not in function
    assert "&mark_seen=true" in function
    assert "_syncEmailReadState(uidAtStart, true)" in function
    assert "_syncEmailReadState(uidAtStart, false)" in function
    assert "if (!isCurrentOpen()) return" in function


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_inbox_late_read_response_cannot_apply_after_newer_open():
    source = _INBOX_JS.read_text(encoding="utf-8")
    function = _extract_between(source, "async function _openEmail", "\nfunction _showEmailMenu")
    assert "let _openEmailRequestSeq = 0;" in source

    harness = f"""
const realLog = console.log;
console.error = () => {{}};
const API_BASE = 'https://odysseus.invalid';
const window = {{ __odysseusActiveEmailAccount: 'acct-a' }};
let _currentFolder = 'INBOX';
const _acct = () => '&account_id=acct-a';
let _openEmailRequestSeq = 0;
let _docModule = null;
const spinnerModule = {{ createWhirlpool() {{ throw new Error('spinner should not run'); }} }};
const sessionModule = null;
let firstResolve;
const calls = [];
async function fetch(url) {{
  calls.push(String(url));
  if (calls.length === 1) {{
    return await new Promise((resolve) => {{
      firstResolve = () => resolve({{ json: async () => ({{ uid: '1', subject: 'old' }}) }});
    }});
  }}
  return {{ json: async () => ({{ error: 'newer open completed test' }}) }};
}}
{function}
const oldEmail = {{ uid: '1', is_read: false }};
const newerEmail = {{ uid: '2', is_read: false }};
const first = _openEmail(oldEmail, null);
await Promise.resolve();
const second = _openEmail(newerEmail, null);
await second;
firstResolve();
await first;
realLog(JSON.stringify({{ calls, oldRead: oldEmail.is_read, newerRead: newerEmail.is_read }}));
"""
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=harness,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, f"node failed: {proc.stderr}\n---\n{harness}"
    result = json.loads(proc.stdout.strip())
    assert len(result["calls"]) == 2
    assert all("mark_seen=true" in url for url in result["calls"])
    assert result["oldRead"] is False
    assert result["newerRead"] is False
