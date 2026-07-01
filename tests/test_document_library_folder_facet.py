"""GET /api/documents/library folder facet + folder filter.

The library response must carry `folders` (ALL of the owner's folders, empties
included, with correct per-folder counts), `unfiled_count`, and folder_id /
folder_name on each document. `?folder_id=<id>` narrows the list to that folder,
`?unfiled=true` to the unfiled docs. The per-folder sidebar counts are
independent of the active search/language/folder filters.
"""
import pytest

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def _facet(res):
    return {f["id"]: f for f in res["folders"]}


def test_response_lists_all_folders_with_counts(h):
    a = h.create_folder("alice", "Alpha")["id"]
    b = h.create_folder("alice", "Beta")["id"]
    empty = h.create_folder("alice", "Empty")["id"]
    h.seed_doc("alice", folder_id=a)
    h.seed_doc("alice", folder_id=a)
    h.seed_doc("alice", folder_id=b)
    h.seed_doc("alice", folder_id=None)
    h.seed_doc("alice", folder_id=None)

    res = h.library("alice")
    facet = _facet(res)
    assert facet[a]["count"] == 2
    assert facet[b]["count"] == 1
    assert facet[empty]["count"] == 0  # empty folder still present
    assert res["unfiled_count"] == 2
    assert {f["name"] for f in res["folders"]} == {"Alpha", "Beta", "Empty"}


def test_documents_carry_folder_id_and_name(h):
    a = h.create_folder("alice", "Alpha")["id"]
    h.seed_doc("alice", folder_id=a, title="filed")
    h.seed_doc("alice", folder_id=None, title="loose")
    docs = {d["title"]: d for d in h.library("alice")["documents"]}
    assert docs["filed"]["folder_id"] == a
    assert docs["filed"]["folder_name"] == "Alpha"
    assert docs["loose"]["folder_id"] is None
    assert docs["loose"]["folder_name"] is None


def test_filter_by_folder_id(h):
    a = h.create_folder("alice", "Alpha")["id"]
    b = h.create_folder("alice", "Beta")["id"]
    h.seed_doc("alice", folder_id=a, title="in-a")
    h.seed_doc("alice", folder_id=b, title="in-b")
    h.seed_doc("alice", folder_id=None, title="loose")

    res = h.library("alice", folder_id=a)
    assert {d["title"] for d in res["documents"]} == {"in-a"}
    assert res["total"] == 1


def test_filter_none_returns_only_unfiled(h):
    a = h.create_folder("alice", "Alpha")["id"]
    h.seed_doc("alice", folder_id=a, title="in-a")
    h.seed_doc("alice", folder_id=None, title="loose1")
    h.seed_doc("alice", folder_id=None, title="loose2")

    res = h.library("alice", unfiled=True)
    assert {d["title"] for d in res["documents"]} == {"loose1", "loose2"}
    assert res["total"] == 2


def test_facet_counts_are_independent_of_active_filters(h):
    a = h.create_folder("alice", "Alpha")["id"]
    h.seed_doc("alice", folder_id=a, title="alpha-doc", language="python")
    h.seed_doc("alice", folder_id=a, title="other-doc", language="markdown")
    h.seed_doc("alice", folder_id=None, title="loose", language="python")

    # Even with a language filter + folder filter applied, the facet still
    # reflects the full per-folder totals (like the language facet does).
    res = h.library("alice", folder_id=a, language="python", search="alpha")
    facet = _facet(res)
    assert facet[a]["count"] == 2
    assert res["unfiled_count"] == 1
    # The document LIST, however, honors the filters.
    assert {d["title"] for d in res["documents"]} == {"alpha-doc"}


def test_facet_is_owner_scoped(h):
    a = h.create_folder("alice", "Alpha")["id"]
    h.create_folder("bob", "BobFolder")
    h.seed_doc("alice", folder_id=a)
    h.seed_doc("bob", folder_id=None)

    res = h.library("alice")
    assert {f["name"] for f in res["folders"]} == {"Alpha"}
    assert res["unfiled_count"] == 0  # alice has no unfiled docs; bob's don't count
