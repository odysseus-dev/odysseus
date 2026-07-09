"""Regression tests for EmbeddingClient response parsing and cleanup."""
from unittest.mock import MagicMock

import pytest

from src.embeddings import EmbeddingClient


def test_post_embeddings_raises_on_missing_embedding_field(monkeypatch):
    client = EmbeddingClient(url="http://localhost:9999/v1/embeddings", model="test")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "object": "embedding"}]}

    client._client = MagicMock()
    client._client.post.return_value = _Resp()

    with pytest.raises(ValueError, match="missing 'embedding' field"):
        client._post_embeddings(["hello"])


def test_post_embeddings_accepts_valid_payload(monkeypatch):
    client = EmbeddingClient(url="http://localhost:9999/v1/embeddings", model="test")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}

    client._client = MagicMock()
    client._client.post.return_value = _Resp()

    assert client._post_embeddings(["hello"]) == [[0.1, 0.2]]


def test_close_shuts_down_httpx_client():
    client = EmbeddingClient(url="http://localhost:9999/v1/embeddings", model="test")
    mock_http = MagicMock()
    client._client = mock_http
    client.close()
    mock_http.close.assert_called_once()