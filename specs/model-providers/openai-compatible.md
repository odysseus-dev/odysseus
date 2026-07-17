# General OpenAI-Compatible Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `generic_openai`; structural fallback reader
`src/model_capability_readers/generic_openai.py`; shared Chat/Responses field
paths in `src/provider_capability_schemas.py`.

## Catalog Envelopes

Accepted identity envelopes are:

- standard `{"data": [{"id": ...}]}`;
- compatible `{"models": [{"id"|"key"|"name"|"model": ...}]}`;
- bare `[{"id": ...}]` lists.

Identity alone is unknown. The reader maps only exact structured fields:

- `type`, `model_type`, `task`, or `pipeline_tag` from a maintained value map;
- top-level or `architecture` input/output modalities and modality arrows;
- true capability booleans and recognized `supported_parameters`;
- exact numeric context/input/output fields.

Descriptions, names, ownership, pricing, arbitrary future booleans, serialized
text, and port numbers are ignored for capability. Unknown keys stay in `raw`,
so a new provider shape retains identity and evidence until an intentional
mapping lands.

## Request And Response Shape

OpenAI Chat commonly uses `messages`, `tools[].function`, tool calls under
`choices[].message|delta`, and text under `content`. Compatible reasoning
channels observed by Odysseus include `reasoning_content`, `reasoning`, and
`thinking`; these are accepted response paths, not proof every model reasons.
Responses uses typed `input`/`output` items and different stream events.

Provider extensions must be gated by provider/endpoint identity. llama.cpp
cache fields, Kimi headers, Mistral structured blocks, and Cerebras restrictions
must never leak across the general path.

## Forward Compatibility

The general reader should preserve useful identity and explicit common fields
when a provider adds a compatible shape. It must fail soft for null, malformed,
or mixed entries. Forward compatibility means unknown-but-usable identity, not
guessing UI surfaces or request parameters.

## Current Gaps

- Compatible providers differ on path prefixes, null handling, tools,
  streaming usage, and strict extra-field rejection.
- Provider identity and endpoint-specific probes are still required for safe
  request shaping.
