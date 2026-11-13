"""Regression tests for RAG ownership isolation across VectorRAG, personal routes, and MCP."""

import asyncio
import os
import pytest

import src.rag_vector as rag_vector
import src.personal_docs as personal_docs
from src.embedding_lanes import EmbeddingLane, migrate_legacy_collection


class _FakeCollection:
    def __init__(self, rows=None):
        # rows: list of (id, text, metadata)
        self._rows = list(rows or [])

    def count(self):
        return len(self._rows)

    def get(self, ids=None, where=None, include=None):
        matched = self._rows
        if ids is not None:
            wanted = set(ids)
            matched = [r for r in matched if r[0] in wanted]
        if where and "source" in where:
            src = where["source"]
            matched = [r for r in matched if isinstance(r[2], dict) and r[2].get("source") == src]
        return {
            "ids": [r[0] for r in matched],
            "documents": [r[1] for r in matched],
            "metadatas": [r[2] for r in matched],
        }

    def add(self, ids, documents, metadatas, embeddings=None):
        for i, d, m in zip(ids, documents, metadatas):
            self._rows.append((i, d, m))

    def delete(self, ids=None):
        drop = set(ids or [])
        self._rows = [r for r in self._rows if r[0] not in drop]


def _make_rag(rows=None):
    col = _FakeCollection(rows)
    rag = rag_vector.VectorRAG.__new__(rag_vector.VectorRAG)
    rag._collection = col
    rag._lanes = [
        EmbeddingLane(
            name="primary",
            collection_name="odysseus_rag",
            model="test-model",
            url="http://localhost",
            dimension=384,
            fingerprint="fp",
            client=type("_E", (), {"encode": lambda self, t, **kw: [[0.0] * 384 for _ in t]})(),
            collection=col,
        )
    ]
    rag._healthy = True
    return rag


def test_vectorrag_rejects_sentinel_owners_on_write_search_and_delete():
    rag = _make_rag([
        ("a1", "alice secret", {"owner": "alice", "source": "/docs/a.md"}),
        ("b1", "bob secret", {"owner": "bob", "source": "/docs/b.md"}),
    ])

    assert rag.add_document("spoofed", {"owner": "internal-tool", "source": "/docs/s.md"}) is False
    assert rag.search("secret", owner="internal-tool") == []
    assert rag._keyword_search_fallback("secret", owner="internal-tool") == []
    assert rag.delete_by_source("/docs/a.md", owner="internal-tool") == 0
    assert rag.remove_directory("/docs", owner="internal-tool")["success"] is False

    # Alice and Bob's rows remain untouched
    assert set(rag._collection.get()["ids"]) == {"a1", "b1"}


def test_vectorrag_owner_scoped_remove_and_delete_preserve_other_owners():
    rag = _make_rag([
        ("a1", "alice file", {"owner": "alice", "source": os.path.join(os.path.abspath("/shared/dir"), "file.md")}),
        ("b1", "bob file", {"owner": "bob", "source": os.path.join(os.path.abspath("/shared/dir"), "file.md")}),
        ("u1", "ownerless file", {"source": os.path.join(os.path.abspath("/shared/dir"), "file.md")}),
    ])

    removed = rag.delete_by_source(os.path.join(os.path.abspath("/shared/dir"), "file.md"), owner="alice")
    assert removed == 1
    assert set(rag._collection.get()["ids"]) == {"b1", "u1"}

    res = rag.remove_directory("/shared/dir", owner="bob")
    assert res["success"] is True
    assert res["removed_count"] == 1
    assert set(rag._collection.get()["ids"]) == {"u1"}


def test_personal_docs_owner_scoped_remove_keeps_shared_dir_tracking(tmp_path):
    shared_dir = os.path.abspath(str(tmp_path / "shared"))
    rag = _make_rag([
        ("a1", "alice note", {"owner": "alice", "source": os.path.join(shared_dir, "a.md")}),
        ("b1", "bob note", {"owner": "bob", "source": os.path.join(shared_dir, "b.md")}),
    ])
    mgr = personal_docs.PersonalDocsManager(str(tmp_path), rag_manager=rag)
    mgr.indexed_directories = [shared_dir]

    res = mgr.remove_directory(shared_dir, owner="alice")
    assert res["success"] is True
    assert res["removed_count"] == 1
    assert shared_dir in mgr.indexed_directories
    assert set(rag._collection.get()["ids"]) == {"b1"}


def test_migrate_legacy_collection_require_owner_skips_ownerless_rows(monkeypatch):
    legacy = _FakeCollection([
        ("owned", "owned text", {"owner": "alice"}),
        ("ownerless", "ownerless text", {}),
        ("sentinel", "sentinel text", {"owner": "internal-tool"}),
    ])
    target = _FakeCollection()

    class _FakeClient:
        def get_collection(self, name):
            return legacy

    import src.chroma_client as chroma_client
    monkeypatch.setattr(chroma_client, "get_chroma_client", lambda: _FakeClient())

    lane = EmbeddingLane(
        name="primary",
        collection_name="odysseus_rag",
        model="test-model",
        url="http://localhost",
        dimension=384,
        fingerprint="fp",
        client=type("_E", (), {"encode": lambda self, t, **kw: [[0.0] * 384 for _ in t]})(),
        collection=target,
    )
    migrate_legacy_collection("odysseus_rag", [lane], require_owner=True)
    assert set(target.get()["ids"]) == {"owned"}


def test_mcp_rag_server_blocks_unauthenticated_cross_owner_actions(monkeypatch):
    pytest.importorskip("mcp")
    import mcp_servers.rag_server as rs

    monkeypatch.setattr(rs, "_ensure_init", lambda: None)

    out_list = asyncio.run(rs.call_tool("manage_rag", {"action": "list"}))
    assert "owner-scoped listing" in out_list[0].text

    out_add = asyncio.run(rs.call_tool("manage_rag", {"action": "add_directory", "directory": "/tmp"}))
    assert "owner-scoped indexing" in out_add[0].text

    out_rm = asyncio.run(rs.call_tool("manage_rag", {"action": "remove_directory", "directory": "/tmp"}))
    assert "owner-scoped deletion" in out_rm[0].text
