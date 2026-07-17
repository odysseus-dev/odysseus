# Cohere Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `cohere`; native Chat v2 plus the OpenAI Compatibility
API; catalog reader `src/model_capability_readers/cohere.py`. Current
Odysseus source already treats Cohere as a native tool-capable API host, while
unknown-host runtime dispatch still falls through the general compatible path.

## Catalog Shape

`GET /v1/models` returns a paginated `models[]` envelope. Each model can carry
`name`, `endpoints`, `default_endpoints`, `context_length`, `features`, and
`sampling_defaults`; the root can carry `next_page_token`.

Map only exact structured fields:

- a single canonical family from `endpoints`: `chat`/`generate`, `embed`,
  `rerank`, or `classify`;
- `context_length` to the endpoint/model context limit;
- known sampling-default keys to deterministic controls.

If one card spans incompatible canonical families, keep its family unknown.
Preserve `features` as raw evidence, but do not promote arbitrary feature text
without an explicit maintained mapping.

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

Exact `*.cohere.ai` or `*.cohere.com` host identity selects Cohere. The native
`models[]` shape requires endpoint metadata so an arbitrary general envelope
does not identify the provider. Marketing pages and provider-wide endpoint
features do not grant every listed model tools, vision, or reasoning.

## Evidence And Gaps

- Official List/Get Models resources define the catalog fields.
- Official Chat v2, Reasoning, and Compatibility API resources define the
  transport and thinking controls.
- Odysseus has no direct Cohere request adapter or sanitized fixtures beyond
  this canonical reader yet; runtime integration remains follow-up work.
