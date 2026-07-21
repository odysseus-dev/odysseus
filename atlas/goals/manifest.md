# Goals manifest

Process workflows for agent orchestration. Goals describe **what** to achieve; runtime code stays in place.

| Goal | Path | Summary |
|------|------|---------|
| Build app (ATLAS) | [`build_app.md`](build_app.md) | Architect → Trace → Link → Assemble → Stress-test |
| Handoff between agents | [`../context/handoff-to-cursor-mempalace-openclaw.md`](../context/handoff-to-cursor-mempalace-openclaw.md) | Cursor ↔ Claude ↔ Odysseus context transfer |
| Odysseus handoff | [`../context/odysseus-handoff.md`](../context/odysseus-handoff.md) | General Odysseus session handoff notes |
| Formflow handoff | [`../context/formflow-v1-handoff.md`](../context/formflow-v1-handoff.md) | Formflow v1 integration handoff |
| Research swarm | *index only* | See [`../context/INDEX.md`](../context/INDEX.md) → `research-orch/` |
| Session ops review | *index only* | See [`../context/INDEX.md`](../context/INDEX.md) → `session-review-*` |
| Codebase map | [`../context/Graphy.md`](../context/Graphy.md) | Generated stack/architecture map |

## External skill workflows (not duplicated here)

- `.cursor/skills/` — Cursor agent skills (research-swarm, handoff, session-recall, etc.)
- `integrations/claude/skills/` — Claude Code Odysseus + handoff skills
- `integrations/codex/skills/` — Codex equivalents

## Adding a goal

1. Add markdown under `atlas/goals/` or relocate context into `atlas/context/`.
2. Register one line in this manifest.
3. Point at existing tools via [`../tools/manifest.md`](../tools/manifest.md).
