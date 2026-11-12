import httpx
import pytest

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


@pytest.mark.parametrize('raise_inside', [False, True])
def test_http_pool_context_closes_on_success_and_exception(raise_inside):
    from contextlib import nullcontext
    client = EmbeddingClient(url='http://embeddings.test')
    pool = client._client
    try:
        with pytest.raises(RuntimeError) if raise_inside else nullcontext():
            with client as entered:
                assert entered is client
                assert not pool.is_closed
                if raise_inside:
                    raise RuntimeError('owned failure')
        assert pool.is_closed
        client.close()
        assert pool.is_closed
    finally:
        pool.close()


def test_discarded_embedding_client_releases_http_pool():
    import gc
    import weakref
    client = EmbeddingClient(url='http://embeddings.test')
    pool = client._client
    reference = weakref.ref(client)
    del client
    gc.collect()
    try:
        assert reference() is None
        assert pool.is_closed
    finally:
        pool.close()


@pytest.mark.parametrize('failure', ['malformed', 'http', 'connect'])
def test_failed_embedding_probe_closes_before_local_fallback(monkeypatch, failure):
    import src.embeddings as embeddings
    clients = []
    original = EmbeddingClient
    def reply(request):
        if failure == 'connect':
            raise httpx.ConnectError('owned unavailable endpoint', request=request)
        return httpx.Response(503 if failure == 'http' else 200, json={'data':[{}]})
    def make_client():
        client = original(url='http://embeddings.test')
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(reply))
        clients.append(client)
        return client
    class LocalClient:
        model = "owned-local-fixture"
        def get_sentence_embedding_dimension(self):
            assert clients[0]._client.is_closed
            return 2
    local = LocalClient()
    monkeypatch.setattr(embeddings, 'EmbeddingClient', make_client)
    monkeypatch.setattr(embeddings, 'FastEmbedClient', lambda: local)
    monkeypatch.setattr(embeddings, '_load_persisted_endpoint', lambda: {})
    monkeypatch.setattr(embeddings, '_http_embed_down', False)
    try:
        assert embeddings.get_embedding_client() is local
        assert embeddings._http_embed_down
        assert clients[0]._client.is_closed
    finally:
        for client in clients:
            client._client.close()


def test_successful_embedding_probe_keeps_returned_client_open(monkeypatch):
    import src.embeddings as embeddings
    client = EmbeddingClient(url='http://embeddings.test')
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200,json={'data':[{'embedding':[3,4]}]})
    ))
    monkeypatch.setattr(embeddings, 'EmbeddingClient', lambda: client)
    monkeypatch.setattr(embeddings, '_load_persisted_endpoint', lambda: {})
    monkeypatch.setattr(embeddings, '_http_embed_down', False)
    try:
        assert embeddings.get_embedding_client() is client
        assert not client._client.is_closed
        assert client.encode(['one']).shape == (1,2)
        assert not embeddings._http_embed_down
    finally:
        client._client.close()
