"""Behavioral regressions for the account-scoped email archive fallback.

The helper is DOM-free, so execute its real JavaScript with Node instead of
relying on source-string assertions for an asynchronous account switch.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "emailArchiveFallback.js"
_HAS_NODE = shutil.which("node") is not None


def _run(body: str):
    source = _HELPER.read_text(encoding="utf-8")
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source + "\n" + body,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_account_switch_while_archive_prompt_is_pending_aborts_creation_and_retry():
    result = _run(
        """
        let activeAccountId = 'account-a';
        let resolveConfirm;
        let markConfirmStarted;
        const confirmGate = new Promise(resolve => { resolveConfirm = resolve; });
        const confirmStarted = new Promise(resolve => { markConfirmStarted = resolve; });
        const calls = [];
        const action = captureArchiveAction('42', activeAccountId, 'INBOX');

        const pending = runArchiveFallback(action, {
          getActiveAccountId: () => activeAccountId,
          archiveOnce: async captured => {
            calls.push(['archive', captured.accountId, captured.sourceFolder, captured.uid]);
            return { success: false, needs_archive_folder: true, suggested_folder: 'Archive' };
          },
          confirmCreate: async () => {
            calls.push(['confirm', action.accountId]);
            markConfirmStarted();
            return confirmGate;
          },
          createFolder: async (folderName, captured) => {
            calls.push(['create', captured.accountId, folderName]);
            return { success: true, created: true };
          },
          refreshFolders: async captured => {
            calls.push(['refresh', captured.accountId]);
          },
        });

        await confirmStarted;
        activeAccountId = 'account-b';
        resolveConfirm(true);
        const outcome = await pending;
        console.log(JSON.stringify({ outcome, calls }));
        """
    )

    assert result["outcome"] == {"success": False, "stale": True}
    assert result["calls"] == [
        ["archive", "account-a", "INBOX", "42"],
        ["confirm", "account-a"],
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_archive_retry_reuses_frozen_account_folder_and_uid_context():
    result = _run(
        """
        let activeAccountId = 'account-a';
        let mutableFolder = 'INBOX';
        const calls = [];
        const action = captureArchiveAction('42', activeAccountId, mutableFolder);
        let archiveAttempts = 0;

        const outcome = await runArchiveFallback(action, {
          getActiveAccountId: () => activeAccountId,
          archiveOnce: async captured => {
            calls.push(['archive', captured.accountId, captured.sourceFolder, captured.uid]);
            archiveAttempts += 1;
            return archiveAttempts === 1
              ? { success: false, needs_archive_folder: true, suggested_folder: 'Archive' }
              : { success: true, folder: 'Archive' };
          },
          confirmCreate: async () => {
            mutableFolder = 'Sent';
            return true;
          },
          createFolder: async (folderName, captured) => {
            calls.push(['create', captured.accountId, captured.sourceFolder, folderName]);
            return { success: true, created: true };
          },
          refreshFolders: async captured => {
            calls.push(['refresh', captured.accountId, captured.sourceFolder]);
          },
        });

        console.log(JSON.stringify({
          outcome,
          calls,
          frozen: Object.isFrozen(action),
          action,
        }));
        """
    )

    assert result["outcome"] == {"success": True, "folder": "Archive"}
    assert result["frozen"] is True
    assert result["action"] == {
        "uid": "42",
        "accountId": "account-a",
        "sourceFolder": "INBOX",
    }
    assert result["calls"] == [
        ["archive", "account-a", "INBOX", "42"],
        ["create", "account-a", "INBOX", "Archive"],
        ["refresh", "account-a", "INBOX"],
        ["archive", "account-a", "INBOX", "42"],
    ]


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_same_account_actions_share_one_prompt_creation_and_ready_notification():
    result = _run(
        """
        const activeAccountId = 'account-a';
        let resolveConfirm;
        const confirmGate = new Promise(resolve => { resolveConfirm = resolve; });
        const attempts = new Map();
        const counts = { confirm: 0, create: 0, refresh: 0, ready: 0 };
        const deps = {
          getActiveAccountId: () => activeAccountId,
          archiveOnce: async captured => {
            const attempt = (attempts.get(captured.uid) || 0) + 1;
            attempts.set(captured.uid, attempt);
            return attempt === 1
              ? { success: false, needs_archive_folder: true }
              : { success: true };
          },
          confirmCreate: async () => {
            counts.confirm += 1;
            return confirmGate;
          },
          createFolder: async () => {
            counts.create += 1;
            return { success: true, created: true };
          },
          refreshFolders: async () => { counts.refresh += 1; },
          onFolderReady: async () => { counts.ready += 1; },
        };

        const first = runArchiveFallback(
          captureArchiveAction('1', activeAccountId, 'INBOX'),
          deps,
        );
        const second = runArchiveFallback(
          captureArchiveAction('2', activeAccountId, 'INBOX'),
          deps,
        );
        await new Promise(resolve => setTimeout(resolve, 0));
        resolveConfirm(true);
        const outcomes = await Promise.all([first, second]);

        console.log(JSON.stringify({ counts, attempts: Object.fromEntries(attempts), outcomes }));
        """
    )

    assert result == {
        "counts": {"confirm": 1, "create": 1, "refresh": 2, "ready": 1},
        "attempts": {"1": 2, "2": 2},
        "outcomes": [{"success": True}, {"success": True}],
    }


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_archive_setup_prompt_is_deduplicated_per_account():
    result = _run(
        """
        let activeAccountId = 'account-a';
        let resolveA;
        let aConfirmCount = 0;
        let bConfirmCount = 0;
        const aGate = new Promise(resolve => { resolveA = resolve; });

        const deps = accountId => ({
          getActiveAccountId: () => activeAccountId,
          archiveOnce: async () => ({ success: false, needs_archive_folder: true }),
          confirmCreate: async () => {
            if (accountId === 'account-a') {
              aConfirmCount += 1;
              return aGate;
            }
            bConfirmCount += 1;
            return false;
          },
          createFolder: async () => ({ success: true }),
          refreshFolders: async () => {},
        });

        const a1 = runArchiveFallback(captureArchiveAction('1', 'account-a', 'INBOX'), deps('account-a'));
        const a2 = runArchiveFallback(captureArchiveAction('2', 'account-a', 'INBOX'), deps('account-a'));
        await new Promise(resolve => setTimeout(resolve, 0));

        activeAccountId = 'account-b';
        const b = runArchiveFallback(captureArchiveAction('3', 'account-b', 'INBOX'), deps('account-b'));
        await new Promise(resolve => setTimeout(resolve, 0));
        resolveA(false);
        await Promise.all([a1, a2, b]);

        console.log(JSON.stringify({ aConfirmCount, bConfirmCount }));
        """
    )

    assert result == {"aConfirmCount": 1, "bConfirmCount": 1}
