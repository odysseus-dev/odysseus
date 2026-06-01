"""Provider registry — URL → ProviderSpec → Transport.

The minimal seam introduced alongside the OpenAI-Codex (ChatGPT-subscription)
provider. `detect_spec` recognizes every built-in provider so spec metadata
(id / label / auth_type / model-list mode) is available uniformly, but only the
**codex** transport is wired here — OpenAI-compatible and Anthropic endpoints
continue to be served by `llm_core`'s existing detection/dispatch. Folding those
transports into this registry is left as a follow-up refactor.
"""
from src.providers.codex_responses import CodexResponsesTransport
from src.providers.spec import BUILTIN_SPECS, OPENAI_SPEC, ProviderSpec

# Singleton transports — pure and stateless, safe to share. Only the codex
# transport lives here for now (see module docstring).
_TRANSPORTS = {
    "codex_responses": CodexResponsesTransport(),
}


def detect_spec(url: str) -> ProviderSpec:
    """Resolve the ProviderSpec for an endpoint URL (OpenAI is the default)."""
    for spec in BUILTIN_SPECS:
        if spec.url_matchers and spec.matches(url):
            return spec
    return OPENAI_SPEC


def get_transport(spec_or_id):
    """Resolve a Transport from a ProviderSpec or a transport id.

    Only the codex transport is wired in this seam; resolving any other transport
    raises — non-codex endpoints must route through `llm_core`'s legacy provider
    dispatch, not this registry.
    """
    tid = spec_or_id.transport if isinstance(spec_or_id, ProviderSpec) else spec_or_id
    try:
        return _TRANSPORTS[tid]
    except KeyError:
        raise KeyError(
            f"transport '{tid}' is not wired in the codex seam — non-codex "
            f"endpoints are served by llm_core's legacy provider dispatch"
        )


def is_codex_url(url: str) -> bool:
    """True for the codex (ChatGPT-subscription) backend — the one OAuth provider."""
    return detect_spec(url).id == "openai-codex"
