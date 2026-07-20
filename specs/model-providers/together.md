# Together AI Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical provider ID `together`; OpenAI-compatible cloud transport; curated
models and discovery compatibility in `routes/model_routes.py`.

## Shape And Observations

Together has returned both standard `data[]` and bare model-card lists. The
current generic reader accepts the standard envelope when the caller supplies
the Together vendor, but it does not accept a bare root list. It keeps
identity/provider scope and promotes no capability fields. Task, modality,
parameter, and limit data needs a dedicated Together reader before it becomes
canonical; model names and the curated picker list are not capability evidence.

Together can serve many upstream families. Direct-provider quirks do not
automatically apply because Together may normalize requests and responses.

## Fallback And Current Gaps

Both `*.together.xyz` and `*.together.ai` identify the provider. Malformed/null
lists fail soft. A provider-specific rich capability schema has not been
confirmed, so general fallback remains intentional. Bare-list catalogs require
route-specific preprocessing or a future reader update.
