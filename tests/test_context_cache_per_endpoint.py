"""Regression for #2603 — model context-window cache must be keyed per endpoint.

`get_context_length()` cached by model id alone, so two different remote endpoints
serving the same model id (e.g. a capped proxy at 8k vs. the full provider at 200k)
collided: whichever resolved first won process-wide and the other was served the
wrong window. The fix keys the cache on (endpoint_url, model).
"""

import src.model_context as mc


def _setup(monkeypatch, windows):
    """windows: {endpoint_url: context_length}. Force the remote path."""
    monkeypatch.setattr(mc, "is_local_endpoint", lambda url: False)
    monkeypatch.setattr(mc, "_configured_endpoint_kind", lambda url: "api")
    monkeypatch.setattr(mc, "_query_context_length", lambda url, model: (windows[url], True))
    mc._context_cache.clear()


def test_same_model_two_remote_endpoints_get_their_own_window(monkeypatch):
    a, b = "https://proxy-a.example/v1", "https://provider-b.example/v1"
    _setup(monkeypatch, {a: 8000, b: 200000})

    assert mc.get_context_length(a, "shared-model") == 8000
    # Same model id, different endpoint: must NOT return endpoint A's cached 8000.
    assert mc.get_context_length(b, "shared-model") == 200000


def test_cache_hit_still_works_per_endpoint(monkeypatch):
    a, b = "https://proxy-a.example/v1", "https://provider-b.example/v1"
    _setup(monkeypatch, {a: 8000, b: 200000})
    mc.get_context_length(a, "shared-model")
    mc.get_context_length(b, "shared-model")

    # Both endpoints are now cached under their own key; flip the underlying
    # query to prove subsequent reads come from the per-endpoint cache, not a re-query.
    monkeypatch.setattr(mc, "_query_context_length", lambda url, model: (999, True))
    assert mc.get_context_length(a, "shared-model") == 8000
    assert mc.get_context_length(b, "shared-model") == 200000
