"""Compose must not send from an active account that no longer exists (#1912)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.helpers.document_source import function_body


_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None

REMOVED_TOAST = "Selected email account no longer exists; using your SMTP account."
RECEIVE_ONLY_TOAST = "Selected email account is receive-only; using your SMTP account."


def test_missing_active_account_returns_null_before_the_stale_id():
    body = function_body("_resolveComposeSendAccountId")

    # The inverted guard used to return the stale id exactly when the account
    # was absent from the account list.
    assert (
        "if (!activeAccount || _accountCanSend(activeAccount)) return activeAccountId;"
        not in body
    )
    guard = body.index("if (!activeAccount) {")
    assert body.index("return null;", guard) < body.index("return activeAccountId;")
    assert REMOVED_TOAST in body

    # The receive-only fallback keeps its own guard and message.
    assert "if (_accountCanSend(activeAccount)) return activeAccountId;" in body
    assert RECEIVE_ONLY_TOAST in body


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_compose_send_account_resolution_behaviour():
    can_send = function_body("_accountCanSend")
    resolve = function_body("_resolveComposeSendAccountId")

    harness = "\n".join(
        [
            "const window = {};",
            "const uiModule = { toasts: [], showToast(message) { this.toasts.push(message); } };",
            "let _accounts = [];",
            "async function _getEmailAccountsCached() { return _accounts; }",
            can_send,
            resolve,
            """
async function resolveFor(activeId, accounts) {
  uiModule.toasts.length = 0;
  window.__odysseusActiveEmailAccount = activeId;
  _accounts = accounts;
  const result = await _resolveComposeSendAccountId();
  return { result, toasts: uiModule.toasts.slice() };
}
const sendable = { id: 10, smtp_host: "smtp.example.com", smtp_user: "me", has_smtp_password: true };
const receiveOnly = { id: 11, smtp_host: "smtp.example.com", smtp_user: "me" };
console.log(JSON.stringify({
  deleted: await resolveFor("10", [{ id: 99 }]),
  sendable: await resolveFor("10", [sendable]),
  receiveOnly: await resolveFor("11", [receiveOnly]),
  noActiveAccount: await resolveFor(null, [sendable]),
}));
""",
        ]
    )
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
        "deleted": {"result": None, "toasts": [REMOVED_TOAST]},
        "sendable": {"result": "10", "toasts": []},
        "receiveOnly": {"result": None, "toasts": [RECEIVE_ONLY_TOAST]},
        "noActiveAccount": {"result": None, "toasts": []},
    }
