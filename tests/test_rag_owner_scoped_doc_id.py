"""Regression guard for #1662 — RAG doc ids must be owner-namespaced.

_generate_doc_id hashed the text only, and add_document / add_documents_batch
early-return on an existing id. So when two owners indexed byte-identical text,
the second owner's chunk collided with the first's id, was skipped, and never
appeared in the second owner's owner-scoped search. The id now includes the
owner; owner=None keeps the legacy content-only id, so existing rows and
shared/legacy chunks are unchanged and need no re-index.
"""
import hashlib
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from src.rag_vector import _generate_doc_id, VectorRAG


def _legacy_id(text):
    return f"doc_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def test_owner_none_keeps_legacy_id():
    # Back-compat: existing rows / shared chunks keep their content-only id.
    assert _generate_doc_id("hello") == _legacy_id("hello")
    assert _generate_doc_id("hello", None) == _legacy_id("hello")


def test_distinct_owners_get_distinct_ids_for_same_text():
    a = _generate_doc_id("hello", "alice")
    b = _generate_doc_id("hello", "bob")
    shared = _generate_doc_id("hello", None)
    assert a != b
    assert a != shared and b != shared
    # Deterministic per (owner, text).
    assert a == _generate_doc_id("hello", "alice")


class _FakeCollection:
    def __init__(self):
        self.rows = {}  # id -> (text, metadata)

    def get(self, ids=None, include=None):
        if ids is not None:
            present = [i for i in ids if i in self.rows]
            return {"ids": present, "metadatas": [self.rows[i][1] for i in present]}
        all_ids = list(self.rows)
        return {"ids": all_ids, "metadatas": [self.rows[i][1] for i in all_ids]}

    def add(self, ids=None, embeddings=None, documents=None, metadatas=None):
        for i, doc, meta in zip(ids, documents, metadatas):
            self.rows[i] = (doc, meta)


def _make_rag():
    rag = VectorRAG.__new__(VectorRAG)  # skip Chroma connect
    rag._collection = _FakeCollection()
    rag._healthy = True
    rag._embed = lambda texts: [[0.0] for _ in texts]
    return rag


def test_two_owners_identical_text_both_indexed():
    rag = _make_rag()
    text = "shared boilerplate chunk"
    rag.add_documents_batch([(text, {"source": "/a/f.md", "owner": "alice"})])
    rag.add_documents_batch([(text, {"source": "/b/f.md", "owner": "bob"})])

    rows = rag._collection.rows
    assert len(rows) == 2, "both owners' identical chunk must be stored"
    owners = sorted(m["owner"] for (_, m) in rows.values())
    assert owners == ["alice", "bob"]  # was just ["alice"] before the fix


def test_same_owner_same_text_still_deduped():
    rag = _make_rag()
    text = "dup chunk"
    rag.add_documents_batch([(text, {"source": "/a/1.md", "owner": "alice"})])
    rag.add_documents_batch([(text, {"source": "/a/2.md", "owner": "alice"})])
    assert len(rag._collection.rows) == 1  # genuine per-owner dedup is preserved
