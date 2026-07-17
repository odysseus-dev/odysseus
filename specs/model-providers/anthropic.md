# Anthropic Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `anthropic`; Anthropic Messages dialect; identity reader
`src/model_capability_readers/anthropic.py`; runtime adapter in
`src/llm_core.py`.

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
sampling omission is an exact structured model quirk, not an Anthropic-wide
rule. Anthropic-compatible proxies are Anthropic dialect only when configured
or their exact payload/endpoint shape proves it (#3110).

## Fallback And Safety

Use exact `*.anthropic.com` host or explicit endpoint kind. A provider using
Anthropic Messages through another host must be explicit; host substring
matching is forbidden. Identity-only model cards remain unknown.

## Current Gaps

- The public model list does not provide per-model canonical capability data.
- Runtime model-version parsing must migrate to structured identity before the
  canonical quirk registry can own all sampling exceptions.
