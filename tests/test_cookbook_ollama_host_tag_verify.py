"""Regression for PR #4535 follow-up — in-container `ollama serve` must verify
the selected model against the HOST Ollama daemon before reporting success.

Odysseus running inside Docker cannot import a HuggingFace GGUF or `ollama pull`
a missing tag; it can only proxy to the host daemon. The earlier fix rejected on
slash presence, which both (a) rejected valid namespaced Ollama tags like
`library/qwen3:8b` and (b) let an unverified bare tag through and pinned it as an
available model the host could not serve (model-not-found at chat time).

`_ollama_tag_served` is the pure matcher the route uses to decide whether the
host daemon already serves `req.repo_id`. It mirrors Ollama's implicit `:latest`
and case-insensitive tag semantics.

The route probes the host daemon's NATIVE `/api/tags` via
`model_routes._probe_ollama_tags` — NOT the chat-filtered `/v1/models` path —
so presence verification sees every served tag, including embedding models whose
names (e.g. `nomic-embed-text`) the chat filter would drop. That unfiltered
probe is covered by `test_probe_ollama_tags_unfiltered`.
"""

import pytest

# The module imports fastapi/sqlalchemy at top level; skip cleanly where the
# serving stack isn't installed (pure-JS/test-tooling environments) so this file
# never blocks collection.
cb = pytest.importorskip("routes.cookbook_routes")
_ollama_tag_served = cb._ollama_tag_served


def test_probe_ollama_tags_unfiltered(monkeypatch):
    """`_probe_ollama_tags` must return EVERY served tag (no chat filter), hit
    the native `/api/tags`, and strip an accidental `/v1` suffix from the base."""
    mr = pytest.importorskip("routes.model_routes")

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [
                {"name": "qwen3:latest"},
                {"name": "nomic-embed-text:latest"},  # embedding: chat filter would drop it
            ]}

    def _fake_get(url, **kwargs):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(mr.httpx, "get", _fake_get)
    served = mr._probe_ollama_tags("http://host.docker.internal:11434/v1", timeout=5)
    assert captured["url"] == "http://host.docker.internal:11434/api/tags"
    assert served == ["qwen3:latest", "nomic-embed-text:latest"]


def test_probe_ollama_tags_returns_empty_on_error(monkeypatch):
    """Unreachable host → `[]`, so the route reports 'could not reach' guidance."""
    mr = pytest.importorskip("routes.model_routes")

    def _boom(url, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(mr.httpx, "get", _boom)
    assert mr._probe_ollama_tags("http://host.docker.internal:11434") == []


@pytest.mark.parametrize("repo_id, served", [
    ("qwen3", ["qwen3:latest", "llama3.2:8b"]),          # implicit :latest match
    ("qwen3:latest", ["qwen3:latest"]),                  # exact
    ("qwen3:8b", ["qwen3:8b", "qwen3:latest"]),          # specific tag present
    ("library/qwen3:8b", ["library/qwen3:8b"]),          # namespaced tag (was wrongly rejected)
    ("Qwen3", ["qwen3:latest"]),                         # case-insensitive
    ("llama3.2", ["llama3.2"]),                          # both implicit :latest
    ("nomic-embed-text", ["nomic-embed-text:latest"]),   # embedding tag (non-chat name)
])
def test_served_tags_match(repo_id, served):
    assert _ollama_tag_served(repo_id, served) is True


@pytest.mark.parametrize("repo_id, served", [
    ("qwen3:8b", ["qwen3:latest"]),                      # only :latest installed, not :8b
    ("mistral", ["qwen3:latest"]),                       # different model
    ("TheBloke/Foo-GGUF", ["qwen3:latest"]),             # HF-GGUF repo, never imported
    ("qwen3", []),                                        # host unreachable / nothing served
    ("qwen3", None),                                      # defensive: None list
])
def test_unserved_tags_rejected(repo_id, served):
    assert _ollama_tag_served(repo_id, served) is False
