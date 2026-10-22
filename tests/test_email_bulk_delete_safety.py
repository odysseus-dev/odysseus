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
        self.uid_expunge_status = None
        self.select_status = "OK"

    def select(self, folder, readonly=False):
        return self.select_status, [b"3"]

    def uid(self, command, raw_uid, *args):
        uid = raw_uid.decode() if isinstance(raw_uid, bytes) else (str(raw_uid) if raw_uid is not None else "")
        self.calls.append((command, uid))
        if command == "FETCH":
            if uid not in self.inbox:
                return "OK", []
            if args and "(FLAGS)" in args:
                flags = rb"(\Deleted)" if uid in self.deleted else rb"()"
                return "OK", [(f"1 (UID {uid} FLAGS ".encode() + flags + b")")]
            return "OK", [f"1 (UID {uid})".encode()]
        if command == "SEARCH":
            if args and "DELETED" in args:
                return "OK", [b" ".join(u.encode() for u in sorted(self.deleted))]
            searched_uid = args[-1].split()[-1] if args else ""
            return "OK", [searched_uid.encode() if searched_uid in self.inbox else b""]
        if command == "MOVE":
            status = self.move_status.get(uid, "NO") if isinstance(self.move_status, dict) else self.move_status
            if isinstance(status, Exception):
                raise status
            if status == "OK":
                self.trash.add(uid)
                self.inbox.remove(uid)
            return status, []
        if command == "COPY":
            status = self.copy_status.get(uid, "OK")
            if isinstance(status, Exception):
                raise status
            if status == "OK":
                self.trash.add(uid)
            return status, []
        if command == "STORE":
            status = self.store_status.get(uid, "OK")
            if isinstance(status, Exception):
                raise status
            if status == "OK":
                if args and args[0] == "-FLAGS":
                    self.deleted.discard(uid)
                else:
                    self.deleted.add(uid)
            return status, []
        if command == "EXPUNGE":
            status = self.uid_expunge_status if self.uid_expunge_status is not None else self.expunge_status
            if isinstance(status, Exception):
                raise status
            if status == "OK":
                expunged = {u for u in uid.split(",") if u in self.deleted}
                self.inbox.difference_update(expunged)
                self.deleted.difference_update(expunged)
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
    invalidate_cache = Mock()

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
    monkeypatch.setitem(routes._CACHE_HOOKS, "invalidate_list", invalidate_cache)
    app = FastAPI()
    app.include_router(routes.setup_email_routes())
    app.dependency_overrides[routes.require_owner] = lambda: "alice"

    with TestClient(app) as client:
        def delete(uids):
            response = client.post("/api/email/delete-bulk", json={"uids": uids})
            assert response.status_code == 200
            return response.json()

        yield SimpleNamespace(
            mailbox=mailbox,
            delete=delete,
            index_delete=index_delete,
            invalidate_cache=invalidate_cache,
            routes=routes,
        )


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
    assert sum(1 for command, _ in bulk_delete.mailbox.calls if command == "EXPUNGE") == 1
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


