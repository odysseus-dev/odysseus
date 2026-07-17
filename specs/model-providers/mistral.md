# Mistral Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `mistral`; OpenAI-compatible chat with Mistral response
extensions; reader `src/model_capability_readers/mistral.py`; runtime handling
in `src/llm_core.py`.

## Catalog Shape

`GET /v1/models` returns `data[]` cards with `id`, `root`, aliases,
`max_context_length`, and `capabilities` booleans including
`completion_chat`, `completion_fim`, `function_calling`, `vision`,
`classification`, and lifecycle/fine-tuning fields. Map only runtime fields:

- chat/FIM or classification family;
- vision input;
- function calling;
- explicitly reported reasoning/structured output when present;
- context limit and root family.

Fine-tuning availability and archived status are not inference capabilities.
Different Mistral models retain independent records.

## Request And Response Shape

Reasoning-capable models accept graded `reasoning_effort`. Mistral can return
`content` as typed blocks: a `thinking` block containing text fragments plus a
normal `text` block. Normalize these structured blocks into separate reasoning
and visible channels; do not stringify the list or scan text tags (#4698).

## Fallback And Safety

Exact Mistral host or rich capability shape selects this reader. A Mistral
model served through another engine uses that serving engine's dialect and
capability evidence; direct-Mistral response quirks do not automatically apply.

## Current Gaps

- Catalog reasoning fields vary across model-card generations; absent remains
  unknown.
- Runtime thinking-family selection still uses names and should migrate to
  structured root/capability identity.
