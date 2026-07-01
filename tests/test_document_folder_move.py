"""PATCH /api/document/{id} folder_id filing semantics.

folder_id present + value -> file (owner-validated); present + ""/"__none__"/
null -> unfile (NULL); absent -> untouched (a rename-only PATCH must NOT wipe
the folder). Filing into another user's folder, or moving a doc you don't own,
is a 404 (no IDOR / no cross-owner leak).
"""
import pytest
from fastapi import HTTPException

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_set_folder(h):
    fid = h.create_folder("alice", "Acme")["id"]
    doc = h.seed_doc("alice", folder_id=None)
    res = h.patch_doc("alice", doc, {"folder_id": fid})
    assert res["folder_id"] == fid
    assert res["folder_name"] == "Acme"
    assert h.get_doc_folder_id(doc) == fid


def test_change_folder(h):
    a = h.create_folder("alice", "A")["id"]
    b = h.create_folder("alice", "B")["id"]
    doc = h.seed_doc("alice", folder_id=a)
    res = h.patch_doc("alice", doc, {"folder_id": b})
    assert res["folder_id"] == b
    assert h.get_doc_folder_id(doc) == b


@pytest.mark.parametrize("clear_value", ["", "__none__", None])
def test_clear_folder(h, clear_value):
    fid = h.create_folder("alice", "Acme")["id"]
    doc = h.seed_doc("alice", folder_id=fid)
    res = h.patch_doc("alice", doc, {"folder_id": clear_value})
    assert res["folder_id"] is None
    assert res["folder_name"] is None
    assert h.get_doc_folder_id(doc) is None


def test_absent_folder_id_leaves_filing_untouched(h):
    fid = h.create_folder("alice", "Acme")["id"]
    doc = h.seed_doc("alice", folder_id=fid)
    # A title-only PATCH must not clobber the folder.
    res = h.patch_doc("alice", doc, {"title": "Renamed"})
    assert res["title"] == "Renamed"
    assert res["folder_id"] == fid
    assert h.get_doc_folder_id(doc) == fid


def test_cannot_file_into_another_users_folder(h):
    bob_folder = h.create_folder("bob", "BobFolder")["id"]
    doc = h.seed_doc("alice", folder_id=None)
    with pytest.raises(HTTPException) as ei:
        h.patch_doc("alice", doc, {"folder_id": bob_folder})
    assert ei.value.status_code == 404
    assert h.get_doc_folder_id(doc) is None


def test_cannot_move_a_doc_you_do_not_own(h):
    fid = h.create_folder("alice", "Acme")["id"]
    bob_doc = h.seed_doc("bob", folder_id=None)
    with pytest.raises(HTTPException) as ei:
        h.patch_doc("alice", bob_doc, {"folder_id": fid})
    assert ei.value.status_code == 404
    assert h.get_doc_folder_id(bob_doc) is None


def test_set_nonexistent_folder_is_404(h):
    doc = h.seed_doc("alice", folder_id=None)
    with pytest.raises(HTTPException) as ei:
        h.patch_doc("alice", doc, {"folder_id": "ghost"})
    assert ei.value.status_code == 404
