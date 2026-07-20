# Anthropic Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical placeholder vendor ID `anthropic`; Anthropic Messages runtime
adapter in `src/llm_core.py`. There is no dedicated Anthropic capability-reader
module; explicit/auto-detected Anthropic payloads use the generic identity-only
reader.

## Catalog Shape

`GET /v1/models` returns `data[]` model resources with `id`, `type: model`,
`display_name`, and `created_at`, plus pagination metadata. These fields prove
identity/availability only. Do not assume all listed Claude models share
vision, tools, reasoning, sampling, or context limits.

## Request And Response Shape

Native Messages uses a top-level `system`, alternating `messages`, content
blocks, `tools[].input_schema`, `tool_use` assistant blocks, and `tool_result`
user blocks. Text, thinking, signatures, server-tool blocks, and tool calls are
typed content rather than OpenAI roles/fields. Preserve block IDs/signatures
needed for continuation.

Sampling and thinking support can be version/model specific. The Opus 4.7+
sampling omission is a model-scoped runtime observation, not an Anthropic-wide
rule. Anthropic-compatible proxies are Anthropic dialect only when configured
or their exact payload/endpoint shape proves it (#3110).

## Fallback And Safety

Runtime provider detection uses label-bounded Anthropic host matching. The
canonical reader helper separately uses a plain `endswith("anthropic.com")`
hostname hint or explicit endpoint kind. A provider using Anthropic Messages
through another host must be explicit. Identity-only model cards remain
unknown.

## Current Gaps

- The public model list does not provide per-model canonical capability data.
- There is no dedicated Anthropic canonical reader; only `id`, `name`, or
  `model` identity survives generic normalization.
- Runtime model-version parsing needs structured identity before a later
  consumer can centralize sampling exceptions without another name matcher.
