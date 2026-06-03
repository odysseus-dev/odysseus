"""Regression guard for #1660 — removing one RAG directory must delete only that
directory's chunks, never wipe the whole shared collection.

Two compounding defects were fixed:
  1. PersonalDocsManager.remove_directory called rag_manager.rebuild_index(),
     which delete+recreates the entire shared "odysseus_rag" collection (all
     owners + the base index), then re-indexed only the remaining tracked dirs
     (ownerless, never personal_dir). Now it does a targeted per-directory delete.
  2. VectorRAG.remove_directory selected via where={"source": {"$contains": dir}},
     which no Chroma metadata operator supports as a path-prefix match (and a
     substring would over-delete siblings). Now it filters stored absolute
     `source` paths in Python with a path boundary (dir or dir + os.sep).

These tests are hermetic — no chromadb; VectorRAG is exercised against a fake
collection, PersonalDocsManager against a fake rag manager.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

import src.rag_vector as rag_vector
import src.personal_docs as personal_docs
import src.ai_interaction as ai


# --------------------------------------------------------------------------- #
# VectorRAG.remove_directory selection correctness (edit C)
# --------------------------------------------------------------------------- #


class _FakeCollection:
    def __init__(self, rows):
        self._ids = [r[0] for r in rows]
        self._metas = [r[1] for r in rows]

    def get(self, include=None):
        return {"ids": list(self._ids), "metadatas": list(self._metas)}

    def delete(self, ids=None):
        drop = set(ids or [])
        kept = [(i, m) for i, m in zip(self._ids, self._metas) if i not in drop]
        self._ids = [i for i, _ in kept]
        self._metas = [m for _, m in kept]


def _make_vectorrag(rows):
    rag = rag_vector.VectorRAG.__new__(rag_vector.VectorRAG)  # skip Chroma connect
    rag._collection = _FakeCollection(rows)
    rag._healthy = True
    return rag


def test_vectorrag_remove_is_path_bounded():
    rows = [
        ("a", {"source": "/a/docs/f1.md"}),
        ("b", {"source": "/a/docs/sub/f2.md"}),   # nested -> must be removed
        ("c", {"source": "/a/docs2/f3.md"}),       # sibling prefix -> must survive
        ("d", {"source": "/a/docs_personal/f4.md"}),  # sibling prefix -> must survive
        ("e", {"filename": "no-source.md"}),       # sourceless dict -> must not crash/survive
    ]
    rag = _make_vectorrag(rows)
    res = rag.remove_directory("/a/docs")
    assert res["success"] is True
    assert res["removed_count"] == 2
    remaining = set(rag._collection.get()["ids"])
    assert remaining == {"c", "d", "e"}, remaining


def test_vectorrag_remove_no_match_is_noop():
    rag = _make_vectorrag([("a", {"source": "/a/docs/f1.md"})])
    res = rag.remove_directory("/nowhere")
    assert res["success"] is True
    assert res["removed_count"] == 0
    assert set(rag._collection.get()["ids"]) == {"a"}


# --------------------------------------------------------------------------- #
# PersonalDocsManager.remove_directory must delete-targeted, not wipe (edit A)
# --------------------------------------------------------------------------- #


class _FakeRag:
    """Records calls and simulates a chunk store keyed by id -> metadata."""

    def __init__(self, store):
        self.store = store
        self.rebuild_called = False

    def rebuild_index(self):
        # The catastrophic op — mimic delete_collection wiping everything.
        self.rebuild_called = True
        self.store.clear()
        return True

    def index_personal_documents(self, directory, owner=None):
        return {"indexed_count": 0}  # old recovery path re-adds nothing here

    def remove_directory(self, directory):
        directory = os.path.abspath(directory)
        doomed = [
            i for i, m in self.store.items()
            if isinstance(m.get("source"), str)
            and (m["source"] == directory or m["source"].startswith(directory + os.sep))
        ]
        for i in doomed:
            del self.store[i]
        return {"success": True, "removed_count": len(doomed)}


def test_personal_docs_remove_is_targeted(tmp_path):
    personal = os.path.abspath(str(tmp_path / "personal"))
    target = os.path.abspath(str(tmp_path / "target"))
    other = os.path.abspath(str(tmp_path / "other"))
    store = {
        "p": {"source": os.path.join(personal, "note.md"), "owner": "alice"},
        "t": {"source": os.path.join(target, "doc.md"), "owner": "alice"},
        "o": {"source": os.path.join(other, "doc.md"), "owner": "bob"},
    }
    fake = _FakeRag(store)
    mgr = personal_docs.PersonalDocsManager(str(tmp_path), rag_manager=fake)
    mgr.indexed_directories = [target, other]  # personal_dir intentionally NOT tracked

    mgr.remove_directory(target)

    assert fake.rebuild_called is False, "must not wipe the whole collection"
    assert "t" not in store, "target directory's chunk should be removed"
    assert "p" in store, "base personal index must survive"
    assert "o" in store, "another owner's chunk must survive"


# --------------------------------------------------------------------------- #
# do_manage_rag remove path must not fire a whole-collection rebuild (edit B)
# --------------------------------------------------------------------------- #


async def test_do_manage_rag_remove_does_not_rebuild(monkeypatch):
    calls = {"rebuild": 0}

    class _Rag:
        def rebuild_index(self):
            calls["rebuild"] += 1

        def remove_directory(self, directory):
            pass

    class _PDocs:
        def remove_directory(self, directory):
            pass

    monkeypatch.setattr(ai, "_rag_manager", _Rag())
    monkeypatch.setattr(ai, "_personal_docs_manager", _PDocs())

    # Untracked path: the old code still fired an unconditional rebuild_index().
    result = await ai.do_manage_rag("remove_directory\n/abs/untracked/dir")

    assert calls["rebuild"] == 0, "remove must not rebuild (whole-collection wipe)"
    assert "error" not in result, result
