# xAI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `xai`; OpenAI-compatible xAI cloud transport; provider
labels/curation in `src/llm_core.py` and `routes/model_routes.py`.

## Shape

Model discovery uses general identity-only inventory. Reasoning effort, tools,
image input, or other Grok behavior must be
scoped per returned model/registry/probe. The provider's broad API feature set
does not grant every listed model every capability.

## Fallback And Current Gaps

Exact `*.x.ai` selects xAI. Preserve provider identity through OpenAI-compatible
fallback and reject lookalikes. A current rich model catalog schema and
structured version registry are not yet mapped.
