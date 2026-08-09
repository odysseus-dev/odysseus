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
    assert "_syncEmailReadState(uidAtStart, true, readContext)" in function
    assert "_syncEmailReadState(uidAtStart, false, readContext)" in function
    assert "openGeneration === _emailCardOpenSeq" in function
    assert "_emailReadMutations.get(readContextKey)?.generation !== readMutation.generation" in function
    assert "authoritativeReadSucceeded = true;" in function
    assert "if (!authoritativeReadSucceeded) restoreUnreadState();" in function
    assert "if (!isCurrentOpen()) return" in function


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_library_authoritative_success_defeats_newer_rollback_in_either_order():
    source = _LIBRARY_JS.read_text(encoding="utf-8")
    function = _extract_between(source, "async function _toggleCardPreview", "\n/**\n * Wrap a probable signature block")
    settlements = _extract_between(
        function,
        "  const restoreUnreadState = () => {",
        "\n\n  // Collapse any other expanded card",
    )

    harness = f"""
const _emailReadMutations = new Map();
const readContextKey = 'same-mailbox-message';
const uidAtStart = '1';
const readContext = {{ accountId: 'acct-a', folder: 'INBOX', uid: '1' }};
const readUpdates = [];
function _syncEmailReadState(uid, isRead, context) {{
  readUpdates.push({{ uid, isRead, context }});
}}
function createSettlers(readMutation) {{
{settlements}
  return {{ restoreUnreadState, commitReadState }};
}}
function runRace(successFirst) {{
  _emailReadMutations.clear();
  readUpdates.length = 0;
  const mutationA = {{ generation: 1, rollbackUnread: true }};
  _emailReadMutations.set(readContextKey, mutationA);
  const settlersA = createSettlers(mutationA);
  const mutationB = {{ generation: 2, rollbackUnread: true }};
  _emailReadMutations.set(readContextKey, mutationB);
  const settlersB = createSettlers(mutationB);
  if (successFirst) {{
    settlersA.commitReadState();
    settlersB.restoreUnreadState();
  }} else {{
    settlersB.restoreUnreadState();
    settlersA.commitReadState();
  }}
  return {{
    hasMutation: _emailReadMutations.has(readContextKey),
    readUpdates: readUpdates.map(update => update.isRead),
  }};
}}
console.log(JSON.stringify({{
  successFirst: runRace(true),
  failureFirst: runRace(false),
}}));
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
    assert json.loads(proc.stdout.strip()) == {
        "successFirst": {"hasMutation": False, "readUpdates": [True]},
        "failureFirst": {"hasMutation": False, "readUpdates": [False, True]},
    }


def test_library_reply_open_carries_immutable_mailbox_context():
    library_source = _LIBRARY_JS.read_text(encoding="utf-8")
    inbox_source = _INBOX_JS.read_text(encoding="utf-8")

    assert "const mailboxGeneration = _emailMailboxGeneration;" in library_source
    assert "messageFolder = String(options.email?.folder || libraryFolder)" in library_source
    assert "return onEmailClick({ ...options, mailboxContext });" in library_source
    assert "mailboxContext?.messageFolder || _currentFolder" in inbox_source
    assert "mailboxContextIsCurrent()" in inbox_source
    assert "if (!isCurrentOpen()) return;\n        let activeSid = await _createEmailChat" in inbox_source


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


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("context_change", ["account", "folder", "library"])
def test_inbox_late_read_response_cannot_apply_after_mailbox_switch(context_change):
    source = _INBOX_JS.read_text(encoding="utf-8")
    function = _extract_between(source, "async function _openEmail", "\nfunction _showEmailMenu")

    changes = {
        "account": "window.__odysseusActiveEmailAccount = 'acct-b';",
        "folder": "_currentFolder = 'Archive';",
        "library": "libraryCurrent = false;",
    }
    change = changes[context_change]
    open_call = (
        "_openEmail(email, null, null, 'reply', '', '', mailboxContext)"
        if context_change == "library"
        else "_openEmail(email, null)"
    )
    harness = f"""
const realLog = console.log;
console.error = () => {{}};
const API_BASE = 'https://odysseus.invalid';
const window = {{ __odysseusActiveEmailAccount: 'acct-a' }};
let _currentFolder = 'INBOX';
const _acct = () => '&account_id=acct-a';
let _openEmailRequestSeq = 0;
let libraryCurrent = true;
const mailboxContext = {{
  accountId: 'acct-a',
  messageFolder: 'Archive',
  isCurrent: () => libraryCurrent,
}};
let createCalls = 0;
let _docModule = {{}};
async function _createEmailChat() {{ createCalls += 1; return 'stale-session'; }}
const spinnerModule = {{ createWhirlpool() {{ throw new Error('spinner should not run'); }} }};
const sessionModule = null;
let resolveRead;
async function fetch() {{
  return await new Promise((resolve) => {{
    resolveRead = () => resolve({{ json: async () => ({{ uid: '1', subject: 'old' }}) }});
  }});
}}
{function}
const email = {{ uid: '1', is_read: false }};
const pending = {open_call};
await Promise.resolve();
{change}
resolveRead();
await pending;
realLog(JSON.stringify({{ createCalls, isRead: email.is_read }}));
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
    assert result == {"createCalls": 0, "isRead": False}
