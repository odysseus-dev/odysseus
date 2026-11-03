import sys
import threading
import types

import pytest

from src import embedding_lanes as lanes
from src import embeddings


@pytest.fixture
def loads(monkeypatch, tmp_path):
    """Replaces fastembed with a stub that records each model load."""
    calls = []
    module = types.ModuleType("fastembed")

    class TextEmbedding:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    module.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    monkeypatch.setattr(embeddings, "FASTEMBED_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(embeddings, "_shared_fastembed", None)
    monkeypatch.delenv("FASTEMBED_MODEL", raising=False)
    monkeypatch.delenv("FASTEMBED_THREADS", raising=False)
    return calls


def test_every_lane_shares_one_model_load(loads):
    clients = [lanes._build_fastembed_client() for _ in range(3)]

    assert len(loads) == 1
    assert clients[0] is clients[1] is clients[2]


def test_lanes_and_the_fallback_factory_share_the_load(loads, monkeypatch):
    monkeypatch.setattr(embeddings, "_http_embed_down", True)
    monkeypatch.setattr(embeddings, "_load_persisted_endpoint", lambda: {})

    assert lanes._build_fastembed_client() is embeddings.get_embedding_client()
    assert len(loads) == 1


def test_concurrent_first_use_loads_once(loads):
    barrier = threading.Barrier(8)
    clients = []

    def use():
        barrier.wait()
        clients.append(embeddings.get_shared_fastembed_client())

    threads = [threading.Thread(target=use) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(loads) == 1
    assert len({id(c) for c in clients}) == 1


def test_changing_the_model_replaces_the_client(loads, monkeypatch):
    first = embeddings.get_shared_fastembed_client()
    monkeypatch.setenv("FASTEMBED_MODEL", "other-model")
    second = embeddings.get_shared_fastembed_client()

    assert second is not first
    assert second.model == "other-model"
    assert len(loads) == 2


def test_a_failed_load_is_retried(loads, monkeypatch):
    monkeypatch.setenv("FASTEMBED_THREADS", "not-a-number")
    with pytest.raises(ValueError, match="FASTEMBED_THREADS"):
        embeddings.get_shared_fastembed_client()

    monkeypatch.delenv("FASTEMBED_THREADS")
    assert embeddings.get_shared_fastembed_client() is not None
    assert len(loads) == 1
