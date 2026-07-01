"""Bulk move: POST /api/document-folders/move-documents.

Moves the caller's OWN documents into a folder (or unfiles them with folder_id
null) in one atomic UPDATE, returns the moved count, 404s on a target folder
that isn't the caller's, and only ever touches the caller's docs.
"""
import pytest
from fastapi import HTTPException

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_bulk_move_files_owned_docs(h):
    fid = h.create_folder("alice", "Acme")["id"]
    d1 = h.seed_doc("alice", folder_id=None)
    d2 = h.seed_doc("alice", folder_id=None)
    res = h.move_documents("alice", [d1, d2], fid)
    assert res == {"ok": True, "count": 2}
    assert h.get_doc_folder_id(d1) == fid
    assert h.get_doc_folder_id(d2) == fid


def test_bulk_move_into_subfolder(h):
    root = h.create_folder("alice", "Root")["id"]
    sub = h.create_folder("alice", "Sub", parent_id=root)["id"]
    d1 = h.seed_doc("alice", folder_id=None)
    res = h.move_documents("alice", [d1], sub)
    assert res["count"] == 1
    assert h.get_doc_folder_id(d1) == sub


def test_bulk_unfile_with_null_target(h):
    fid = h.create_folder("alice", "Acme")["id"]
    d1 = h.seed_doc("alice", folder_id=fid)
    res = h.move_documents("alice", [d1], None)
    assert res["count"] == 1
    assert h.get_doc_folder_id(d1) is None


def test_bulk_move_only_touches_callers_docs(h):
    fid = h.create_folder("alice", "Acme")["id"]
    a_doc = h.seed_doc("alice", folder_id=None)
    b_doc = h.seed_doc("bob", folder_id=None)
    res = h.move_documents("alice", [a_doc, b_doc], fid)
    assert res["count"] == 1               # bob's doc ignored
    assert h.get_doc_folder_id(a_doc) == fid
    assert h.get_doc_folder_id(b_doc) is None


def test_bulk_move_into_foreign_folder_is_404(h):
    bob_folder = h.create_folder("bob", "BobFolder")["id"]
    d1 = h.seed_doc("alice", folder_id=None)
    with pytest.raises(HTTPException) as ei:
        h.move_documents("alice", [d1], bob_folder)
    assert ei.value.status_code == 404
    assert h.get_doc_folder_id(d1) is None


def test_bulk_move_empty_list_is_noop(h):
    fid = h.create_folder("alice", "Acme")["id"]
    res = h.move_documents("alice", [], fid)
    assert res == {"ok": True, "count": 0}
