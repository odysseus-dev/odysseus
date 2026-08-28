# Cohere Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Documented provider identity `cohere`; native Chat v2 plus the OpenAI
Compatibility API. Current `dev` has no dedicated Cohere capability reader or
direct Cohere request adapter; compatible endpoints use the general runtime
path when explicitly configured.

## Catalog Shape

`GET /v1/models` returns a paginated `models[]` envelope. Each model can carry
`name`, `endpoints`, `default_endpoints`, `context_length`, `features`, and
`sampling_defaults`; the root can carry `next_page_token`.

These are candidate fields for a future dedicated reader:

- a single canonical family from `endpoints`: `chat`/`generate`, `embed`,
  `rerank`, or `classify`;
- `context_length` to the endpoint/model context limit;
- known sampling-default keys to deterministic controls.

Current canonical normalization does not map them. When the generic reader is
explicitly selected with vendor `cohere`, it preserves only item identity plus
the raw item; family, context, features, and sampling controls stay unknown.

## Request And Response Shape

Native `POST /v2/chat` uses `messages`, structured content blocks, tools,
`response_format`, sampling fields, and an optional structured `thinking`
object. Text lives in `message.content[type=text].text`; reasoning-capable
models use `message.content[type=thinking].thinking`. Streaming uses typed
events rather than one generic text delta.

The OpenAI compatibility base is `/compatibility/v1`. Its current chat subset
includes tools, structured output, sampling, and `reasoning_effort`, but model
support remains per-model. In the compatibility dialect only `none` and `high`
currently map to native thinking off/on; do not assume low/medium support.

## Fallback And Safety

No Cohere host or payload-shape detection exists in the canonical reader
registry. The caller must supply provider/endpoint configuration. Marketing
pages and provider-wide endpoint features do not grant every listed model
tools, vision, or reasoning.

## Evidence And Gaps

- Official List/Get Models resources define the catalog fields.
- Official Chat v2, Reasoning, and Compatibility API resources define the
  transport and thinking controls.
- Odysseus has no direct Cohere request adapter, canonical reader, or sanitized
  canonical fixtures yet; both normalization and runtime integration remain
  follow-up work.
