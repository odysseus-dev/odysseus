# SKILL.md format

Odysseus skills are single Markdown files (`SKILL.md`) with YAML frontmatter and
a structured body. The agent discovers and applies them through the
`manage_skills` tool. This page is the public reference for the format so you can
author skills (for example, to ship alongside an external MCP server) without
reading the source. The parser/writer is `services/memory/skill_format.py`, which
remains the source of truth.

## Frontmatter

A YAML block at the top of the file, delimited by `---`:

```yaml
---
name: open-pr-from-branch
description: One-line summary surfaced in the skills index.
version: 1.0.0
category: dev
tags: [git, github]
platforms: [linux, macos]            # optional
requires_toolsets: []                # optional
fallback_for_toolsets: []            # optional
status: published                    # draft | published
confidence: 0.8                      # 0..1
source: learned                      # learned | taught | imported
teacher_model: claude-opus-4-7       # optional
created: 2026-05-09T21:43:00Z
---
```

Field notes:

- **name** — kebab-case identifier; also used as the skill's key.
- **description** — the one-line summary shown in the skills index, used to decide
  relevance.
- **status** — `draft` or `published`. Only published skills (plus teacher
  drafts) surface in the default index.
- **confidence** — `0.0`–`1.0`.
- **source** — how the skill came to exist: `learned`, `taught`, or `imported`.
- **platforms**, **requires_toolsets**, **fallback_for_toolsets**,
  **teacher_model** are optional.

## Body

Markdown sections after the frontmatter. Any subset may be present; each is
rendered as a heading:

```markdown
## When to Use
Trigger conditions in plain English.

## Procedure
1. First step
2. Second step

## Pitfalls
- Common failure mode + how to recover

## Verification
- How to confirm success
```

Any content after the last recognized section is preserved verbatim (kept as
`body_extra`) and round-trips on save, so you can include extra prose without
losing it.

## Usage counters

Retrieval counters (`uses`, `last_used`) are **not** stored in the `SKILL.md`
file. They live in a sidecar `_usage.json` keyed by skill name, so the skill file
doesn't churn every time the skill is used.

## Lineage

The format is inspired by the Hermes skills format
(<https://hermes-agent.nousresearch.com/docs/user-guide/features/skills>).
