"""Provider seam for the OpenAI-Codex (ChatGPT-subscription) provider.

A small registry of `ProviderSpec`s + a pure `Transport` for the codex Responses
backend, plus an `EndpointRef`-based credential seam (`providers.auth`) that
resolves OAuth tokens per request — so a subscription token can refresh without
re-freezing headers at session-create time.

Transports are PURE — no I/O, no global state: they shape requests
(`target_url` / `build_payload` / `build_headers`), parse responses
(`parse_response`), and normalize streaming (`stream_decoder`). `llm_core` stays
the execution engine (httpx pool, cache, dead-host cooldown, retry, fallback) and
calls into the transport for codex endpoints; the transport never calls back into
`llm_core` (no import cycle).

This seam is intentionally codex-only: OpenAI-compatible and Anthropic endpoints
continue to be served by `llm_core`'s existing provider dispatch. Folding those
into the registry too is a separate refactor.
"""
