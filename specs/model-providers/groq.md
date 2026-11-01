# Groq Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `groq`; OpenAI-compatible cloud transport; detection and
request behavior in `src/llm_core.py`.

## Shape

Model discovery falls back to the general `data[].id` identity shape. Richer
fields require a Groq-native mapped shape even when the payload happens to
supply modalities, supported parameters, or limits. Groq transport may accept OpenAI-style tools and streaming extensions,
but support remains per model and account.

Runtime currently exempts Groq/OpenRouter from some parameter stripping paths;
that is transport compatibility, not a provider-wide model capability claim.

## Fallback And Current Gaps

Exact `*.groq.com` preserves Groq identity. Do not infer Llama/Gemma model
capabilities from IDs. There is no canonical rich Groq model-card reader or
freshness policy yet.
