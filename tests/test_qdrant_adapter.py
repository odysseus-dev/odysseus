import sys
import types
from unittest.mock import MagicMock

import pytest

_qdrant = MagicMock()
_qdrant.models.PointStruct = types.SimpleNamespace
sys.modules["qdrant_client"] = _qdrant
sys.modules["qdrant_client.models"] = _qdrant.models

from src.qdrant_store import QdrantCollection


class MockQdrantClient:
    def __init__(self):
        self._store: dict[str, list] = {}

    def collection_exists(self, name):
        return name in self._store

    def create_collection(self, collection_name, **_):
        self._store.setdefault(collection_name, [])

    def upsert(self, collection_name, points):
        store = self._store.setdefault(collection_name, [])
        by_id = {p.id: p for p in store}
        for pt in points:
            by_id[pt.id] = types.SimpleNamespace(
                id=pt.id, payload=pt.payload, score=1.0
            )
        self._store[collection_name] = list(by_id.values())

    def retrieve(self, collection_name, ids, **_):
        want = set(ids)
        return [p for p in self._store.get(collection_name, []) if p.id in want]

    def delete(self, collection_name, points_selector):
        drop = set(points_selector)
        self._store[collection_name] = [
            p for p in self._store.get(collection_name, []) if p.id not in drop
        ]

    def scroll(self, collection_name, **_):
        return list(self._store.get(collection_name, [])), None

    def count(self, collection_name, **_):
        return types.SimpleNamespace(count=len(self._store.get(collection_name, [])))

    def query_points(self, collection_name, query, limit=10, **_):
        return types.SimpleNamespace(
            points=self._store.get(collection_name, [])[:limit]
        )


@pytest.fixture()
def col():
    return QdrantCollection(MockQdrantClient(), "test")


def test_add_get_by_ids(col):
    col.add(
        ids=["a", "b"],
        embeddings=[[0.1, 0.2], [0.3, 0.4]],
        documents=["da", "db"],
        metadatas=[{"k": 1}, {"k": 2}],
    )
    r = col.get(ids=["a", "b"])
    assert set(r) == {"ids", "documents", "metadatas"}
    assert sorted(r["ids"]) == ["a", "b"]


def test_get_empty_ids(col):
    assert col.get(ids=[]) == {"ids": [], "documents": [], "metadatas": []}


def test_upsert_overwrites(col):
    col.add(ids=["a"], embeddings=[[0.1, 0.2]], documents=["orig"], metadatas=[{}])
    col.upsert(ids=["a"], embeddings=[[0.9, 0.9]], documents=["new"], metadatas=[{}])
    assert col.get(ids=["a"])["documents"] == ["new"]


def test_delete(col):
    col.add(
        ids=["a", "b"],
        embeddings=[[0.1] * 2] * 2,
        documents=["x", "y"],
        metadatas=[{}, {}],
    )
    col.delete(ids=["a"])
    assert col.get(ids=["a"])["ids"] == []


def test_count(col):
    assert col.count() == 0
    col.add(
        ids=["a", "b"],
        embeddings=[[0.1] * 2] * 2,
        documents=["x", "y"],
        metadatas=[{}, {}],
    )
    assert col.count() == 2


def test_query_chroma_shape(col):
    col.add(
        ids=["a"], embeddings=[[0.1, 0.2]], documents=["hello"], metadatas=[{"t": 1}]
    )
    r = col.query(query_embeddings=[[0.1, 0.2]], n_results=5)
    assert set(r) == {"ids", "documents", "metadatas", "distances"}
    assert r["ids"] == [["a"]]
    assert r["documents"] == [["hello"]]
    assert len(r["distances"][0]) == 1


def test_query_one_list_per_embedding(col):
    col.add(ids=["x"], embeddings=[[1.0, 0.0]], documents=["d"], metadatas=[{}])
    r = col.query(query_embeddings=[[1.0, 0.0], [0.0, 1.0]])
    assert len(r["ids"]) == 2


def test_metadata_no_internal_keys(col):
    col.add(
        ids=["m"],
        embeddings=[[0.0, 1.0]],
        documents=["doc"],
        metadatas=[{"owner": "alice"}],
    )
    meta = col.get(ids=["m"])["metadatas"][0]
    assert meta == {"owner": "alice"}


def test_get_all_via_scroll(col):
    col.add(
        ids=["p", "q"],
        embeddings=[[0.1] * 2] * 2,
        documents=["dp", "dq"],
        metadatas=[{}, {}],
    )
    assert sorted(col.get()["ids"]) == ["p", "q"]
