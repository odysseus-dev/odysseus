# Hard prompts index

Reusable LLM instruction templates live in root [`prompts/`](../../prompts/). This folder indexes them; do not duplicate prompt bodies here unless promoting to a stable hard prompt.

| Prompt | Path | Use |
|--------|------|-----|
| Executive brief template | [`prompts/executive-brief-template.md`](../../prompts/executive-brief-template.md) | CEO/exec brief generation |
| RAS morning brief | [`prompts/ras-morning-brief.md`](../../prompts/ras-morning-brief.md) | Morning briefing workflow |
| Work pattern audit | [`prompts/work-pattern-audit.md`](../../prompts/work-pattern-audit.md) | Work-pattern analysis |
| Wargame execution harness | [`prompts/wargame-execution-harness-saas.md`](../../prompts/wargame-execution-harness-saas.md) | SaaS wargame harness |

## Related (not in `prompts/`)

| Source | Path | Use |
|--------|------|-----|
| Agent skills | `.cursor/skills/*/SKILL.md` | Cursor skill hard prompts |
| Claude integrations | `integrations/claude/skills/*/SKILL.md` | Claude Code skills |
| OpenSpec commands | `.cursor/commands/opsx-*.md` | Spec workflow commands |

## Adding a hard prompt

1. Add template under `prompts/` (app-facing) or `atlas/hardprompts/` (agent-only).
2. Register one line in this index.
3. Reference from a goal in [`../goals/manifest.md`](../goals/manifest.md).
