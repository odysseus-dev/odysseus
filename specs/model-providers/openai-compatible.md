# General OpenAI-Compatible Inventory Fallback

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical compatibility identity `generic_openai`; inventory-only reader
`src/model_capability_readers/generic_openai.py`; explicit fallback shapes in
`src/provider_capability_schemas.py`.

This is not a universal OpenAI-compatible capability schema. Transport request
and response behavior remains in `src.llm_core` and provider adapters.

## Accepted Inventory Envelopes

- `fallback.models.data.v1`: `{"data": [...]}`;
- `fallback.models.envelope.v1`: `{"models": [...]}`;
- `fallback.models.list.v1`: a bare list.

Within an item, fallback may recover identity from `id`, `name`, `model`,
`key`, or `slug`. It preserves the raw item only for explicit diagnostic use.
The canonical capability remains unknown.

## Disabled Capability Paths

The fallback deliberately ignores all capability-looking fields, including:

- `type`, `model_type`, `task`, and `pipeline_tag`;
- top-level or nested modality fields;
- capability booleans/maps/lists;
- `supported_parameters`;
- context, input, output, and model-length fields.

Those field names have different meanings across providers and versions. They
become evidence only inside a provider-native reader with a discriminating,
tested payload shape. Names, descriptions, ownership, pricing, serialized
text, and ports also never promote capability.

## Forward Compatibility

An explicitly configured but unknown provider ID is preserved and paired with
one of the fallback shape IDs. That allows inventory and endpoint-scoped
stable IDs to keep working while every family, modality, feature, limit, and
control remains unknown. Null, malformed, and mixed envelopes fail soft.

Provider-specific headers, request extensions, and reasoning channels must be
selected by explicit provider/endpoint adapters. They never leak through this
fallback.

## Current Gaps

- Compatible providers differ on path prefixes, null handling, tools,
  streaming usage, and strict extra-field rejection.
- Safe request shaping still requires explicit endpoint/provider
  configuration even when inventory fallback succeeds.
