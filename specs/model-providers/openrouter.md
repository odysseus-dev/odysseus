# OpenRouter Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `openrouter`; OpenAI-compatible chat dialect; rich reader
`src/model_capability_readers/openrouter.py`.

## Catalog Shape

`GET /api/v1/models` returns `data[]`. Canonical fields are:

- `id`, `canonical_slug`, and display `name`;
- `architecture.input_modalities`, `architecture.output_modalities`, and
  compatibility `architecture.modality`;
- `context_length` and `top_provider.max_completion_tokens`;
- `supported_parameters`, `default_parameters`, `supported_voices`, and
  `per_request_limits`.

Modalities determine family and vision/file/audio/image/video behavior.
Recognized supported parameters claim tools, JSON/structured output,
reasoning, and web search. Sampling/default parameters become controls, not
capabilities. Descriptions, pricing, author slugs, and tokenizer names do not.

## Provider Versus Routed Endpoint

OpenRouter normalizes requests while routing a model to one of several
underlying providers. The catalog model record is OpenRouter-scoped. Do not
copy a direct-provider quirk to OpenRouter unless its normalized API and exact
model/endpoint evidence require it. `top_provider` limits describe the current
route class, not a permanent global model limit.

## Fallback And Safety

Prefer the rich shape over the general `data[].id` shape. Future fields remain
raw. If modalities are absent, retain identity/known controls and keep family
unknown rather than parsing the model slug.

## Current Gaps

- Per-upstream endpoint differences can still invalidate an aggregate claim.
- Catalog values change frequently and need freshness/expiry when persisted.
