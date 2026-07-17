# Canonical Provider And Model Capability Layer

Last updated: dev@28d27ee | 2026-07-17

## Scope

This spec covers:

- canonical model values in `src/model_capabilities.py`;
- provider identity and model-catalog detection in
  `src/provider_capability_schemas.py`;
- provider-native model-payload readers in `src/model_capability_readers/`;
- regression coverage in `tests/test_model_capabilities.py`,
  `tests/test_model_capability_readers.py`,
  `tests/test_provider_capability_schemas.py`, and
  `tests/test_model_capability_diagnostics.py`;
- future consumers in model discovery, endpoint resolution, model context,
  request routing, pickers, and capability probes.

The layer normalizes already-fetched evidence. It performs no provider network
I/O, does not shape provider requests, and does not authorize tool execution.

## Layer Boundaries

- `ProviderCapabilitySchema` owns canonical provider identity, aliases, exact
  host suffixes, and known native catalog shapes.
- `ProviderCatalogShape` owns only catalog recognition: shape ID, provider,
  envelope, identity paths, required discriminator paths/types/values,
  detection priority, and whether the shape is fallback inventory.
- `ProviderResolution` reports provider, provider source, detected catalog
  shape, and fallback status.
- Provider request/response paths remain in `src.llm_core` and its adapters.
  They are not duplicated in the catalog detector.
- `ModelCapabilityRecord` keeps the reader's internal capability/assertion and
  control objects, endpoint-scoped stable identity, resolution evidence, and
  optional raw provider record.
- Model-specific behavior observations remain in
  [model-quirks.md](model-quirks.md). There is no runtime quirk registry in this
  layer until a real structured consumer exists.

Provider transport support and per-model support are different facts. A
provider may expose several APIs while individual model cards remain unknown.

## Lean Canonical Record

`ModelCapabilityRecord.to_dict()` emits canonical shape version 1:

```json
{
  "schema_version": 1,
  "provider": "openrouter",
  "model": "provider/model",
  "stable_id": "openrouter|global|provider/model",
  "family": "chat",
  "task": "chat.completions",
  "modalities": {
    "input": ["text", "image"],
    "output": ["text"]
  },
  "features": ["tool_call", "vision"],
  "limits": {
    "context_tokens": 131072
  },
  "controls": ["temperature", "top_p"],
  "evidence": {
    "source": "provider_reader",
    "confidence": "provider_reported",
    "provider_source": "explicit",
    "shape": "openrouter.models.rich.v1",
    "fallback": false
  }
}
```

The serialized record deliberately has one name for each concept. It does not
repeat `capability`, assertions, and controls in parallel nested structures.
Display names and raw provider fields are reader evidence, not canonical
identity. `raw` is included only when a caller explicitly requests it.

`family`, `task`, modalities, features, limits, and controls remain empty or
unknown when the provider did not report them through an intentionally mapped
native field. Missing evidence is not an unsupported claim.

## Evidence Rules

Evidence must remain scoped to provider, endpoint, stable model identity, and
observation source. Safe sources, from strongest local intent to weakest, are:

1. explicit admin override or endpoint configuration at that endpoint;
2. a bounded endpoint/model capability probe;
3. explicit native per-model provider fields;
4. a scoped maintained registry;
5. heuristic evidence, only where a consumer explicitly accepts it;
6. unknown.

Never use display names, descriptions, ownership labels, pricing text,
provider marketing, serialized prompt/Modelfile text, or a default port as
authoritative per-model capability.

## Provider Resolution

Provider identity is resolved from:

1. explicit provider;
2. explicit endpoint kind;
3. exact known host or subdomain;
4. one unambiguous discriminating native payload shape;
5. unknown.

Explicit identity wins over payload inference because compatible proxies may
rewrite catalog bodies. A previously unseen explicit provider ID is preserved
in normalized form and uses the inventory fallback until a native reader is
added. Host matching rejects lookalikes. Ports such as 11434, 1234, 8000, and
30000 never identify a provider.

Catalog shape detection is separate from provider identity. A known provider
with an unrecognized but list-shaped response keeps its provider identity and
is marked with an explicit fallback shape.

## Explicit Fallback Contract

The only general shapes are:

- `fallback.models.data.v1` for `data[]`;
- `fallback.models.envelope.v1` for `models[]`;
- `fallback.models.list.v1` for a bare list.

Fallback capability promotion is disabled. The inventory reader may recover
identity from `id`, `name`, `model`, `key`, or `slug`, preserve the raw record,
and return an unknown capability. It must ignore capability-looking fields
such as `type`, `task`, `pipeline_tag`, modalities, `capabilities`,
`supported_parameters`, and token limits.

This is the forward-compatible behavior: a new provider or payload revision
can still list stable endpoint-scoped model identities, but it cannot silently
opt those models into UI surfaces or request parameters. Null, malformed, or
mixed envelopes fail soft.

## Provider-Native Reader Contract

Native readers:

- accept decoded JSON-compatible values and never make HTTP calls;
- tolerate nulls, non-object entries, and unknown fields;
- interpret only provider-owned fields with tested shapes and value types;
- preserve endpoint-scoped stable identity;
- keep identity-only cards unknown;
- do not inherit provider-wide capability across all of its models.

Ollama illustrates the split: `/api/tags` is inventory-only, while
`/api/show.capabilities` and structured `model_info.*.context_length` can
describe a selected model. Hugging Face `pipeline_tag` is interpreted only in
the Hugging Face reader; a similarly named field in a generic payload has no
canonical meaning.

## Diagnostics

Capability diagnostics use Odysseus's existing `LOG_LEVEL` environment
toggle; it does not add a capability-specific CLI argument. At
`LOG_LEVEL=DEBUG`, normalization emits one bounded summary with canonical
version, provider/source, catalog shape, fallback state, record count, and the
set of normalized families/features/controls. It never logs model IDs or raw
payload values.

## Tests

Tests pin native shape discrimination, exact host matching, provider/model
separation, unregistered explicit provider identity, malformed payloads,
identity-only fallback, native provider mappings, the exact canonical v1
serialization, and safe diagnostics. Dangerous-looking generic fields are
negative fixtures: they must not promote capability.

## Current Gaps

- The canonical readers are not yet the single source for runtime provider
  dispatch, model picker filtering, context lookup, or request shaping.
- Probe merge/persistence, override layering, evidence expiry, and conflict
  presentation remain later integration work.
- Some providers expose useful metadata only through detail or probe endpoints;
  list-only discovery must keep those fields unknown.
- Runtime request builders still contain model-name heuristics. This catalog
  does not reproduce them as a second matching system.
