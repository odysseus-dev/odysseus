# Canonical Provider And Model Capability Layer

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers the implementation introduced on current `dev` in:

- canonical model values and query helpers in `src/model_capabilities.py`;
- record, identity, and provider-detection helpers in
  `src/model_capability_readers/base.py`;
- reader dispatch in `src/model_capability_readers/__init__.py`;
- concrete readers for generic OpenAI-compatible, OpenAI, OpenRouter, Google,
  Ollama, LM Studio, and llama.cpp payloads;
- regression coverage in `tests/test_model_capabilities.py` and
  `tests/test_model_capability_readers.py`.

The layer normalizes already-fetched JSON-compatible values. It performs no
network I/O, does not shape provider requests, does not persist its output, and
does not authorize model or tool use. No production caller currently consumes
the canonical records outside this package; runtime integration remains later
work.

There is no `src/provider_capability_schemas.py`, capability-specific
diagnostics module, or runtime model-quirk registry on current `dev`.

## Layer Boundaries

- `src.model_capabilities` defines normalized families, tasks, modalities,
  capabilities, evidence sources/confidence, assertion states, deterministic
  controls, probe results, reasoning-control tokens, and display-surface
  queries.
- `ModelCapability` owns family, primary task, input/output modalities,
  capability tokens, limits, source, and confidence.
- `CapabilityAssertion` records claimed, verified, unsupported, or unknown
  status for one capability. Missing evidence is not an unsupported claim.
- `DeterministicControl` records support evidence for controls such as
  temperature, top-p, seed, tool choice, or prompt caching. A supported
  request control is not itself a model capability.
- `CapabilityProbeResult` is an in-memory evidence shape that converts pass,
  fail, or partial probe state into an assertion. No current runtime probe
  stores or merges these objects.
- `CapabilityQuery` and `display_surfaces_for()` map a normalized capability
  into candidate surfaces such as chat, vision chat, image generation,
  embeddings, or reranking. They are not wired into current pickers.
- Reader `ModelCapabilityRecord` binds a vendor/model identity to the nested
  capability object, assertions, deterministic controls, and optional raw
  provider evidence.

Provider transport support and per-model support are separate facts. Request
and response adapters remain in `src.llm_core` and related provider modules.
Model-specific observations remain in [model-quirks.md](model-quirks.md).

## Current Serialized Shapes

`ModelCapability.to_dict()` emits the nested capability shape:

```json
{
  "family": "chat",
  "primary_task": "chat.completions",
  "modalities": {
    "input": ["text", "image"],
    "output": ["text"]
  },
  "capabilities": ["tool_call", "vision"],
  "limits": {"context_tokens": 131072},
  "source": "provider_reader",
  "confidence": "provider_reported"
}
```

`ModelCapabilityRecord.to_dict()` wraps that value with `vendor`, `model_id`,
`stable_model_id`, `display_name`, `capability_assertions`, and
`deterministic_controls`. It does not currently emit a schema version or the
flat `provider`/`model`/`features`/`controls` shape. Raw provider fields are
included only when the caller passes `include_raw=True`.

Endpoint configuration can explicitly map `model_type=llm` to chat and
`model_type=image` to image generation. Missing or unrecognized endpoint types
stay unknown rather than silently becoming chat-capable in this schema layer.

## Identity And Reader Dispatch

`records_from_payload()` selects a reader from an explicit `vendor`, or from
`detect_vendor(base_url, endpoint_kind)` when no vendor is supplied.

Current detection order and behavior are:

1. a recognized explicit endpoint kind;
2. label-bounded hostname checks for OpenRouter, OpenAI, Anthropic, Google APIs, and Ollama Cloud;
3. common local ports: `11434` for Ollama, `1234` for LM Studio, `8000` for vLLM, and `30000` for SGLang;
4. generic OpenAI-compatible for any other parsed host, otherwise unknown.

