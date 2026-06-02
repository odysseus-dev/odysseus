"""``list_model_ids`` Ollama fallback must match on hostname, not substring.

When the primary model-list fetch fails, ``list_model_ids`` retries against the
native Ollama ``/api/tags`` endpoint. The gate for that retry was
``"ollama" in base_chat_url.lower()`` — a *substring* test that fires for any
URL merely containing the text "ollama" (a look-alike host such as
``ollama-gateway.example.com`` or even ``?model=ollama-7b`` in the query
string). That is exactly the substring-vs-hostname mismatch #768/#815 removed
from ``_detect_provider``; the fallback here was missed by that sweep.

These tests import the real ``list_model_ids`` and drive the fallback by making
the primary fetch fail. They FAIL on the substring gate (the look-alike host
and query-string cases wrongly hit ``/api/tags``) and pass once the gate uses
``_host_match``. The local ``:11434`` port convention is preserved.
"""
import pytest

from src import llm_core


def _make_fake_get(calls):
    class _Resp:
        def __init__(self, ok, data):
            self._ok = ok
            self._data = data

        def raise_for_status(self):
            if not self._ok:
                raise Exception("HTTP error")

        def json(self):
            return self._data

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/api/tags"):
            # A non-Ollama host that happens to answer /api/tags would feed
            # these bogus ids straight into the model list.
            return _Resp(True, {"models": [{"name": "bogus-model"}]})
        # Primary /models (and /v1/models) fetch always fails here, forcing the
        # fallback to decide whether to try the native Ollama endpoint.
        return _Resp(False, {})

    return fake_get


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_core.httpx, "get", _make_fake_get(calls))
    return calls


def _fallback_hit(calls):
    return any(u.endswith("/api/tags") for u in calls)


class TestFallbackFiresForRealOllama:
    def test_local_ollama_openai_compat_path(self, captured):
        # Ollama exposes an OpenAI-compatible /v1 path; the :11434 port is the
        # convention that identifies it when the host is generic (localhost).
        llm_core.list_model_ids("http://localhost:11434/v1/chat/completions")
        assert _fallback_hit(captured)

    def test_ollama_cloud(self, captured):
        llm_core.list_model_ids("https://ollama.com/v1/chat/completions")
        assert _fallback_hit(captured)

    def test_ollama_cloud_subdomain(self, captured):
        llm_core.list_model_ids("https://api.ollama.com/v1/chat/completions")
        assert _fallback_hit(captured)


class TestFallbackRejectsSubstringFalsePositives:
    def test_lookalike_host_does_not_fall_back(self, captured):
        out = llm_core.list_model_ids(
            "https://ollama-gateway.example.com/v1/chat/completions"
        )
        assert not _fallback_hit(captured)
        assert out == []

    def test_ollama_in_query_string_does_not_fall_back(self, captured):
        out = llm_core.list_model_ids(
            "https://api.example.com/v1/chat/completions?model=ollama-7b"
        )
        assert not _fallback_hit(captured)
        assert out == []

    def test_plain_openai_host_unaffected(self, captured):
        out = llm_core.list_model_ids("https://api.openai.com/v1/chat/completions")
        assert not _fallback_hit(captured)
        assert out == []
