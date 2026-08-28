# Hugging Face Provider And Registry Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical placeholder vendor ID `huggingface`; download/fit metadata in
`services/hwfit/`; OpenAI-compatible inference providers/TGI handled as their
serving dialect. There is no dedicated Hugging Face canonical reader on
current `dev`.

## Hub Model Shape

Hub model info can provide `modelId`/`id`, `pipeline_tag`, `tags`, `config`, and
card metadata. Current canonical normalization does not map `pipeline_tag`,
`config.model_type`, or Hub task/modality fields. An explicitly selected
Hugging Face vendor uses generic identity-only normalization.

This source is `cookbook_hf`/registry confidence, not live endpoint truth.
Free-form tags, README/card prose, repository names, and architecture names do
not automatically claim capability. A serving engine can load a model with
missing projection, different template, or disabled parser.

## Serving Shape

Hugging Face routed inference and TGI can expose OpenAI-compatible endpoints;
their model list may be identity-only. Keep Hub identity separate from the
serving endpoint and merge only when exact revision/model identity is known.

## Fallback And Safety

Hub metadata can fill a scoped registry record after provider payload fields
and probes, but must not overwrite fresh endpoint-negative evidence. Treat
remote code, model cards, and repository files as untrusted content.

## Current Gaps

- Revision/digest linkage between downloads, Hub records, and serving
  endpoints is incomplete.
- Hub task/family metadata is not consumed by the canonical reader package.
- Pipeline tags can be missing or overly broad; unknown stays unknown.
