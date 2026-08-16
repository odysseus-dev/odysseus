# OpenCode Provider Shape

Last updated: dev@2e2bb52 | 2026-08-16

## Scope

Canonical provider identity `opencode` with Zen/Go endpoint variants; OpenAI-compatible transport and webhook presets in `src/llm_core.py` and canonical `routes/webhook/webhook_routes.py`, with the top-level route module retained as a compatibility shim.

## Shape

Keep Zen and Go path identity in endpoint metadata even though the canonical
provider family is OpenCode. Model discovery uses general identity-only
fallback. Path/version, account policy, and model selection can differ between
variants; do not flatten them into OpenAI.

## Fallback And Current Gaps

Exact `*.opencode.ai` plus configured `/zen` or `/zen/go` selects this family.
No provider-specific rich capability catalog is mapped, and runtime still has
separate variant labels that should eventually become structured endpoint
metadata.
