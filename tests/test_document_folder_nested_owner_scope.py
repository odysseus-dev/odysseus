"""Owner-scoping for the NESTED-folder surface (no IDOR, no cross-owner leak).

Bob cannot create under, move, or reparent into Alice's folder; cannot bulk-move
into it; cannot scope the library to it. Every cross-owner attempt is a 404 and
Alice's tree is left untouched. Behavioral (drives the real route closures via
the harness), extending test_document_folder_owner_scope.py to the tree surface.
"""
import pytest
from fastapi import HTTPException

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_b_cannot_create_under_as_parent(h):
    a_folder = h.create_folder("alice", "Acme")["id"]
    with pytest.raises(HTTPException) as ei:
        h.create_folder("bob", "Sneaky", parent_id=a_folder)
    assert ei.value.status_code == 404
    assert h.list_folders("bob")["folders"] == []   # nothing created for bob


def test_b_cannot_move_alices_folder(h):
    a_folder = h.create_folder("alice", "Acme")["id"]
    b_folder = h.create_folder("bob", "BobRoot")["id"]
    # bob tries to reparent ALICE's folder under bob's -> 404 (not bob's folder).
    with pytest.raises(HTTPException) as ei:
        h.move_folder("bob", a_folder, b_folder)
    assert ei.value.status_code == 404
    assert h.folder_parent_id(a_folder) is None


def test_b_cannot_reparent_into_alices_folder(h):
    a_folder = h.create_folder("alice", "Acme")["id"]
    b_folder = h.create_folder("bob", "BobRoot")["id"]
    # bob owns b_folder but tries to move it under ALICE's folder -> 404.
    with pytest.raises(HTTPException) as ei:
        h.move_folder("bob", b_folder, a_folder)
    assert ei.value.status_code == 404
    assert h.folder_parent_id(b_folder) is None


def test_b_cannot_bulk_move_into_alices_folder(h):
    a_folder = h.create_folder("alice", "Acme")["id"]
    b_doc = h.seed_doc("bob", folder_id=None)
    with pytest.raises(HTTPException) as ei:
        h.move_documents("bob", [b_doc], a_folder)
    assert ei.value.status_code == 404
    assert h.get_doc_folder_id(b_doc) is None


def test_b_cannot_scope_library_to_alices_folder(h):
    a_folder = h.create_folder("alice", "Acme")["id"]
    with pytest.raises(HTTPException) as ei:
        h.library("bob", folder_id=a_folder)
    assert ei.value.status_code == 404


def test_owner_partitioned_move_stays_isolated(h):
    a_folder = h.create_folder("alice", "Acme")["id"]
    h.create_folder("bob", "Beta")
    a_doc = h.seed_doc("alice", folder_id=None)
    # alice moving her own doc into her own folder works and is isolated.
    h.move_documents("alice", [a_doc], a_folder)
    assert h.get_doc_folder_id(a_doc) == a_folder
    # bob still only sees his own folder.
    assert {f["name"] for f in h.list_folders("bob")["folders"]} == {"Beta"}