These are normalization hints, not authorization. Hostname checks accept an exact domain or its dot-delimited subdomains after lowercasing and removing a trailing dot, so names such as `notopenai.com` do not match `openai.com`; local-port mappings remain intentionally covered by tests. Callers must not treat any result as proof of endpoint trust.

Implemented reader modules are `generic_openai`, `openai`, `openrouter`,
`google`, `llamacpp`, `ollama`, and `lmstudio`. Anthropic, Hugging Face,
SGLang, and vLLM have placeholder vendor IDs but currently dispatch through the
generic identity-only reader. Other explicitly supplied vendor strings are
also preserved while using that generic reader.

Stable model identity is scoped in this order:

- explicit endpoint ID;
- a short hash of normalized base URL when an endpoint ID is absent;
- `global` when neither endpoint identity is supplied.

## Generic Identity-Only Contract

The generic reader accepts mapping payloads containing `data[]` or `models[]`.
Each item must itself be a mapping and provide `id`, `name`, or `model`.
Bare-list payloads and `key`/`slug`-only items are not accepted by the current
implementation.

The reader deliberately returns unknown family, modalities, capabilities, and
controls. It preserves the raw item on the in-memory record but does not parse
type/task fields, descriptions, ownership, supported-parameter lists,
capability-looking booleans, or token limits.

## Provider-Native Readers

- OpenAI keeps the official Models API identity-only.
- OpenRouter maps explicit architecture modalities, supported parameters,
  limits, voices, and default parameters into family/capability/control state.
- Google maps the native Models resource. Embedding-only methods map to the
  embedding family; content-generation methods do not prove modality or chat
  family. Explicit thinking, limits, sampling fields, caching, and batch
  methods are retained without parsing product names.
- Ollama treats `/api/tags` as identity-only and maps selected-model
  `/api/show` capability tokens. Context can come from structured fields or a
  parsed `num_ctx` line in the serialized `parameters` value.
- LM Studio maps native v1 `models[]` and v0-style `data[]` fields. A plain
  OpenAI-compatible list without native type/capability fields stays unknown.
- llama.cpp can merge `/v1/models`, `/props`, and `/slots` evidence for one
  served model. It records tool/streaming claims, explicit unsupported
  vision/audio assertions, controls, and runtime/training/size limits.

Readers tolerate non-object entries and unknown fields where their helpers
permit it. They do not infer authoritative capability from model IDs or display
names.

## Evidence Semantics

The canonical vocabulary includes admin override, endpoint configuration,
provider reader, Cookbook/Hugging Face, maintained registries, heuristic,
probe, and unknown sources. It also defines explicit, provider-reported,
registry, heuristic, and unknown confidence values.

Those tokens make evidence representable; current `dev` does not implement a
global precedence, merge, expiry, or conflict-resolution engine. Assertions
generated by readers are usually `claimed`; a `CapabilityProbeResult` maps pass
to verified, fail to unsupported, and partial to claimed at the scope carried
by that object.

## Tests

Focused tests pin:

- endpoint-kind, host, and common-port vendor detection;
- endpoint/base-URL-scoped stable IDs;
- unknown behavior for generic and official OpenAI lists;
- canonical normalization and display-surface matching;
- assertion, deterministic-control, and probe-result shapes;
- OpenRouter, Google, Ollama, LM Studio, and llama.cpp mappings;
- negative cases that avoid name-based media/capability inference.

## Current Gaps

- Canonical records are not yet used by runtime discovery, endpoint resolution, model context, request shaping, or frontend pickers.
- Reader output is not persisted, refreshed, merged, or expired.
- Provider detection still uses common-port hints; consumers must not promote normalization hints into trust decisions.
- Only seven concrete readers exist; placeholder and other providers use the
  identity-only generic reader.
- Generic fallback does not accept bare-list or `key`/`slug`-only payloads.
- There is no capability-specific diagnostic/logging path.
- Runtime request builders still contain model-name heuristics outside this
  canonical layer.
