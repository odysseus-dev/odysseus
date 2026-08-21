"""Regression: a recreated ChromaDB container must not silently disable retrieval.

Lane collection handles are resolved once, against the process-wide cached HTTP
client in ``src.chroma_client`` and against the collection ids that existed at
that moment. Recreating the ChromaDB container (a compose edit, an image pull,
``docker compose down && up``) invalidates both, and nothing in the running
process notices.

``VectorRAG.search`` caught the resulting error and fell through to
``_keyword_search_fallback``, which talks to the same stale handles and returns
``[]``. Callers cannot tell that apart from "nothing matched", so RAG and
personal-document retrieval went quiet — no error, no empty state — for the
rest of the process lifetime. Recovery required restarting the app, which is
exactly what made issue #6132's volume fix look like it had not worked.

``search`` now rebuilds the client and lanes once per throttle window and
retries, so the next query recovers on its own.
"""
import pytest

from src import rag_vector
from src.rag_vector import VectorRAG


class _FakeCollection:
    """Collection handle used by the keyword fallback."""

    def __init__(self, docs=()):
        self._docs = list(docs)  # (id, text, metadata)

    def count(self):
        return len(self._docs)

    def get(self, include=None):
        return {
            "ids": [d[0] for d in self._docs],
            "documents": [d[1] for d in self._docs],
            "metadatas": [d[2] for d in self._docs],
        }


class _FakeLane:
    def __init__(self, collection):
        self.name = "fastembed"
        self.collection = collection


_HIT = {
    "ids": [["doc_1"]],
    "documents": [["the indexed answer"]],
    "metadatas": [[{"owner": "alice"}]],
    "distances": [[0.1]],
}


def _store(collection=None):
    store = VectorRAG.__new__(VectorRAG)
    store._healthy = True
    store._collection = collection if collection is not None else _FakeCollection()
    store._lanes = [_FakeLane(store._collection)]
    store._last_reconnect = 0.0
    return store


@pytest.fixture
def backend(monkeypatch):
    """Drive lane queries, the client cache, and re-initialization from one place."""

    state = {
        "service_up": True,      # is the ChromaDB service answering at all?
        "init_ok": True,         # can the lanes be rebuilt once it is?
        "handles_stale": True,   # do the cached collection handles still resolve?
        "resets": 0,
        "inits": 0,
    }

    def fake_lane_count(lanes):
        return 1

    def fake_query_lanes(lanes, query, **kwargs):
        if state["handles_stale"]:
            # What a recreated container actually produces: the collection id
            # the cached handle was built around no longer exists.
            raise RuntimeError("fastembed: Collection not found")
        return [(lanes[0], _HIT)]

    def fake_get_chroma_client():
        if not state["service_up"]:
            raise RuntimeError("ChromaDB is not reachable")
        return object()

    def fake_reset_client():
        state["resets"] += 1

    def fake_initialize_system(self):
        state["inits"] += 1
        if not state["service_up"] or not state["init_ok"]:
            # Mirrors the real handler: lanes could not be rebuilt.
            self._healthy = False
            return False
        # A successful re-init rebinds the lanes to the new container.
        state["handles_stale"] = False
        self._healthy = True
        return True

    monkeypatch.setattr(rag_vector, "lane_count", fake_lane_count)
    monkeypatch.setattr(rag_vector, "query_lanes", fake_query_lanes)
    monkeypatch.setattr(VectorRAG, "_initialize_system", fake_initialize_system)

    import src.chroma_client as cc

    monkeypatch.setattr(cc, "get_chroma_client", fake_get_chroma_client)
    monkeypatch.setattr(cc, "reset_client", fake_reset_client)
    return state


def test_recreated_container_does_not_silently_kill_search(backend):
    """The bug: stale handles against a live service returned [] forever."""
    backend["service_up"] = True
    backend["handles_stale"] = True

    results = _store().search("the indexed answer", k=3, owner="alice")

    assert [r["id"] for r in results] == ["doc_1"], (
        "search must rebuild the stale handles and return real hits, not an "
        "empty list that every caller reads as 'nothing matched'"
    )
    assert backend["resets"] == 1, "the stale cached client must be discarded"
    assert backend["inits"] == 1, "lanes must be rebound to the new container"


def test_keyword_fallback_still_serves_a_persistently_failing_query(backend, monkeypatch):
    """Existing behaviour: when retrying does not help, the fallback still answers."""
    backend["service_up"] = True

    def always_fails(lanes, query, **kwargs):
        raise RuntimeError("where clause rejected")

    monkeypatch.setattr(rag_vector, "query_lanes", always_fails)

    store = _store(
        _FakeCollection([("kw_1", "the indexed answer", {"owner": "alice"})])
    )
    results = store.search("indexed answer", k=3, owner="alice")

    assert [r["id"] for r in results] == ["kw_1"], (
        "a query the vector path cannot serve must still reach the keyword "
        "fallback exactly as before"
    )
    assert results[0]["search_type"] == "keyword_fallback"


def test_reconnect_is_throttled_while_the_backend_stays_down(backend):
    """A backend that stays down must not turn every query into a reconnect."""
    backend["service_up"] = False
    backend["handles_stale"] = True

    store = _store()
    assert store.search("first", k=3) == []
    assert store.search("second", k=3) == []

    assert backend["resets"] == 1, (
        "the second query inside the throttle window must reuse the earlier "
        "failed attempt rather than reconnecting again"
    )


def test_store_left_unhealthy_recovers_without_an_app_restart(backend):
    """A failed reconnect must not strand the store for the process lifetime.

    ``_initialize_system`` sets ``_healthy = False`` when the backend is down,
    and ``rag_singleton`` keeps returning this same cached instance, so a
    ``healthy`` guard that returned early would never let it come back.
    """
    # Service is reachable, but the lanes cannot be rebuilt yet (for example the
    # embedding backend is still starting), so _initialize_system fails.
    backend["service_up"] = True
    backend["init_ok"] = False

    store = _store()
    assert store.search("while degraded", k=3) == []
    assert backend["resets"] == 1
    assert store.healthy is False, "a failed re-init leaves the store unhealthy"

    # Everything recovers and the throttle window has passed. Nothing external
    # resets _healthy — search itself has to attempt recovery.
    backend["init_ok"] = True
    store._last_reconnect -= rag_vector.RECONNECT_THROTTLE_SECONDS + 1

    results = store.search("the indexed answer", k=3, owner="alice")
    assert [r["id"] for r in results] == ["doc_1"]
    assert backend["resets"] == 2
