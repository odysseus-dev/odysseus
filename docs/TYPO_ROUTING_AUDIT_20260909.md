# Typo-tolerant tool routing audit

The 9B SFT model was not retrained. This audit targets the earlier harness
stage that decides which complete tool families the model is allowed to see.

## Method

- Source prompts: real `sft_alex_creator` sessions from `a37dcb3b-...` onward.
- Labels: recorded single-family tool calls, excluding mixed/ambiguous traces.
- Variants: deletion, adjacent transposition, duplicated character,
  keyboard-neighbor substitution, and accidental word split.
- Split: deterministic SHA-256 assignment before scoring (75% dev, 25% blind).
- Safety: static routing only; no historical mutation or send action is replayed.
- Acceptance: at least 95% blind exact-family accuracy and below 1% blind
  wrong-family authorization. Abstention is measured separately.

## Results

| Router | Dev family supplied | Blind family supplied | Blind exact | Blind wrong-family |
|---|---:|---:|---:|---:|
| Previous exact rules | 63.64% | 65.69% | — | — |
| Conservative fuzzy fallback r4 | 96.31% | 98.31% | 96.62% | 0.00% |
| Final router + safe-read repair | 98.31% | 98.73% | 97.05% | 0.00% |

The fallback runs only for action/lookup-shaped requests, resolves exactly one
nearby family term, and abstains on ambiguity. Conceptual questions remain
tool-free. Complete family schemas are still selected by the immutable turn
contract; fuzzy matching never chooses an individual tool or its arguments.

Authoritative machine reports:

- `reports/typo-tool-routing-baseline-20260909.json`
- `reports/typo-tool-routing-fuzzy-r4-20260909.json`
- `reports/typo-tool-routing-final-20260909.json`
- `reports/post-followup-agent-80-20260909.json`
- `reports/post-typo-routing-agent-80-20260909.json`
- `reports/live-typo-agent-20-20260909.json`
- `reports/live-typo-unresolved-r3-20260909.json`
- `reports/live-typo-agent-final-20-20260909.json`
- `reports/post-typo-safe-read-agent-final-80-20260909.json`

## Live 7011 findings

The post-deployment standard matrix passed 80/80 through the real Agent UI.
The first read-only typo matrix then attempted 17 of 20 planned turns before
its total-time limit. Initial Notes, Calendar, Email, Tasks, Documents, and
Cookbook calls passed. Completed failing turns still had the correct family
and required tool in `turn_contract.offered`; the 9B model sometimes answered
without calling that offered tool. Memory and Search also exposed timeouts.

This separates three failure classes:

1. **Tool injection:** addressed by conservative fuzzy family routing; blind
   exact routing is 96.62% with zero blind wrong-family authorizations.
2. **Required read execution:** a correctly offered safe list/refresh tool can
   still be skipped by the model, especially after a typo or on “list those
   again” follow-ups. This should be handled by the generic deterministic
   safe-read path, not additional prompt-specific hints.
3. **Runtime timeout:** Search and one Memory follow-up require loop/backend
   diagnosis. A timeout is not counted as a model-accuracy or routing result.

The generic safe-read parser and search-family precedence were then repaired.
The previously unresolved Calendar, Email, Search, and Shell/Files cases passed
8/8. The complete typo matrix passed 20/20, including initial requests and
follow-ups for all ten families. The final standard Agent UI compatibility
matrix passed 80/80 across family, Web-toggle, and follow-up combinations.

The broad routing regression suite passed 458 tests. The model was not
retrained and no DeepSeek API was used: the measured defect was in harness
family selection and deterministic safe-read execution, upstream of the
model. All 1,535 unique labeled historical turns were statically audited to
mine failure categories. Historical write/send/delete actions were not replayed
against live data; live verification used the deduplicated read-only matrices.
