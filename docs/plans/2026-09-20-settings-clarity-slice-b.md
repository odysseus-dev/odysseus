# Settings clarity slice B — information architecture

Agents: this slice is Settings IA. Do not wire Default Chat Model to
9router routes (slice C). Do not delete leftover cloud ModelEndpoint
admin (slice D).

## Intent

One Inference area. Overlay 9router is first. Local leftover is last.
Added Models is not a peer “add model” story. Integrations Codex/Claude
read as inbound CLI plugins.

## Hierarchy (coarse → fine)

1. 9router connections
2. 9router routes (`automatic` / `fast` / `balanced` / `best`)
3. OpenHands runtime (Native / OpenCode on the composer)
4. Chat vs Agent workspace privilege (composer)
5. Local leftover add + inventory

## In this slice

- Nav label Inference. Drop Added Models tab and cloud API inventory.
- Keep leftover local endpoints on Inference.
- Group Integrations accounts vs CLI plugins (call Odysseus).
- Tests: `tests/test_ninerouter_settings_static.py` slice B cases plus
  Settings shell registry order.
