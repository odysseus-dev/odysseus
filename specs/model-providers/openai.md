# OpenAI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `openai`; API dialects OpenAI Chat Completions and
Responses; catalog reader `src/model_capability_readers/openai.py`.

## Catalog Shape

`GET /v1/models` returns `object: list` with `data[]` model cards containing
`id`, `object`, `created`, and `owned_by`. This is identity and availability
metadata only. It does not claim vision, tools, reasoning, modality, task, or
context length. The record remains unknown and keeps the raw fields.

## Request And Response Shape

Chat uses `messages`, `tools[].function`, `tool_choice`, and
`choices[].message|delta`; Responses uses `input`, flattened tools, output
items, and typed stream events. OpenAI may support a parameter at the platform
level while individual models differ. A later model registry or probe must
scope that fact before it becomes canonical model capability.

## Fallback And Safety

Exact `*.openai.com` host identity selects this provider; lookalikes do not.
Do not parse model IDs or ownership labels. If a proxy returns richer explicit
fields under an OpenAI endpoint configured as OpenAI, preserve them as raw
evidence until an intentional OpenAI shape version maps them.

## Current Gaps

- OpenAI's Models API does not publish the per-model capability shape needed
  for automatic canonical classification.
- Runtime model-specific sampling/reasoning behavior still needs a maintained
  structured registry or endpoint probes.
