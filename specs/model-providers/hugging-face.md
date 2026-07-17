# Hugging Face Provider And Registry Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `huggingface`; Hub registry reader
`src/model_capability_readers/huggingface.py`; download/fit metadata in
`services/hwfit/`; OpenAI-compatible inference providers/TGI handled as their
serving dialect.

## Hub Model Shape

Hub model info can provide `modelId`/`id`, `pipeline_tag`, `tags`, `config`, and
card metadata. Only exact `pipeline_tag` values map canonical task/family and
modalities, including text generation, embeddings/feature extraction,
image-text-to-text, text/image/video generation, ASR, TTS, and classification.
`config.model_type` is structured family evidence.

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
- Pipeline tags can be missing or overly broad; unknown stays unknown.
