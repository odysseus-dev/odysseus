# Mistral Provider Shape

Last updated: dev@2e2bb52 | 2026-08-16

## Scope

Canonical provider ID `mistral`; OpenAI-compatible chat with Mistral response
extensions and runtime handling in `src/llm_core.py`. There is no dedicated
Mistral canonical reader on current `dev`.

## Catalog Shape

`GET /v1/models` returns `data[]` cards with `id`, `root`, aliases,
`max_context_length`, and `capabilities` booleans including
`completion_chat`, `completion_fim`, `function_calling`, `vision`,
`classification`, and lifecycle/fine-tuning fields. These are candidate fields
for a future dedicated reader:

- chat/FIM or classification family;
- vision input;
- function calling;
- explicitly reported reasoning/structured output when present;
- context limit and root family.

Fine-tuning availability and archived status are not inference capabilities.
The current generic reader retains identity/raw data only and does not map any
of these fields. Different Mistral models retain independent identities.

## Request And Response Shape

Reasoning-capable models accept graded `reasoning_effort`. Mistral can return `content` as typed blocks: a `thinking` block containing text fragments plus a normal `text` block. Runtime normalizes those blocks for async utility calls as well as chat/stream paths, keeping reasoning and visible text separate instead of stringifying the list or scanning text tags (#4698, #5882).

## Fallback And Safety

Runtime `llm_core` detects label-bounded Mistral hosts for request/response
handling. The canonical registry has no Mistral host or rich-payload detector;
an explicitly supplied `mistral` vendor falls back to generic identity. A
Mistral model served through another engine uses that serving engine's dialect.

## Current Gaps

- Catalog reasoning fields vary across model-card generations; absent remains
  unknown.
- Mistral catalog capability fields are not normalized by current `dev`.
- Runtime thinking-family selection still uses names and should migrate to
  structured root/capability identity.
