# NVIDIA NIM Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `nvidia`; OpenAI-compatible NVIDIA/NIM endpoints; current
provider detection, catalog routing, and reasoning stream handling in
`src/llm_core.py`, `routes/model_routes.py`, and tests.

## Shape And Observations

Model lists use the general identity-only shape; capability-looking fields
require a provider-native mapped shape.
NIM/vLLM-style responses have emitted structured `reasoning` while older paths
used `reasoning_content`; Odysseus routes either to the reasoning channel
(#602). This response compatibility does not claim that every NIM model
reasons.

NVIDIA endpoints can host many unrelated model families with different tools,
vision, context, and parser support. Keep endpoint/model stable identity and
prefer provider fields or probes.

## Fallback And Current Gaps

Exact NVIDIA host preserves provider identity; private NIM installations need
explicit endpoint kind because a local port/hostname is not distinctive. No
safe normalized native NIM capability endpoint is currently consumed.
