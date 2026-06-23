"""Issue #4590 — EmbeddingClient must fail clearly on a malformed response and
must not leak its httpx.Client.

Two defects:
  * `encode()` did `emb["embedding"]`, so a response object missing that field
    raised a bare `KeyError` with no hint of which endpoint/model misbehaved.
  * the `httpx.Client` opened in `__init__` had no close()/context-manager, and
    `get_embedding_client()` dropped the probe client on the floor when the
    endpoint was down — leaking its pooled connection for the whole process.

These tests use a fake httpx client, so no network or embedding server is
needed.
"""

from unittest.mock import MagicMock

import pytest

from src.embeddings import EmbeddingClient
import src.embeddings as embeddings


def _resp(payload):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=payload)
    return r


def test_encode_raises_clear_error_on_missing_embedding_field():
    client = EmbeddingClient(url="http://embed.test/v1/embeddings", model="all-minilm")
    client._client = MagicMock()
    client._client.post.return_value = _resp({"data": [{"index": 0}]})  # no 'embedding'

    with pytest.raises(ValueError) as excinfo:
        client.encode(["hello"])

    msg = str(excinfo.value)
    assert "embedding" in msg
    assert "http://embed.test/v1/embeddings" in msg
    assert "all-minilm" in msg


def test_encode_valid_response_still_returns_vectors():
    client = EmbeddingClient(url="http://embed.test/v1/embeddings", model="all-minilm")
    client._client = MagicMock()
    client._client.post.return_value = _resp(
        {"data": [{"embedding": [3.0, 4.0], "index": 0}]}
    )

    out = client.encode(["hello"], normalize_embeddings=False)

    assert out.shape == (1, 2)
    assert out.tolist() == [[3.0, 4.0]]


def test_close_closes_underlying_client():
    client = EmbeddingClient(url="http://embed.test", model="m")
    client._client = MagicMock()

    client.close()

    client._client.close.assert_called_once()


def test_context_manager_closes_on_exit():
    client = EmbeddingClient(url="http://embed.test", model="m")
    fake = MagicMock()
    with client as c:
        c._client = fake
    fake.close.assert_called_once()


def test_factory_closes_probe_client_when_endpoint_down(monkeypatch):
    embeddings.reset_http_embed_state()
    created = []

    class _FakeProbe:
        def __init__(self, *a, **k):
            self.url = "http://embed.test"
            self.model = "m"
            self.closed = False
            created.append(self)

        def get_sentence_embedding_dimension(self):
            raise RuntimeError("connection refused")

        def close(self):
            self.closed = True

    def _no_fastembed(*a, **k):
        raise ImportError("fastembed not installed")

    monkeypatch.setattr(embeddings, "EmbeddingClient", _FakeProbe)
    monkeypatch.setattr(embeddings, "FastEmbedClient", _no_fastembed)
    monkeypatch.setattr(embeddings, "_load_persisted_endpoint", lambda: {})

    result = embeddings.get_embedding_client()

    assert result is None  # both HTTP and FastEmbed unavailable
    assert created and created[0].closed is True  # probe client was closed, not leaked
