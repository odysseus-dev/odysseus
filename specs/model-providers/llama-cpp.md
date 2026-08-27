# llama.cpp Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical provider ID `llamacpp`; OpenAI Chat/Responses and Anthropic Messages
compatibility plus native server metadata; reader
`src/model_capability_readers/llamacpp.py`.

## Metadata Shapes

`/v1/models` provides served identity and can include server model entries;
native `/props` is authoritative for the running model/server combination:

- `model_alias`/`model_path`;
- `default_generation_settings.n_ctx` and sampling `params`;
- `total_slots` and optional `/slots[].n_ctx` fallback;
- `chat_template_caps` for tools/system role;
- `modalities.vision|audio`;
- current server/build state.

Capability depends on weights, projection/model assets, chat template, parser,
and launch flags. It is endpoint evidence, not a checkpoint-name claim.
`/props` and `/v1/models` can be merged only for the same served identity.

## Request And Response Shape

llama-server supports several OpenAI-compatible tasks and native extensions.
Do not infer embeddings/rerank/chat solely from the OpenAI model card; use an
explicit server model capability field or endpoint configuration. Tool and
reasoning correctness can depend on selected chat template and parser.

## Fallback And Safety

The registry selects llama.cpp through an explicit vendor or endpoint kind; it
does not auto-detect `/props` from payload shape. Port 8000 currently maps to
the vLLM placeholder, while 8080 falls through to generic OpenAI-compatible.
llama.cpp-only `session_id` and `cache_prompt` affinity fields must remain local
endpoint behavior and never leak to strict cloud providers (#4640 and current
affinity tests).

## Current Gaps

- Multi-model routing requires per-served-model `/props` association.
- Parser/template configuration is not yet fully represented in canonical
  endpoint metadata.