def test_bulk_delete_invalidates_source_and_trash_caches(bulk_delete):
    result = bulk_delete.delete(["101"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    bulk_delete.invalidate_cache.assert_any_call(None, "INBOX")
    bulk_delete.invalidate_cache.assert_any_call(None, "Trash")


def test_partial_failure_with_exception_invalidates_caches(bulk_delete):
    bulk_delete.mailbox.move_status = {"101": "OK"}
    bulk_delete.mailbox.uid_expunge_status = RuntimeError("Socket reset during expunge")
    bulk_delete.mailbox.expunge_status = RuntimeError("Socket reset during expunge")

    result = bulk_delete.delete(["101", "102"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    assert result["failed_uids"] == ["102"]
    assert result["error"] == "Mail operation partially failed"
    bulk_delete.invalidate_cache.assert_any_call(None, "INBOX")
    bulk_delete.invalidate_cache.assert_any_call(None, "Trash")


def test_total_failure_returns_success_false(bulk_delete):
    bulk_delete.mailbox.copy_status["101"] = "NO"

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["error"] == "Failed to delete requested emails"
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]


def test_uid_expunge_preserves_unrelated_deleted_message(bulk_delete):
    # Unrelated message 999 is already marked \Deleted before bulk delete
    bulk_delete.mailbox.inbox.add("999")
    bulk_delete.mailbox.deleted.add("999")

    result = bulk_delete.delete(["101"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    # 101 was expunged, 999 remains in inbox and remains marked deleted
    assert "101" not in bulk_delete.mailbox.inbox
    assert "999" in bulk_delete.mailbox.inbox
    assert bulk_delete.mailbox.deleted == {"999"}
    assert ("EXPUNGE", "101") in bulk_delete.mailbox.calls
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls


def test_server_lacking_uidplus_never_calls_mailbox_expunge(bulk_delete):
    # Server does not support UIDPLUS (UID EXPUNGE returns BAD)
    # Even if no other deleted messages exist, mailbox-wide EXPUNGE is strictly forbidden.
    bulk_delete.mailbox.uid_expunge_status = "BAD"
    assert bulk_delete.mailbox.deleted == set()

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert "101" in bulk_delete.mailbox.inbox
    assert "101" not in bulk_delete.mailbox.deleted
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_concurrent_client_marks_unrelated_deleted_message_during_operation(bulk_delete):
    # Another client marks unrelated UID 999 \Deleted before/during the operation
    bulk_delete.mailbox.inbox.add("999")
    bulk_delete.mailbox.deleted.add("999")

    result = bulk_delete.delete(["101"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    # 101 was expunged, unrelated 999 is NEVER expunged
    assert "101" not in bulk_delete.mailbox.inbox
    assert "999" in bulk_delete.mailbox.inbox
    assert bulk_delete.mailbox.deleted == {"999"}
    assert ("EXPUNGE", "101") in bulk_delete.mailbox.calls
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls


def test_preexisting_deleted_flag_preserved_when_expunge_fails(bulk_delete):
    # Message 101 already had \Deleted flag before the bulk operation
    bulk_delete.mailbox.deleted.add("101")
    bulk_delete.mailbox.uid_expunge_status = "NO"

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert "101" in bulk_delete.mailbox.inbox
    # Pre-existing \Deleted flag must NOT be removed
    assert "101" in bulk_delete.mailbox.deleted
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_preexisting_deleted_flag_expunged_on_success(bulk_delete):
    # Message 101 already had \Deleted flag before the bulk operation
    bulk_delete.mailbox.deleted.add("101")

    result = bulk_delete.delete(["101"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    assert "101" not in bulk_delete.mailbox.inbox
    assert "101" not in bulk_delete.mailbox.deleted
    assert "101" in bulk_delete.mailbox.trash
    bulk_delete.index_delete.assert_called_once_with("alice", None, "INBOX", "101")


@pytest.mark.parametrize("status", ["NO", "BAD"])
def test_uid_expunge_status_failures(bulk_delete, status):
    bulk_delete.mailbox.uid_expunge_status = status

    result = bulk_delete.delete(["101", "102"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101", "102"]
    assert bulk_delete.mailbox.inbox == {"101", "102", "103"}
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_uid_expunge_connection_exception_handled_conservatively(bulk_delete):
    bulk_delete.mailbox.uid_expunge_status = ConnectionResetError("Connection lost during selective expunge")

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert "101" in bulk_delete.mailbox.inbox
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_disconnect_after_copy_before_store_preserves_message(bulk_delete):
    # COPY succeeds, but STORE raises a disconnect
    bulk_delete.mailbox.store_status["101"] = ConnectionResetError("Disconnected")

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert "101" in bulk_delete.mailbox.inbox
    assert "101" in bulk_delete.mailbox.trash
    assert bulk_delete.mailbox.deleted == set()
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_disconnect_after_store_preserves_messages_and_index(bulk_delete):
    # COPY and STORE succeed, but UID EXPUNGE disconnects
    bulk_delete.mailbox.uid_expunge_status = ConnectionResetError("Disconnected during expunge")

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert "101" in bulk_delete.mailbox.inbox
    assert "101" in bulk_delete.mailbox.trash
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()


def test_disconnect_with_partial_move_success(bulk_delete):
    # 101 succeeds via MOVE, 102 falls back to COPY+STORE and UID EXPUNGE disconnects
    bulk_delete.mailbox.move_status = {"101": "OK", "102": "NO"}
    bulk_delete.mailbox.uid_expunge_status = ConnectionResetError("Disconnected during expunge")

    result = bulk_delete.delete(["101", "102"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    assert result["failed_uids"] == ["102"]
    assert "101" not in bulk_delete.mailbox.inbox
    assert "102" in bulk_delete.mailbox.inbox
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_called_once_with("alice", None, "INBOX", "101")
    bulk_delete.invalidate_cache.assert_any_call(None, "INBOX")
    bulk_delete.invalidate_cache.assert_any_call(None, "Trash")


def test_lost_acknowledgement_retains_local_index_and_permits_safe_retry(bulk_delete):
    # Server succeeds in moving, but connection drops during acknowledgement
    bulk_delete.mailbox.uid_expunge_status = OSError("Connection dropped")
    bulk_delete.mailbox.expunge_status = OSError("Connection dropped")

    result = bulk_delete.delete(["101"])

    assert result["success"] is False
    assert result["deleted_uids"] == []
    assert result["failed_uids"] == ["101"]
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
    bulk_delete.index_delete.assert_not_called()

    # Retry when server is reconnected and 101 was already moved (absent from source)
    bulk_delete.mailbox.inbox.discard("101")
    bulk_delete.mailbox.uid_expunge_status = "OK"
    bulk_delete.mailbox.expunge_status = "OK"

    retry_result = bulk_delete.delete(["101"])
    assert retry_result["deleted_uids"] == []
    assert retry_result["failed_uids"] == ["101"]
    bulk_delete.index_delete.assert_not_called()


def test_repeated_requests_after_partial_failure(bulk_delete):
    # Initial request: 101 moves, 102 fails copy
    bulk_delete.mailbox.move_status = {"101": "OK", "102": "NO"}
    bulk_delete.mailbox.copy_status["102"] = "NO"

    res1 = bulk_delete.delete(["101", "102"])
    assert res1["deleted_uids"] == ["101"]
    assert res1["failed_uids"] == ["102"]

    # Retry with failed UID 102 now succeeding via copy
    bulk_delete.mailbox.copy_status["102"] = "OK"
    res2 = bulk_delete.delete(["102"])
    assert res2["deleted_uids"] == ["102"]
    assert res2["failed_uids"] == []

    assert bulk_delete.mailbox.inbox == {"103"}
    assert bulk_delete.mailbox.trash == {"101", "102"}
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls


def test_unrelated_messages_never_expunged(bulk_delete):
    # Mailbox contains unrelated 888 (with \Deleted) and 999 (without \Deleted)
    bulk_delete.mailbox.inbox.update({"888", "999"})
    bulk_delete.mailbox.deleted.add("888")

    result = bulk_delete.delete(["101"])

    assert result["success"] is True
    assert result["deleted_uids"] == ["101"]
    assert "101" not in bulk_delete.mailbox.inbox
    assert "101" in bulk_delete.mailbox.trash
    # 888 and 999 are completely untouched in inbox
    assert "888" in bulk_delete.mailbox.inbox
    assert "999" in bulk_delete.mailbox.inbox
    assert "888" in bulk_delete.mailbox.deleted
    assert "888" not in bulk_delete.mailbox.trash
    assert "999" not in bulk_delete.mailbox.trash
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls


def test_standard_expunge_never_invoked_by_fallback(bulk_delete):
    # Perform operations under multiple fallback conditions; standard expunge is never called
    bulk_delete.delete(["101"])
    assert ("EXPUNGE", None) not in bulk_delete.mailbox.calls
