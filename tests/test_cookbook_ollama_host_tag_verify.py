"""Regression for PR #4535 follow-up — in-container `ollama serve` must verify
the selected model against the HOST Ollama daemon before reporting success.

Odysseus running inside Docker cannot import a HuggingFace GGUF or `ollama pull`
a missing tag; it can only proxy to the host daemon. The earlier fix rejected on
slash presence, which both (a) rejected valid namespaced Ollama tags like
`library/qwen3:8b` and (b) let an unverified bare tag through and pinned it as an
available model the host could not serve (model-not-found at chat time).

`_ollama_tag_served` is the pure matcher the route uses to decide whether the
host daemon already serves `req.repo_id`. It mirrors Ollama's implicit `:latest`
and case-insensitive tag semantics. Tested directly here; the full route gate
(probe host /api/tags, 400 on miss) exercises this via CI integration.
"""

import pytest

# The module imports fastapi/sqlalchemy at top level; skip cleanly where the
# serving stack isn't installed (pure-JS/test-tooling environments) so this file
# never blocks collection.
cb = pytest.importorskip("routes.cookbook_routes")
_ollama_tag_served = cb._ollama_tag_served


@pytest.mark.parametrize("repo_id, served", [
    ("qwen3", ["qwen3:latest", "llama3.2:8b"]),          # implicit :latest match
    ("qwen3:latest", ["qwen3:latest"]),                  # exact
    ("qwen3:8b", ["qwen3:8b", "qwen3:latest"]),          # specific tag present
    ("library/qwen3:8b", ["library/qwen3:8b"]),          # namespaced tag (was wrongly rejected)
    ("Qwen3", ["qwen3:latest"]),                         # case-insensitive
    ("llama3.2", ["llama3.2"]),                          # both implicit :latest
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
