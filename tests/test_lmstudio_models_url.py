"""Regression coverage for LM Studio /v1 model-list endpoints (issue #25).

LM Studio's OpenAI-compatible surface exposes its model list at
``/v1/models`` (just like llama-server, vLLM, text-generation-webui). Two
distinct failure modes were reported by users:

1. Pasting ``http://localhost:1234`` (no ``/v1``) — ``build_models_url``
   used to return ``http://localhost:1234/models``, which LM Studio does
   not expose, so the user got a generic "No models found" error even
   though the server was running and reachable.
2. Pasting ``http://localhost:1234/v1`` (with ``/v1``) — the model list
   fetch was correct, but the error message gave the user no way to tell
   whether the URL was wrong, the server was down, or the server was
   reachable but had no model loaded.

This module pins both behaviors so future refactors don't regress them.
"""

import httpx

from src import endpoint_resolver, llm_core


def _neutralize_provider_detection(monkeypatch):
    """``_is_ollama_native_url`` matches any localhost host with an empty
    path, which would route ``http://localhost:1234`` (LM Studio) into the
    Ollama branch and probe ``/api/tags`` instead of ``/v1/models``. Force
    provider detection to "openai" so the URL builder takes the LM Studio
    path the user actually intends."""
    monkeypatch.setattr(llm_core, "_is_ollama_native_url", lambda url: False)


# ── build_models_url: handle LM Studio base shapes ────────────────────


def test_build_models_url_inserts_v1_for_bare_host_port(monkeypatch):
    """`http://localhost:1234` must probe `/v1/models` for LM Studio."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    _neutralize_provider_detection(monkeypatch)

    assert (
        endpoint_resolver.build_models_url("http://localhost:1234")
        == "http://localhost:1234/v1/models"
    )


def test_build_models_url_accepts_v1_base(monkeypatch):
    """`http://localhost:1234/v1` must probe `/v1/models` (no double v1)."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    _neutralize_provider_detection(monkeypatch)

    assert (
        endpoint_resolver.build_models_url("http://localhost:1234/v1")
        == "http://localhost:1234/v1/models"
    )


def test_build_models_url_idempotent_for_explicit_models(monkeypatch):
    """`/v1/models` must probe `/v1/models` (normalize_base strips it)."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    _neutralize_provider_detection(monkeypatch)

    assert (
        endpoint_resolver.build_models_url("http://localhost:1234/v1/models")
        == "http://localhost:1234/v1/models"
    )


def test_build_models_url_strips_chat_completions(monkeypatch):
    """`/v1/chat/completions` must collapse to `/v1/models` (parity with #3330)."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    _neutralize_provider_detection(monkeypatch)

    assert (
        endpoint_resolver.build_models_url("http://localhost:1234/v1/chat/completions")
        == "http://localhost:1234/v1/models"
    )


def test_build_models_url_preserves_explicit_non_v1_path(monkeypatch):
    """User-supplied non-empty paths (e.g. `/openai`) must not be overridden
    with `/v1`. We only insert `/v1` when the path is empty — that matches
    the documented contract: a custom path is the caller's intent."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    _neutralize_provider_detection(monkeypatch)

    assert (
        endpoint_resolver.build_models_url("http://proxy.example.com/openai")
        == "http://proxy.example.com/openai/models"
    )


# ── list_model_ids: parse LM Studio's response ─────────────────────────


def test_llm_core_list_model_ids_queries_v1_models_for_lmstudio(monkeypatch):
    """Issue #25: probing `http://localhost:1234/v1` must hit `/v1/models`."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    monkeypatch.setattr(llm_core, "_configured_cached_model_ids", lambda url, **kwargs: [])
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF"},
                    {"id": "qwen2.5-7b-instruct"},
                ],
            },
            request=request,
        )

    monkeypatch.setattr(llm_core.httpx, "get", fake_get)

    assert llm_core.list_model_ids("http://localhost:1234/v1", timeout=1) == [
        "lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF",
        "qwen2.5-7b-instruct",
    ]
    assert seen == ["http://localhost:1234/v1/models"]


def test_llm_core_list_model_ids_queries_v1_models_for_bare_lmstudio(monkeypatch):
    """Issue #25: probing `http://localhost:1234` (no /v1) must hit `/v1/models`."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    monkeypatch.setattr(llm_core, "_configured_cached_model_ids", lambda url, **kwargs: [])
    # Localhost with empty path would otherwise be misclassified as Ollama
    # (llm_core._is_ollama_native_url); neutralise that for the test.
    monkeypatch.setattr(llm_core, "_is_ollama_native_url", lambda url: False)
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"data": [{"id": "model-a"}]}, request=request)

    monkeypatch.setattr(llm_core.httpx, "get", fake_get)

    assert llm_core.list_model_ids("http://localhost:1234", timeout=1) == ["model-a"]
    assert seen == ["http://localhost:1234/v1/models"]


def test_llm_core_list_model_ids_handles_empty_lmstudio_list(monkeypatch):
    """LM Studio returns `{"object":"list","data":[]}` when no model is loaded.
    The helper must return `[]` cleanly so the caller can surface a clear
    error (issue #25: previously the empty case was indistinguishable from
    a connection failure)."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)
    monkeypatch.setattr(llm_core, "_configured_cached_model_ids", lambda url, **kwargs: [])

    def fake_get(url, headers=None, timeout=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"object": "list", "data": []}, request=request)

    monkeypatch.setattr(llm_core.httpx, "get", fake_get)

    assert llm_core.list_model_ids("http://localhost:1234/v1", timeout=1) == []
