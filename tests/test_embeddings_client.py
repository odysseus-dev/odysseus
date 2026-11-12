import json

import httpx
import numpy as np
import pytest

import src.embeddings as embeddings
from src.embeddings import EmbeddingClient


class _FakeEmbeddingHttpClient:
    def __init__(self, handler):
        self.handler = handler
        self.headers = []

    def post(self, url, headers=None, json=None):
        self.headers.append(headers or {})
        request = httpx.Request("POST", url)
        status, body = self.handler(json)
        return httpx.Response(status, request=request, json=body)


def test_embedding_400_batch_retry_falls_back_to_single_inputs(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    calls = []

    def handler(payload):
        texts = payload["input"]
        calls.append(list(texts))
        if len(texts) > 1:
            return 400, {"error": "batch too large"}
        text = texts[0]
        return 200, {"data": [{"index": 0, "embedding": [float(len(text)), 1.0]}]}

    client = EmbeddingClient(url="http://embeddings.test/v1/embeddings", model="embed-test")
    client._client = _FakeEmbeddingHttpClient(handler)

    vecs = client.encode(["a", "bbbb"], normalize_embeddings=False)

    assert calls == [["a", "bbbb"], ["a"], ["bbbb"]]
    assert vecs.tolist() == [[1.0, 1.0], [4.0, 1.0]]


def test_embedding_400_single_input_retries_with_truncated_text(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MAX_CHARS", "200")
    lengths = []

    def handler(payload):
        text = payload["input"][0]
        lengths.append(len(text))
        if len(text) > 200:
            return 400, {"error": "context length exceeded"}
        return 200, {"data": [{"index": 0, "embedding": [2.0, 0.0]}]}

    client = EmbeddingClient(url="http://embeddings.test/v1/embeddings", model="embed-test")
    client._client = _FakeEmbeddingHttpClient(handler)

    vecs = client.encode(["x" * 250], normalize_embeddings=False)

    assert lengths == [250, 200]
    assert vecs.tolist() == [[2.0, 0.0]]


def test_embedding_non_400_errors_are_not_retried_or_swallowed():
    calls = 0

    def handler(payload):
        nonlocal calls
        calls += 1
        return 500, {"error": "server error"}

    client = EmbeddingClient(url="http://embeddings.test/v1/embeddings", model="embed-test")
    client._client = _FakeEmbeddingHttpClient(handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.encode(["a"], normalize_embeddings=False)

    assert calls == 1


def test_embedding_retry_path_preserves_api_key_header():
    seen_headers = []

    def handler(payload):
        return 200, {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

    client = EmbeddingClient(
        url="http://embeddings.test/v1/embeddings",
        model="embed-test",
        api_key="secret-key",
    )
    fake = _FakeEmbeddingHttpClient(handler)
    client._client = fake

    vecs = client.encode(["a"], normalize_embeddings=False)
    seen_headers.extend(fake.headers)

    assert vecs.tolist() == [[1.0, 0.0]]
    assert seen_headers == [{"Authorization": "Bearer secret-key"}]


@pytest.fixture
def embedding_response_client():
    clients = []
    def make(payload, **kwargs):
        client = embeddings.EmbeddingClient(
            url='http://user:password@embeddings.test/v1/embeddings?api_key=query-secret#fragment-secret',
            model='owned-model', **kwargs,
        )
        client._client.close()
        transport = httpx.MockTransport(lambda req: httpx.Response(200, content=json.dumps(payload)))
        client._client = httpx.Client(transport=transport)
        clients.append(client)
        return client
    yield make
    for client in clients:
        client._client.close()


@pytest.mark.parametrize('payload', [
    [], {}, {'data':None}, {'data':{}}, {'data':[]}, {'data':[None]},
    {'data':[{}]}, {'data':[{'embedding':None}]},
    {'data':[{'embedding':[]}]}, {'data':[{'embedding':'not-a-vector'}]},
    {'data':[{'embedding':['1']}]}, {'data':[{'embedding':[True]}]},
    {'data':[{'embedding':[float('nan')]}]}, {'data':[{'embedding':[float('inf')]}]},
    {'data':[{'embedding':[1e40]}]}, {'data':[{'embedding':[10**400]}]},
    {'data':[{'index':True,'embedding':[1]}]},
    {'data':[{'index':-1,'embedding':[1]}]},
    {'data':[{'index':1,'embedding':[1]}]},
    {'data':[{'index':'0','embedding':[1]}]},
])
def test_malformed_response_is_contextual_without_echoing_secrets(embedding_response_client, payload):
    client = embedding_response_client(payload)
    with pytest.raises(ValueError, match='Invalid embedding API response') as caught:
        client.encode(['one'])
    message = str(caught.value)
    assert 'http://embeddings.test/v1/embeddings' in message
    assert 'owned-model' in message
    for secret in ['password', 'query-secret', 'fragment-secret', 'not-a-vector']:
        assert secret not in message
    assert client._dim is None


def test_duplicate_index_does_not_silently_misalign_inputs(embedding_response_client):
    client = embedding_response_client({'data':[{'index':0,'embedding':[1]},{'index':0,'embedding':[2]}]})
    with pytest.raises(ValueError, match='index'):
        client.encode(['one','two'])


@pytest.mark.parametrize('payload', [
    {'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[3,4]}]},
    {'data':[{'embedding':[3,4]},{'embedding':[0,2]}]},
])
def test_valid_indexed_and_positional_vectors_keep_order_and_normalization(embedding_response_client, payload):
    client = embedding_response_client(payload)
    result = client.encode(['one','two'])
    np.testing.assert_allclose(result, [[.6,.8],[0,1]])
    assert result.dtype == np.float32
    assert client.get_sentence_embedding_dimension() == 2


def test_inconsistent_dimensions_across_batches_are_rejected(embedding_response_client, monkeypatch):
    monkeypatch.setenv('EMBEDDING_BATCH_SIZE','1')
    client = embedding_response_client({'data':[{'embedding':[1,2]}]})
    count = 0
    def reply(req):
        nonlocal count
        count += 1
        return httpx.Response(200, json={'data':[{'embedding':[1]*count}]})
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(reply))
    with pytest.raises(ValueError, match='Invalid embedding API response.*dimensions'):
        client.encode(['one','two'])
    assert count == 2
    assert client._dim is None


def test_dimension_cannot_change_after_a_successful_probe(embedding_response_client):
    client = embedding_response_client({'data':[{'embedding':[1,2]}]})
    assert client.get_sentence_embedding_dimension() == 2
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={'data':[{'embedding':[1]}]})
    ))
    with pytest.raises(ValueError, match='Invalid embedding API response.*dimensions'):
        client.encode(['one'])
    assert client._dim == 2


def test_invalid_json_is_contextual(embedding_response_client):
    client = embedding_response_client({})
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'private-response-invalid-json')))
    with pytest.raises(ValueError, match='expected JSON') as caught:
        client.encode(['one'])
    assert 'private-response' not in str(caught.value)
