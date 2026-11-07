# vLLM Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical placeholder provider ID `vllm`; OpenAI Chat and Responses serving;
generic identity-only inventory normalization. There is no dedicated vLLM
reader or model-card detector on current `dev`.

## Catalog Shape

Current `GET /v1/models` returns `object: list`, `data[]` model cards with
`id`, `object`, `owned_by: vllm`, `root`, `parent`, `max_model_len`, and
`permission[]`. The generic reader retains only identity/raw data and does not
inspect `owned_by`, `root`, `parent`, `max_model_len`, or `permission`. The card
does not prove chat template, tools,
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

Current reader detection identifies port 8000 as vLLM, or accepts an explicit
endpoint kind, then dispatches to the generic identity-only reader. It does not
infer vLLM from the model-card payload. Do not consume `/server_info`
environment/config dumps for normal discovery because they can be large and
operationally sensitive.

## Current Gaps

- A small safe native capability endpoint is not part of the canonical probe.
- Deployment parser/template flags are not persisted with endpoint capability.
- No dedicated reader maps vLLM model-card fields today.
