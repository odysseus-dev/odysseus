# Atlas — GOTCHA Agent Ops Overlay

> **Runtime folders are frozen.** Do not move `app.py`, `core/`, `routes/`, `src/`, `services/`, `static/`, `data/`, root `tools/`, or Docker paths. This tree is an agent-organization overlay only.

## GOTCHA layers

| Layer | Path | Purpose |
|-------|------|---------|
| **G**oals | [`goals/`](goals/) | Process workflows + [`manifest.md`](goals/manifest.md) |
| **O**rchestration | *(AI manager)* | Reads goals/args/context; delegates to indexed tools |
| **T**ools | [`tools/`](tools/) | [`manifest.md`](tools/manifest.md) index only — scripts stay in root `tools/`, `scripts/`, `mcp_servers/` |
| **C**ontext | [`context/`](context/) | Relocated handoffs/transcripts + [`INDEX.md`](context/INDEX.md) for in-place folders |
| **H**ard prompts | [`hardprompts/`](hardprompts/) | [`INDEX.md`](hardprompts/INDEX.md) → root [`prompts/`](../prompts/) |
| **A**rgs | [`args/`](args/) | Agent behavior stubs (not app secrets) |

Also: [`.tmp/`](.tmp/) (disposable scratch), [`memory/`](memory/) (session memory logs), [`SIZE.md`](SIZE.md) (measured footprint).

## Canonical handbook

Vendor GOTCHA handbook (do not duplicate):

- [`../atlas_framework/atlas_framework/CLAUDE.md`](../atlas_framework/atlas_framework/CLAUDE.md)
- Setup: [`../atlas_framework/atlas_framework/SETUP_GUIDE.md`](../atlas_framework/atlas_framework/SETUP_GUIDE.md)

## OpenSpec change

Tracked under [`openspec/changes/atlas-repo-organize/`](../openspec/changes/atlas-repo-organize/).
