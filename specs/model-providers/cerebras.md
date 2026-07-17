# Cerebras Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `cerebras`; OpenAI-compatible cloud transport; runtime
provider detection and cache-affinity safeguards in `src/llm_core.py`.

## Shape And Observations

Model lists use the general identity-only inventory reader. Cerebras rejects
llama.cpp-only `session_id` and `cache_prompt` fields (#4640), so cloud identity
must suppress local slot-affinity extensions. Current regressions pin this
provider boundary.

Tool, reasoning, structured output, and limits remain per model. Do not promote
them from the fact that the API accepts OpenAI Chat.

## Fallback And Current Gaps

Exact `*.cerebras.ai` selects provider identity. Compatible proxies require
explicit configuration. No rich per-model Cerebras catalog reader is present.
