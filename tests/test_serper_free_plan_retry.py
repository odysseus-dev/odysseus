"""Serper's free plan answers 400 "Query pattern not allowed for free accounts" to
queries with operators (quotes, OR, site:). The provider must retry once as plain
words instead of failing the whole search."""

import pytest

from services.search import providers


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise providers.httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {}, raising=False)
    monkeypatch.setattr(providers, "_safesearch_for", lambda *_a, **_k: None, raising=False)
    monkeypatch.setenv("SERPER_API_KEY", "k")


def test_plain_query_strips_operators():
    q = '"Пилипцова" психолог OR РАС site:t.me -instagram (СДВГ)'
    assert providers._plain_query(q) == "Пилипцова психолог РАС СДВГ"


def test_serper_retries_plain_query_on_free_plan_400(monkeypatch):
    sent = []

    def _post(url, json=None, **kw):
        sent.append(json["q"])
        if len(sent) == 1:
            return _Resp(400, '{"message":"Query pattern not allowed for free accounts.","statusCode":400}')
        return _Resp(200, {"organic": [{"title": "T", "link": "https://ex.com", "snippet": "S"}]})

    monkeypatch.setattr(providers.httpx, "post", _post)
    results = providers.serper_search('"Пилипцова" OR психолог', 5)
    assert len(sent) == 2
    assert '"' not in sent[1] and " OR " not in sent[1]
    assert results and results[0]["url"] == "https://ex.com"


def test_serper_other_400_is_not_retried(monkeypatch):
    sent = []

    def _post(url, json=None, **kw):
        sent.append(json["q"])
        return _Resp(400, '{"message":"Bad request"}')

    monkeypatch.setattr(providers.httpx, "post", _post)
    try:
        providers.serper_search('"x" OR y', 5)
    except Exception:
        pass
    assert len(sent) == 1
