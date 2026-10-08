"""Bulk Delete must retain messages that could not be moved to Trash."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class Mailbox:
    def __init__(self):
        self.inbox = {"101", "102", "103"}
        self.trash = set()
        self.deleted = set()
        self.calls = []
        self.move_status = "NO"
        self.copy_status = {}
        self.store_status = {}
        self.expunge_status = "OK"
        self.select_status = "OK"

    def select(self, folder, readonly=False):
        return self.select_status, [b"3"]

    def uid(self, command, raw_uid, *args):
        uid = raw_uid.decode() if isinstance(raw_uid, bytes) else raw_uid
        self.calls.append((command, uid))
        if command == "FETCH":
            return "OK", [f"1 (UID {uid})".encode()] if uid in self.inbox else []
        if command == "SEARCH":
            searched_uid = args[-1].split()[-1]
            return "OK", [searched_uid.encode() if searched_uid in self.inbox else b""]
        if command == "MOVE":
            status = self.move_status.get(uid, "NO") if isinstance(self.move_status, dict) else self.move_status
            if status == "OK":
                self.trash.add(uid)
                self.inbox.remove(uid)
            return status, []
        if command == "COPY":
            status = self.copy_status.get(uid, "OK")
            if status == "OK":
                self.trash.add(uid)
            return status, []
        if command == "STORE":
            status = self.store_status.get(uid, "OK")
            if status == "OK":
                self.deleted.add(uid)
            return status, []
        raise AssertionError(f"Unexpected IMAP command: {command}")

    def expunge(self):
        self.calls.append(("EXPUNGE", None))
        if isinstance(self.expunge_status, Exception):
            raise self.expunge_status
        if self.expunge_status == "OK":
            self.inbox.difference_update(self.deleted)
            self.deleted.clear()
        return self.expunge_status, []


@pytest.fixture
def bulk_delete(monkeypatch):
    from routes.email import email_routes as routes

    mailbox = Mailbox()
    index_delete = Mock()

    @contextmanager
    def imap(account_id=None, owner=""):
        assert owner == "alice"
        yield mailbox

    monkeypatch.setattr(routes, "_start_poller", lambda: None)
    monkeypatch.setattr(routes, "_POOL_HOOKS", {})
    monkeypatch.setattr(routes, "_imap", imap)
    monkeypatch.setattr(routes, "_resolve_mail_folder", lambda *args: "Trash")
    monkeypatch.setattr(routes, "_list_imap_folders", lambda conn: ({}, ["INBOX", "Trash"]))
    monkeypatch.setattr(routes, "_email_index_delete", index_delete)
    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: "alice"

    with TestClient(app) as client:
        def delete(uids):
            response = client.post("/api/email/delete-bulk", json={"uids": uids})
            assert response.status_code == 200
            return response.json()

        yield SimpleNamespace(mailbox=mailbox, delete=delete, index_delete=index_delete)


@pytest.mark.parametrize("copy_status", ["NO", "BAD"])
def test_failed_trash_copy_preserves_original(bulk_delete, copy_status):
    bulk_delete.mailbox.copy_status["101"] = copy_status

    result = bulk_delete.delete(["101"])

    assert "101" in bulk_delete.mailbox.inbox
    assert bulk_delete.mailbox.deleted == set()
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert ("STORE", "101") not in bulk_delete.mailbox.calls
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_mixed_batch_only_deletes_messages_copied_to_trash(bulk_delete):
    bulk_delete.mailbox.copy_status["102"] = "NO"

    result = bulk_delete.delete(["101", "102", "103"])

    assert bulk_delete.mailbox.inbox == {"102"}
    assert bulk_delete.mailbox.trash == {"101", "103"}
    assert result["deleted_uids"] == ["101", "103"]
    assert result["failed_uids"] == ["102"]
    assert [call.args[-1] for call in bulk_delete.index_delete.call_args_list] == ["101", "103"]


@pytest.mark.parametrize("expunge_status", ["NO", "BAD", RuntimeError("Disconnected")])
def test_failed_expunge_keeps_index_and_reports_failure(bulk_delete, expunge_status):
    bulk_delete.mailbox.expunge_status = expunge_status

    result = bulk_delete.delete(["101", "102"])

    assert bulk_delete.mailbox.inbox == {"101", "102", "103"}
    assert bulk_delete.mailbox.trash == {"101", "102"}
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101", "102"]
    bulk_delete.index_delete.assert_not_called()


def test_store_failure_retains_original_and_index(bulk_delete):
    bulk_delete.mailbox.store_status["101"] = "NO"

    result = bulk_delete.delete(["101"])

    assert "101" in bulk_delete.mailbox.inbox
    assert bulk_delete.mailbox.trash == {"101"}
    assert result["failed_uids"] == ["101"]
    assert result["deleted_uids"] == []
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_native_move_needs_no_copy_or_expunge(bulk_delete):
    bulk_delete.mailbox.move_status = "OK"

    result = bulk_delete.delete(["101", "102"])

    assert result["deleted_uids"] == ["101", "102"]
    assert result["failed_uids"] == []
    assert bulk_delete.mailbox.inbox == {"103"}
    assert bulk_delete.mailbox.trash == {"101", "102"}
    assert not any(command in {"COPY", "STORE", "EXPUNGE"} for command, _ in bulk_delete.mailbox.calls)


def test_copy_fallback_expunge_runs_once_for_the_batch(bulk_delete):
    result = bulk_delete.delete(["101", "102"])

    assert result["deleted_uids"] == ["101", "102"]
    assert result["failed_uids"] == []
    assert bulk_delete.mailbox.inbox == {"103"}
    assert bulk_delete.mailbox.trash == {"101", "102"}
    assert bulk_delete.mailbox.calls.count(("EXPUNGE", None)) == 1
    assert bulk_delete.index_delete.call_count == 2


def test_failed_folder_selection_never_changes_messages(bulk_delete):
    bulk_delete.mailbox.select_status = "NO"

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["failed_uids"] == ["101"]
    assert bulk_delete.mailbox.calls == []
    bulk_delete.index_delete.assert_not_called()


@pytest.mark.parametrize("expunge_status", ["NO", RuntimeError("Disconnected")])
def test_completed_native_move_is_retained_when_fallback_expunge_fails(bulk_delete, expunge_status):
    bulk_delete.mailbox.move_status = {"101": "OK"}
    bulk_delete.mailbox.expunge_status = expunge_status

    result = bulk_delete.delete(["101", "102"])

    assert result["deleted_uids"] == ["101"]
    assert result["failed_uids"] == ["102"]
    assert bulk_delete.mailbox.inbox == {"102", "103"}
    assert bulk_delete.mailbox.trash == {"101", "102"}
    bulk_delete.index_delete.assert_called_once_with("alice", None, "INBOX", "101")


def test_absent_uid_is_reported_failed_without_deleting_another_message(bulk_delete):
    result = bulk_delete.delete(["999"])

    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["999"]
    assert bulk_delete.mailbox.inbox == {"101", "102", "103"}
    assert bulk_delete.mailbox.trash == set()
    assert not any(command in {"MOVE", "COPY", "STORE", "EXPUNGE"} for command, _ in bulk_delete.mailbox.calls)
    bulk_delete.index_delete.assert_not_called()


def test_duplicate_and_invalid_uids_do_not_repeat_mail_operations(bulk_delete):
    result = bulk_delete.delete(["101", "101", "", None, "invalid"])

    assert result["deleted_uids"] == ["101"]
    assert result["failed_uids"] == []
    assert bulk_delete.mailbox.calls.count(("COPY", "101")) == 1
    bulk_delete.index_delete.assert_called_once_with("alice", None, "INBOX", "101")
