"""CRUD for document folders (POST/GET/PATCH/DELETE /api/document-folders).

Exercises the real route handlers against a file-backed sqlite DB via the
shared DocFoldersHarness: find-or-create (case-insensitive), rename, rename
collision -> 409, and delete returning the reassigned count + removing the row
while NEVER deleting documents. Mirrors the owner-scope precedent in
test_gallery_album_owner_scope.py but asserts on behavior, not source text.
"""
import pytest
from fastapi import HTTPException

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_create_returns_id_and_created_true(h):
    res = h.create_folder("alice", "Acme")
    assert res["name"] == "Acme"
    assert res["created"] is True
    assert res["id"]


def test_create_is_find_or_create_case_insensitive(h):
    first = h.create_folder("alice", "Acme")
    again = h.create_folder("alice", "ACME")
    assert again["id"] == first["id"]
    assert again["created"] is False
    # Only one folder exists for the owner.
    assert len(h.list_folders("alice")["folders"]) == 1


def test_create_normalizes_whitespace(h):
    a = h.create_folder("alice", "  Acme   Corp ")
    assert a["name"] == "Acme Corp"
    b = h.create_folder("alice", "Acme Corp")
    assert b["id"] == a["id"] and b["created"] is False


def test_create_blank_name_is_400(h):
    for bad in ("", "   ", "\t\n"):
        with pytest.raises(HTTPException) as ei:
            h.create_folder("alice", bad)
        assert ei.value.status_code == 400


def test_rename_folder(h):
    fid = h.create_folder("alice", "Old")["id"]
    res = h.rename_folder("alice", fid, "New")
    # PATCH now returns the nested shape: ok/id/name/parent_id/depth.
    assert res["ok"] is True
    assert res["id"] == fid
    assert res["name"] == "New"
    assert res["parent_id"] is None   # still a root folder
    assert res["depth"] == 1
    names = {f["name"] for f in h.list_folders("alice")["folders"]}
    assert names == {"New"}


def test_rename_to_own_name_different_case_is_allowed(h):
    fid = h.create_folder("alice", "Acme")["id"]
    res = h.rename_folder("alice", fid, "ACME")
    assert res["ok"] is True and res["name"] == "ACME"


def test_rename_collision_returns_409(h):
    h.create_folder("alice", "Acme")
    other = h.create_folder("alice", "Beta")["id"]
    with pytest.raises(HTTPException) as ei:
        h.rename_folder("alice", other, "acme")  # collides case-insensitively
    assert ei.value.status_code == 409
    # The losing rename left both folders intact.
    assert {f["name"] for f in h.list_folders("alice")["folders"]} == {"Acme", "Beta"}


def test_rename_missing_folder_is_404(h):
    with pytest.raises(HTTPException) as ei:
        h.rename_folder("alice", "does-not-exist", "X")
    assert ei.value.status_code == 404


def test_delete_unfiles_docs_and_removes_row(h):
    fid = h.create_folder("alice", "Acme")["id"]
    d1 = h.seed_doc("alice", folder_id=fid)
    d2 = h.seed_doc("alice", folder_id=fid)
    h.seed_doc("alice", folder_id=None)  # unrelated, must not count

    res = h.delete_folder("alice", fid)
    assert res["ok"] is True
    assert res["reparented_docs"] == 2
    assert res["new_parent_id"] is None   # top-level folder -> docs go to root
    assert h.folder_exists(fid) is False
    # Documents survive, just unfiled.
    assert h.get_doc_folder_id(d1) is None
    assert h.get_doc_folder_id(d2) is None


def test_delete_missing_folder_is_404(h):
    with pytest.raises(HTTPException) as ei:
        h.delete_folder("alice", "nope")
    assert ei.value.status_code == 404


def test_list_includes_empty_folders_with_zero_count(h):
    empty = h.create_folder("alice", "Empty")["id"]
    full = h.create_folder("alice", "Full")["id"]
    h.seed_doc("alice", folder_id=full)
    by_id = {f["id"]: f for f in h.list_folders("alice")["folders"]}
    assert by_id[empty]["count"] == 0
    assert by_id[full]["count"] == 1
