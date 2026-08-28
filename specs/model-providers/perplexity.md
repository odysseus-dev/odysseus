# Perplexity Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `perplexity`; OpenAI-compatible cloud endpoint recognized
by current UI/provider host maps and agent cloud-host safeguards (#3015).

## Shape

Use general identity-only inventory mapping. Perplexity products may perform
search, but `web_search` becomes a canonical model capability only when an
exact model card, maintained registry, or probe reports it. Provider identity
alone and product descriptions are insufficient.

## Fallback And Current Gaps

Exact `*.perplexity.ai` preserves provider identity. No rich per-model catalog
or search-control mapping is currently consumed.
