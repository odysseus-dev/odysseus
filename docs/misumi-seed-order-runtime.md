# Misumi Seed Order Runtime Loading

Odysseus loads the Misumi Seed Order as trusted runtime context from
`src/seed_order_context.py`.

The loader is read-only. It searches, in order:

- `MISUMI_SOURCE_ROOT`
- `MISUMI_SEED_ORDER_ROOT`
- `MISUMI_CANONICAL_ROOT`
- `FLAT_KNOWLEDGEBASE_ROOT`
- the `misumi_seed_order_root` setting
- common local clone paths such as `~/Documents/flat-knowledgebase`

When a canonical Misumi clone is present, direct chat prompt assembly in
`src/chat_processor.py` and agent/tool prompt assembly in `src/agent_loop.py`
insert the seed context before preset, crew/persona, tool, or routing
instructions.

The runtime context loads the ratified seed files and binding boundaries:

- `docs/core/misumi-seed-order-v0.1.md`
- `docs/core/agent-personality-registry-v0.1.md`
- `protocols/register.md`
- `templates/change-log-entry.md`
- `agents/core/emperor-aoteru-misumi.md`
- `agents/core/operator-lelouch-lamperouge.md`
- `agents/core/archivist-makise-kurisu.md`
- `docs/repository-boundaries.md`
- `docs/odysseus-contract.md`

The seed context makes the repo persona the Misumi Seed Order core plan:
preserve raw actuality, label uncertainty, distinguish candidate from ratified,
keep Level 5/6 changes proposed until ratified, route visible core behavior
through Emperor/Operator/Archivist, and follow:

```text
Observe -> Propose -> Review -> Ratify -> Implement -> Log
```

This change does not add a canonical runtime database, Phase B write path,
secret/config provider, voice pipeline, avatar redesign, or live autonomous
specialist-persona swarm. Specialist personas remain dormant design/routing
concepts until repeated need is evidenced and ratified through the seed order.
