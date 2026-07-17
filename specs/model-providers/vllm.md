# vLLM Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `vllm`; OpenAI Chat and Responses serving; exact vLLM
model-card detection with identity-only inventory normalization.

## Catalog Shape

Current `GET /v1/models` returns `object: list`, `data[]` model cards with
`id`, `object`, `owned_by: vllm`, `root`, `parent`, `max_model_len`, and
`permission[]`. The current detector uses these fields to identify the shape,
but the inventory reader does not promote `max_model_len` or `root` into the
canonical record. The card does not prove chat template, tools,
reasoning parser, vision assets, embeddings, transcription, or rerank.

LoRA cards can use a different `id`, root path, and parent. Keep each served ID
endpoint scoped and do not merge it globally with the base checkpoint.

## Runtime Capability

vLLM's supported API surface is broad, but actual behavior depends on the
loaded model task, chat template, multimodal assets, tool-call parser,
reasoning parser, structured-output configuration, and launch flags. Current
Odysseus reasoning regressions cover structured `reasoning`, legacy
`reasoning_content`, and compatible fields (#602). These response channels are
transport evidence, not a claim that every vLLM model reasons.

## Fallback And Safety

Port 8000 is not vLLM identity. Prefer explicit kind; the discriminating model
card shape is a secondary identity signal. Do not consume `/server_info`
environment/config dumps for normal discovery because they can be large and
operationally sensitive.

## Current Gaps

- A small safe native capability endpoint is not part of the canonical probe.
- Deployment parser/template flags are not persisted with endpoint capability.
