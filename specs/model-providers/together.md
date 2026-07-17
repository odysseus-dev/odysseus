# Together AI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `together`; OpenAI-compatible cloud transport; curated
models and discovery compatibility in `routes/model_routes.py`.

## Shape And Observations

Together has returned both standard `data[]` and bare model-card lists. The
general reader accepts both and keeps identity/provider scope (#4761). Only
explicit per-item task, modality, capability, parameter, and limit fields are
promoted; model names and the curated picker list are not capability evidence.

Together can serve many upstream families. Direct-provider quirks do not
automatically apply because Together may normalize requests and responses.

## Fallback And Current Gaps

Both `*.together.xyz` and `*.together.ai` identify the provider. Malformed/null
lists fail soft. A provider-specific rich capability schema has not been
confirmed, so general fallback remains intentional.
