"""Folder tree: depth cap on create + move, and anti-cycle on move.

Root = depth 1; the tree may nest up to DOCUMENT_FOLDER_MAX_DEPTH levels. Creating
a child of a max-depth folder is 400; moving a subtree so its deepest node would
pass the cap is 400; moving a folder into itself or one of its descendants is 400.
Duplicate names are allowed under different parents. Behavioral — drives the real
route closures through the shared harness.
"""
import pytest
from fastapi import HTTPException

from src.constants import DOCUMENT_FOLDER_MAX_DEPTH
from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def _chain(h, user, depth):
    """Create a root->...->leaf chain `depth` folders deep; return the ids."""
    ids = []
    parent = None
    for i in range(depth):
        res = h.create_folder(user, f"L{i}", parent_id=parent)
        ids.append(res["id"])
        parent = res["id"]
    return ids


def test_create_root_is_depth_1(h):
    res = h.create_folder("alice", "Root")
    assert res["parent_id"] is None
    assert res["depth"] == 1
    assert res["count"] == 0


def test_create_child_reports_incremented_depth(h):
    root = h.create_folder("alice", "Root")["id"]
    child = h.create_folder("alice", "Child", parent_id=root)
    assert child["parent_id"] == root
    assert child["depth"] == 2


def test_create_at_max_depth_ok_but_beyond_is_400(h):
    ids = _chain(h, "alice", DOCUMENT_FOLDER_MAX_DEPTH)
    by_id = {f["id"]: f for f in h.list_folders("alice")["folders"]}
    # The chain reached exactly the cap.
    assert by_id[ids[-1]]["depth"] == DOCUMENT_FOLDER_MAX_DEPTH
    # A child of the max-depth leaf would be depth+1 -> rejected.
    with pytest.raises(HTTPException) as ei:
        h.create_folder("alice", "TooDeep", parent_id=ids[-1])
    assert ei.value.status_code == 400


def test_create_under_nonexistent_parent_is_404(h):
    with pytest.raises(HTTPException) as ei:
        h.create_folder("alice", "X", parent_id="ghost")
    assert ei.value.status_code == 404


def test_duplicate_name_allowed_under_different_parents(h):
    a = h.create_folder("alice", "A")["id"]
    b = h.create_folder("alice", "B")["id"]
    ca = h.create_folder("alice", "Same", parent_id=a)
    cb = h.create_folder("alice", "Same", parent_id=b)
    assert ca["id"] != cb["id"]           # two distinct folders, same name
    assert ca["created"] is True and cb["created"] is True


def test_reask_same_slot_reuses_folder(h):
    a = h.create_folder("alice", "A")["id"]
    first = h.create_folder("alice", "Same", parent_id=a)
    again = h.create_folder("alice", "same", parent_id=a)  # case-insensitive
    assert again["id"] == first["id"]
    assert again["created"] is False


def test_move_folder_updates_parent_and_depth(h):
    root = h.create_folder("alice", "Root")["id"]
    movable = h.create_folder("alice", "Movable")["id"]  # currently root (depth 1)
    res = h.move_folder("alice", movable, root)
    assert res["parent_id"] == root
    assert res["depth"] == 2
    assert h.folder_parent_id(movable) == root


def test_move_to_root_via_null_parent(h):
    root = h.create_folder("alice", "Root")["id"]
    child = h.create_folder("alice", "Child", parent_id=root)["id"]
    res = h.move_folder("alice", child, None)
    assert res["parent_id"] is None
    assert res["depth"] == 1
    assert h.folder_parent_id(child) is None


def test_move_into_self_is_400(h):
    f = h.create_folder("alice", "F")["id"]
    with pytest.raises(HTTPException) as ei:
        h.move_folder("alice", f, f)
    assert ei.value.status_code == 400


def test_move_into_descendant_is_400(h):
    a = h.create_folder("alice", "A")["id"]
    b = h.create_folder("alice", "B", parent_id=a)["id"]
    c = h.create_folder("alice", "C", parent_id=b)["id"]
    # Moving A under its own grandchild C would create a cycle.
    with pytest.raises(HTTPException) as ei:
        h.move_folder("alice", a, c)
    assert ei.value.status_code == 400
    assert h.folder_parent_id(a) is None   # unchanged


def test_move_subtree_respects_height(h):
    # A two-level subtree P -> Q, so its subtree height is 2.
    p = h.create_folder("alice", "P")["id"]
    h.create_folder("alice", "Q", parent_id=p)
    chain = _chain(h, "alice", DOCUMENT_FOLDER_MAX_DEPTH - 1)
    # Under the deepest host (depth MAX-1) the subtree's leaf would hit MAX+1.
    with pytest.raises(HTTPException) as ei:
        h.move_folder("alice", p, chain[-1])
    assert ei.value.status_code == 400
    assert h.folder_parent_id(p) is None   # rejected move left P at root
    # One level shallower (depth MAX-2) it fits exactly at MAX.
    res = h.move_folder("alice", p, chain[-2])
    assert res["parent_id"] == chain[-2]


def test_rename_and_move_in_one_patch(h):
    root = h.create_folder("alice", "Root")["id"]
    f = h.create_folder("alice", "Old")["id"]
    res = h.patch_folder("alice", f, {"name": "New", "parent_id": root})
    assert res["name"] == "New"
    assert res["parent_id"] == root
    assert res["depth"] == 2
