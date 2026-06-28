from src.rag_vector import VectorRAG

class _FakeCollection:
    def __init__(self):
        pass

    def query(self, query_embeddings, n_results, where, include):
        return {
            "ids": [["a", "b", "c"]],
            "documents": [["alice private doc", "bob private doc", "ownerless global doc"]],
            "metadatas": [[{"owner": "alice"}, {"owner": "bob"}, {}]],
            "distances": [[0.1, 0.2, 0.3]],
        }

class _FakeLane:
    def __init__(self, name, count_val=10):
        self.name = name
        self.collection_name = "fake_collection"
        self._count = count_val
        self.collection = _FakeCollection()

    def count(self):
        return self._count

    def encode(self, texts):
        return [[0.0] * 384]

def test_search_allows_ownerless_and_own_docs_but_excludes_other_owners():
    store = VectorRAG.__new__(VectorRAG)
    store._lanes = [_FakeLane("fastembed")]
    store._healthy = True
    
    # User is Alice: should see alice's doc and ownerless doc, but not bob's
    results = store.search("query", k=5, owner="alice")
    texts = [r["document"] for r in results]
    assert "alice private doc" in texts
    assert "ownerless global doc" in texts
    assert "bob private doc" not in texts

    # User is Bob: should see bob's doc and ownerless doc, but not alice's
    results = store.search("query", k=5, owner="bob")
    texts = [r["document"] for r in results]
    assert "bob private doc" in texts
    assert "ownerless global doc" in texts
    assert "alice private doc" not in texts

    # No owner (global search): should see all docs
    results = store.search("query", k=5, owner=None)
    texts = [r["document"] for r in results]
    assert "alice private doc" in texts
    assert "bob private doc" in texts
    assert "ownerless global doc" in texts
