# OpenAI Provider Shape

Last updated: dev@e71f8ce | 2026-08-25

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

An explicit endpoint kind selects this provider. Automatic reader detection accepts exact `openai.com` or a dot-delimited subdomain after normalizing case/trailing dots; it is a normalization hint rather than a trust boundary. Do not parse model IDs or ownership labels. If a proxy returns richer fields while explicitly configured as OpenAI, the reader preserves them as raw evidence but keeps capability unknown.

## Current Gaps

- OpenAI's Models API does not publish the per-model capability shape needed
  for automatic canonical classification.
- Runtime model-specific sampling/reasoning behavior still needs a maintained
  structured registry or endpoint probes.
