# General OpenAI-Compatible Inventory Fallback

Last updated: dev@2e2bb52 | 2026-08-16

## Scope

Canonical compatibility identity `generic_openai`; identity-only reader
`src/model_capability_readers/generic_openai.py`; shared envelope and identity
helpers in `src/model_capability_readers/base.py`.

This is not a universal OpenAI-compatible capability schema. Transport request
and response behavior remains in `src.llm_core` and provider adapters.

## Accepted Inventory Shape

- `{"data": [...]}`;
- `{"models": [...]}`.

Within an item, the reader recovers identity from `id`, `name`, or `model`.
Bare-list payloads and `key`/`slug`-only items are not supported. It preserves
the raw item on the in-memory record, while `to_dict()` includes it only when
the caller explicitly requests `include_raw=True`. Capability remains unknown.

## Disabled Capability Paths

The generic reader does not inspect capability-looking fields, including:

- `type`, `model_type`, `task`, and `pipeline_tag`;
- top-level or nested modality fields;
- capability booleans/maps/lists;
- `supported_parameters`;
- context, input, output, and model-length fields.

Names, descriptions, ownership, pricing, and serialized text also never
promote capability through this reader.

## Forward Compatibility

An explicitly configured but unknown provider ID is preserved when the generic
reader is selected. That allows endpoint-scoped stable IDs to keep working
while every family, modality, capability, limit, and control remains unknown.
Non-object entries are skipped; null or malformed roots return no records.

Provider-specific headers, request extensions, and reasoning channels must be
selected by explicit provider/endpoint adapters. They never leak through this
fallback.

Compatible tool-call syntax is likewise a runtime concern rather than catalog capability. Current parsers recover selected Hermes/Qwen JSON bodies nested inside `tool_call` wrappers and require the full Qwen bare end delimiter; GPT-OSS compatibility can alias names that collide with its built-in tools and reverse that alias before local dispatch. None of those repairs grants execution authority or proves generic tool support.

## Current Gaps

- Compatible providers differ on path prefixes, null handling, tools,
  streaming usage, and strict extra-field rejection.
- Bare-list and `key`/`slug`-only inventories need explicit normalization if a
  runtime consumer later requires them.
- Safe request shaping still requires explicit endpoint/provider
  configuration even when identity normalization succeeds.
