# Canonical Provider And Model Capability Layer

Last updated: dev@28d27ee | 2026-07-17

## Scope

This spec covers:

- canonical model data in `src/model_capabilities.py`;
- provider/API/catalog data in `src/provider_capability_schemas.py`;
- provider model-payload readers in `src/model_capability_readers/`;
- exact model/provider exceptions in `src/model_behavior_quirks.py`;
- regression coverage in `tests/test_model_capabilities.py`,
  `tests/test_model_capability_readers.py`, and
  `tests/test_provider_capability_schemas.py`;
- future consumers in `routes/model_routes.py`, `src/endpoint_resolver.py`,
  `src/model_context.py`, `src/llm_core.py`, model pickers, and capability
  probes.

The layer normalizes already-fetched evidence. It performs no provider network
I/O and does not authorize tool execution.

## Layer Boundaries

Serving provider, API dialect, and model behavior are separate axes:

- `ProviderCapabilitySchema` describes provider identity, known API dialects,
  versioned catalog shapes, host suffixes, and fallback. It does not grant
  every model the provider's aggregate features.
- `ProviderApiShape` describes stable request/response field paths for a
  dialect such as OpenAI Chat, OpenAI Responses, Anthropic Messages, Google
  GenerateContent, or Ollama native.
- `ProviderCatalogShape` describes one exact catalog endpoint, envelope,
  identity fields, discriminating fields and their JSON value types,
  capability-bearing paths, version, and priority.
- `ModelCapabilityRecord` binds endpoint-scoped model identity to canonical
  model capability, assertions, controls, provider/model versions and family,
  resolution metadata, and raw provider evidence.
- `ModelBehaviorQuirk` narrows behavior for an exact structured selector. It
  may constrain request fields, history fields, reasoning controls, or response
  channels; it must not discover identity from arbitrary text.

Provider transport facts can be true even when a model record is unknown.
Conversely, a model registry can describe a model family while an endpoint
uses a different dialect. Consumers must reconcile both axes.

## Canonical Model Shape

`ModelCapability` contains:

- `family`: chat, embedding, image, video, audio, rerank, classification,
  moderation, or unknown;
- `primary_task`;
- explicit input/output modalities;
- normalized capabilities such as vision, files, reasoning, tool calls,
  structured output, generation/editing, transcription, or TTS;
- numeric or structured limits;
- source and confidence.

Claims and negative evidence live in `CapabilityAssertion`; a missing claim is
not an unsupported claim. `CapabilityProbeResult` stores endpoint/model-scoped
pass, fail, or partial evidence and converts it to an assertion. Deterministic
sampling/schema controls and `ReasoningControl` are separate from model
capabilities because accepting a request parameter is not itself a task
capability.

`ReasoningControl` stores the canonical mechanism, canonical on/off/auto
semantics, provider-native values, exact request path, exact response paths,
and evidence. This accommodates native booleans, structured objects, budgets,
template kwargs, message directives, and graded effort without flattening them
into one boolean.

## Evidence And Merge Rules

Evidence remains scoped to provider, endpoint, stable model ID, API dialect,
provider/model version when known, and observation time. Merge rules are:

1. explicit admin override or endpoint configuration applies only to that
   configured endpoint;
2. a fresh successful/failed capability probe is endpoint/model evidence;
3. explicit native provider catalog fields are provider-reported model facts;
4. structured serving-engine or model-registry metadata is registry evidence;
5. maintained provider documentation can describe transport or a scoped model
   quirk;
6. heuristics are low-confidence compatibility evidence;
7. otherwise remain unknown.

Higher-confidence evidence can supersede a weaker claim at the same scope, but
an endpoint failure must not globally mark a model family unsupported. Preserve
conflicting evidence for diagnosis; do not silently erase the losing source.
Never use display names, descriptions, ownership labels, pricing text, or a
provider's aggregate marketing page as authoritative per-model capability.

## Provider Resolution And Fallback

`resolve_provider()` uses this fixed ladder:

1. explicit provider;
2. explicit endpoint kind;
3. exact host or subdomain match;
4. one unambiguous discriminating native payload shape;
5. general structural `data`, `models`, or bare-list shape;
6. unknown.

Explicit identity wins over a conflicting payload because proxies can rewrite
catalog bodies. Host matching rejects lookalikes. Ports such as 11434, 1234,
8000, and 30000 are hints for discovery only, not provider identity.

The general structural reader accepts identity fields plus exact task/type,
modality arrays, capability booleans, supported-parameter arrays, and numeric
limit fields. Unknown keys stay in `raw`. Null/malformed envelopes return an
empty or identity-only result. Names and descriptions do not participate.

Provider-native latest shapes are preferred, followed by intentionally listed
legacy shapes and then the general reader. A new provider shape can therefore
retain identity and raw fields before Odysseus knows its new capabilities,
without silently opting models into UI or request behavior.

## Reader Contract

Readers:

- accept decoded JSON-compatible values and never make HTTP calls;
- tolerate nulls, non-object entries, and unknown fields;
- use exact provider fields and documented nested paths;
- preserve stable endpoint-scoped identity;
- keep identity-only records unknown;
- never parse serialized prompt/Modelfile text for capability truth;
- never infer capability from a model ID, display name, description, or port.

Ollama is the important compatibility example: `/api/tags` supplies identity
and family, while `/api/show` supplies explicit capability tokens and
`model_info.<architecture>.context_length`. The serialized `parameters` text
is not reparsed. LM Studio similarly prefers native v1 capability objects and
retains native v0 as an explicit compatibility shape.

## Tests

Tests pin:

- shape resolution order and host-lookalike rejection;
- native catalog signatures for current providers and local engines;
- general bare-list/null/future-field behavior;
- no model-name or default-port inference;
- identity-only provider behavior;
- rich Mistral, Cohere, Copilot, SGLang, OpenRouter, Google, LM Studio,
  Ollama, and llama.cpp mappings;
- source/confidence, controls, stable IDs, and model quirks;
- exact structured model/version selection rather than regex matching.

Fixture payloads should be minimal excerpts that exercise shape contracts, not
large copied provider responses. Live-provider tests require explicit opt-in
and sanitized credentials; deterministic unit fixtures remain the merge gate.

## Current Gaps

- The canonical registry/readers are not yet the single source for runtime
  provider dispatch, model picker filtering, model context, or request-builder
  quirks.
- Not every provider publishes per-model capability metadata. Those providers
  correctly remain identity-only until scoped registry/probe evidence exists.
- Structured provider/model version identity is not yet persisted on every
  endpoint, so version-gated quirks cannot safely replace all runtime name
  heuristics yet.
- Probe merge/persistence and evidence expiry remain later integration work.
