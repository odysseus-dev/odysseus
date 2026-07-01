"""Non-destructive folder delete: reparent docs AND subfolders up one level.

Deleting a folder moves the caller's direct documents and direct subfolders to
the deleted folder's parent (NULL if it was top-level), removes ONLY that row,
and is owner-scoped + atomic. Grandchildren ride along under the surviving
subfolder (only DIRECT children are reparented, one level).
"""
import pytest
from fastapi import HTTPException

from core.database import Document, DocumentFolder
from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_delete_midtree_reparents_docs_and_subfolders_to_parent(h):
    root = h.create_folder("alice", "Root")["id"]
    mid = h.create_folder("alice", "Mid", parent_id=root)["id"]
    child = h.create_folder("alice", "Child", parent_id=mid)["id"]
    grand = h.create_folder("alice", "Grand", parent_id=child)["id"]
    doc = h.seed_doc("alice", folder_id=mid)

    res = h.delete_folder("alice", mid)
    assert res["ok"] is True
    assert res["new_parent_id"] == root
    assert res["reparented_docs"] == 1
    assert res["reparented_folders"] == 1
    # Only the mid row is gone; everything else survives.
    assert h.folder_exists(mid) is False
    assert h.folder_exists(root) is True
    assert h.folder_exists(child) is True
    assert h.folder_exists(grand) is True
    # Direct doc + direct subfolder now hang off root; grandchild rides along.
    assert h.get_doc_folder_id(doc) == root
    assert h.folder_parent_id(child) == root
    assert h.folder_parent_id(grand) == child   # unchanged (only 1 level moved)


def test_delete_top_level_sends_children_to_root(h):
    top = h.create_folder("alice", "Top")["id"]
    sub = h.create_folder("alice", "Sub", parent_id=top)["id"]
    doc = h.seed_doc("alice", folder_id=top)

    res = h.delete_folder("alice", top)
    assert res["new_parent_id"] is None
    assert res["reparented_docs"] == 1
    assert res["reparented_folders"] == 1
    assert h.get_doc_folder_id(doc) is None      # unfiled
    assert h.folder_parent_id(sub) is None        # now a root folder


def test_delete_is_owner_scoped_for_reparenting(h):
    # bob's doc/subfolder that (improperly) point at alice's folder must not be
    # counted; the FK SET NULL still detaches them but nothing is deleted.
    fid = h.create_folder("alice", "Acme")["id"]
    a_doc = h.seed_doc("alice", folder_id=fid)
    b_doc = h.seed_doc("bob", folder_id=fid)
    b_sub = h.seed_folder("bob", "BobSub", parent_id=fid)

    res = h.delete_folder("alice", fid)
    assert res["reparented_docs"] == 1     # only alice's doc explicitly moved
    assert res["reparented_folders"] == 0  # bob's subfolder is not alice's
    # bob's rows survive (detached by the FK backstop, never deleted).
    db = h.SessionLocal()
    try:
        assert db.query(Document).filter(Document.id == b_doc).first() is not None
        assert db.query(DocumentFolder).filter(DocumentFolder.id == b_sub).first() is not None
    finally:
        db.close()


def test_delete_missing_folder_is_404(h):
    with pytest.raises(HTTPException) as ei:
        h.delete_folder("alice", "nope")
    assert ei.value.status_code == 404
