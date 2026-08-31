# LM Studio Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical provider ID `lmstudio`; native LM Studio v1 plus OpenAI Chat and
Responses compatibility; reader `src/model_capability_readers/lmstudio.py`.

## Catalog Shapes

Preferred shape is `GET /api/v1/models` with root `models[]`. Current fields
include `key`, `type` (`llm` or `embedding`), display/publisher data,
`architecture`, quantization/format/size, `max_context_length`,
`loaded_instances[].config.context_length`, and a capability object containing
`vision`, `trained_for_tool_use`, and reasoning options/defaults.

Compatibility shape `GET /api/v0/models` uses `data[]` with `id`, `type`
(`llm`, `vlm`, or embeddings), `arch`, `compatibility_type`, state, and
context metadata. It is an explicit older shape, not a loose fallback.
OpenAI `/v1/models` is identity-only when native endpoints are unavailable.

Loaded-instance context is the effective runtime context; maximum context is a
separate limit. Model type maps family, explicit capability booleans map
vision/tools/reasoning, and architecture is provider-reported model family.

## Request And Response Shape

Native v1 chat is `/api/v1/chat` and can expose stateful/MCP-oriented output;
LM Studio also supports OpenAI Chat and Responses compatibility. Keep dialect
selection explicit because tool/MCP features differ between native and
compatible paths.

## Fallback And Safety

Current reader detection identifies port 1234 as LM Studio. Prefer pathless
native `/api/v1/models` discovery where configured (#1122, #3615), then v0,
then general identity. The port mapping is a normalization hint, not endpoint
trust. An error object from an unsupported native route is not a model list.

## Current Gaps

- Runtime discovery does not yet persist native capability records.
- LM Studio API capabilities continue to evolve; each new native version needs
  an explicit shape fixture before promotion.
