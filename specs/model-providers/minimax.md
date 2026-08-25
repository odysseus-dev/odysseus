# MiniMax Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `minimax`; international host `api.minimax.io`, China
host `api.minimaxi.com`; current OpenAI-compatible and recommended
Anthropic-compatible text transports. Odysseus contains MiniMax-oriented tool
output handling and local-serving guidance but no dedicated catalog reader.

## Catalog Shape

Current `GET /v1/models` is an OpenAI-compatible identity list:
`object: list`, `data[]`, and model cards containing `id`, `object: model`,
`created`, and `owned_by: minimax`. The `owned_by` discriminator identifies the
provider shape, but the card exposes no per-model capability or modality
fields. Keep these records unknown and preserve raw identity metadata.

Do not backfill current model capabilities, token limits, or modalities from
the platform overview into this list response. Those tables are useful scoped
registry evidence only after model/version identity and freshness are carried
explicitly.

## Request And Response Shape

- OpenAI compatibility uses `/v1/chat/completions` and structured
  `reasoning_content` alongside normal message content.
- Anthropic compatibility uses `/anthropic/v1/messages`; the current M2.7
  family supports typed thinking blocks and interleaved thinking, making this
  the preferred reasoning/tool-continuation transport in provider guidance.
- Native audio, image, video, music, and file endpoints are separate product
  shapes. They must not be inferred from presence in the text model list.

## Local Deployments

The current provider guide documents vLLM, SGLang, and MLX deployment. Those
instances retain serving-engine identity and configuration-derived capability;
the checkpoint name alone does not turn a vLLM/SGLang card into the hosted
MiniMax provider shape.

## Fallback And Current Gaps

Exact MiniMax hosts or the discriminating `owned_by: minimax` model-list shape
select provider identity. Unknown compatible proxies retain the general shape.
The identity list does not safely distinguish M2 reasoning behavior from
speech/image/video/music products, so exact model quirks remain documentation
until structured model-version evidence reaches runtime request builders.
