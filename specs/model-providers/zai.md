# Z.AI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `zai`; Z.AI/GLM OpenAI-compatible endpoints including
coding-plan variants; curated discovery in `routes/model_routes.py` and prior
vision/reasoning fixes such as #664.

## Shape And Observations

Use general identity-only inventory mapping. Some working coding-plan models may be
absent from `/models`, so pinned/curated IDs are availability compatibility,
not capability truth. GLM reasoning controls have appeared as structured
objects or serving-template kwargs depending on direct cloud versus local
engine (#3031). Keep those scopes separate.

## Fallback And Current Gaps

Exact `*.z.ai` or explicit endpoint kind preserves Z.AI identity. Never infer
vision/reasoning/tool support from `glm` in a name. A rich official model-card
reader and direct-versus-coding-plan schema split are still missing.
