"""Library folder scoping: folder_id (direct), recursive (subtree), unfiled, global.

The visible list AND the language facet share the folder scope; the per-folder
sidebar facet stays independent (stable tree). folder_id + unfiled together is
400; an unknown / cross-owner folder_id is 404.
"""
import pytest
from fastapi import HTTPException

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def _titles(res):
    return {d["title"] for d in res["documents"]}


def _tree(h):
    root = h.create_folder("alice", "Root")["id"]
    child = h.create_folder("alice", "Child", parent_id=root)["id"]
    grand = h.create_folder("alice", "Grand", parent_id=child)["id"]
    h.seed_doc("alice", folder_id=root, title="r")
    h.seed_doc("alice", folder_id=child, title="c")
    h.seed_doc("alice", folder_id=grand, title="g")
    h.seed_doc("alice", folder_id=None, title="loose")
    return root, child, grand


def test_folder_id_is_direct_children_only(h):
    root, _child, _grand = _tree(h)
    res = h.library("alice", folder_id=root)
    assert _titles(res) == {"r"}          # NOT c or g (those are deeper)
    assert res["total"] == 1


def test_recursive_includes_whole_subtree(h):
    root, _child, _grand = _tree(h)
    res = h.library("alice", folder_id=root, recursive=True)
    assert _titles(res) == {"r", "c", "g"}
    assert res["total"] == 3


def test_recursive_from_midtree(h):
    _root, child, _grand = _tree(h)
    res = h.library("alice", folder_id=child, recursive=True)
    assert _titles(res) == {"c", "g"}


def test_unfiled_only(h):
    _tree(h)
    res = h.library("alice", unfiled=True)
    assert _titles(res) == {"loose"}


def test_global_returns_everything(h):
    _tree(h)
    res = h.library("alice")
    assert _titles(res) == {"r", "c", "g", "loose"}


def test_unfiled_and_folder_id_together_is_400(h):
    root, _child, _grand = _tree(h)
    with pytest.raises(HTTPException) as ei:
        h.library("alice", folder_id=root, unfiled=True)
    assert ei.value.status_code == 400


def test_language_facet_follows_folder_scope(h):
    root = h.create_folder("alice", "Root")["id"]
    child = h.create_folder("alice", "Child", parent_id=root)["id"]
    h.seed_doc("alice", folder_id=root, language="python", title="r")
    h.seed_doc("alice", folder_id=child, language="markdown", title="c")

    # Direct scope of root sees only its own python doc in the language facet.
    direct = h.library("alice", folder_id=root)
    assert direct["languages"] == {"python": 1}
    # Recursive scope sees both.
    rec = h.library("alice", folder_id=root, recursive=True)
    assert rec["languages"] == {"python": 1, "markdown": 1}


def test_sidebar_facet_stays_independent_of_scope(h):
    root = h.create_folder("alice", "Root")["id"]
    child = h.create_folder("alice", "Child", parent_id=root)["id"]
    h.seed_doc("alice", folder_id=root)
    h.seed_doc("alice", folder_id=child)
    h.seed_doc("alice", folder_id=None)

    res = h.library("alice", folder_id=root)   # scoped view
    facet = {f["id"]: f for f in res["folders"]}
    # Per-folder sidebar counts + unfiled reflect the FULL library, not the scope.
    assert facet[root]["count"] == 1
    assert facet[child]["count"] == 1
    assert res["unfiled_count"] == 1
    # And the sidebar carries depth/parent_id for the tree.
    assert facet[root]["depth"] == 1 and facet[root]["parent_id"] is None
    assert facet[child]["depth"] == 2 and facet[child]["parent_id"] == root


def test_unknown_folder_id_is_404(h):
    with pytest.raises(HTTPException) as ei:
        h.library("alice", folder_id="ghost")
    assert ei.value.status_code == 404
