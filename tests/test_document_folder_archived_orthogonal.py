"""Archiving and folder-filing are orthogonal.

An archived doc keeps its folder_id; filing a doc never archives it. The library
folder facet honors the archived/active scope: the archived view counts only
archived docs (and vice versa), and `?folder_id=<id>` works within the archived
view.
"""
import pytest

from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def _facet(res):
    return {f["id"]: f for f in res["folders"]}


def test_archived_doc_with_folder_shows_in_archived_folder_view(h):
    fid = h.create_folder("alice", "Acme")["id"]
    h.seed_doc("alice", folder_id=fid, archived=True, title="arch")
    h.seed_doc("alice", folder_id=fid, archived=False, title="live")

    res = h.library("alice", archived=True, folder_id=fid)
    assert {d["title"] for d in res["documents"]} == {"arch"}
    assert res["total"] == 1


def test_facet_under_archived_counts_only_archived(h):
    fid = h.create_folder("alice", "Acme")["id"]
    h.seed_doc("alice", folder_id=fid, archived=True)
    h.seed_doc("alice", folder_id=fid, archived=True)
    h.seed_doc("alice", folder_id=fid, archived=False)
    h.seed_doc("alice", folder_id=None, archived=True)

    arch = h.library("alice", archived=True)
    assert _facet(arch)[fid]["count"] == 2
    assert arch["unfiled_count"] == 1

    live = h.library("alice", archived=False)
    assert _facet(live)[fid]["count"] == 1
    assert live["unfiled_count"] == 0


def test_filing_does_not_archive(h):
    fid = h.create_folder("alice", "Acme")["id"]
    doc = h.seed_doc("alice", folder_id=None, archived=False)
    res = h.patch_doc("alice", doc, {"folder_id": fid})
    assert res["folder_id"] == fid
    assert res["archived"] is False


def test_clearing_folder_does_not_change_archived_state(h):
    fid = h.create_folder("alice", "Acme")["id"]
    doc = h.seed_doc("alice", folder_id=fid, archived=True)
    res = h.patch_doc("alice", doc, {"folder_id": "__none__"})
    assert res["folder_id"] is None
    assert res["archived"] is True
