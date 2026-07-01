"""Owner-scoping for document folders (no IDOR, no cross-owner leak).

User B cannot list, rename, delete, or file into user A's folder. A folder's
DELETE only nulls (and counts) the CALLER's documents — even a document that
(improperly) points at the folder but belongs to someone else is left out of
the reassigned count. find_or_create is per-owner: the same name for two owners
yields two distinct folders. Mirrors test_document_tool_owner_scope.py's intent.
"""
import uuid

import pytest
from fastapi import HTTPException

from core.database import Document
from src.document_folders import find_or_create_folder
from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_list_is_owner_scoped(h):
    h.create_folder("alice", "AliceFolder")
    h.create_folder("bob", "BobFolder")
    assert {f["name"] for f in h.list_folders("bob")["folders"]} == {"BobFolder"}


def test_b_cannot_rename_as_folder(h):
    fid = h.create_folder("alice", "Acme")["id"]
    with pytest.raises(HTTPException) as ei:
        h.rename_folder("bob", fid, "Hacked")
    assert ei.value.status_code == 404
    # Alice's folder name is unchanged.
    assert {f["name"] for f in h.list_folders("alice")["folders"]} == {"Acme"}


def test_b_cannot_delete_as_folder(h):
    fid = h.create_folder("alice", "Acme")["id"]
    with pytest.raises(HTTPException) as ei:
        h.delete_folder("bob", fid)
    assert ei.value.status_code == 404
    assert h.folder_exists(fid) is True


def test_b_cannot_file_into_as_folder(h):
    fid = h.create_folder("alice", "Acme")["id"]
    doc = h.seed_doc("bob", folder_id=None)
    with pytest.raises(HTTPException) as ei:
        h.patch_doc("bob", doc, {"folder_id": fid})
    assert ei.value.status_code == 404


def test_delete_only_nulls_and_counts_callers_docs(h):
    fid = h.create_folder("alice", "Acme")["id"]
    a1 = h.seed_doc("alice", folder_id=fid)
    a2 = h.seed_doc("alice", folder_id=fid)
    # Defense-in-depth: a foreign-owned doc that (improperly) points at the
    # folder must NOT be in the reassigned count even though the FK SET NULL
    # backstop will still detach it when the folder row is deleted.
    bob_doc = h.seed_doc("bob", folder_id=fid)

    res = h.delete_folder("alice", fid)
    assert res["reparented_docs"] == 2  # only alice's two docs were explicitly nulled
    assert h.get_doc_folder_id(a1) is None
    assert h.get_doc_folder_id(a2) is None
    # bob's doc survives; the FK detached it but it was never deleted.
    db = h.SessionLocal()
    try:
        assert db.query(Document).filter(Document.id == bob_doc).first() is not None
    finally:
        db.close()


def test_find_or_create_is_per_owner(h):
    db = h.SessionLocal()
    try:
        fa = find_or_create_folder(db, "alice", "Acme")
        fb = find_or_create_folder(db, "bob", "Acme")
        assert fa.id != fb.id
        assert fa.owner == "alice" and fb.owner == "bob"
        # Re-asking for alice's "Acme" reuses her row, not bob's.
        again = find_or_create_folder(db, "alice", "acme")
        assert again.id == fa.id
    finally:
        db.close()
