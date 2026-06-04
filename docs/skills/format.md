# SKILL.md Format

Odysseus skills are Markdown files with a small YAML frontmatter block and a
structured body. They are stored under:

```text
data/skills/<category>/<name>/SKILL.md
```

The `name` is a slug and also acts as the skill id. External integrations should
ship skills as `status: published` so the agent can discover them immediately.

## Example

```markdown
---
name: open-pr-from-branch
description: Open a focused pull request from the current git branch.
version: 1.0.0
category: dev
tags: [git, github]
platforms: [linux, macos]
requires_toolsets: [bash]
fallback_for_toolsets: []
status: published
confidence: 0.9
source: imported
owner: alice
created: 2026-05-09T21:43:00Z
---

## When to Use

Use when the user asks to publish a completed local change to GitHub.

## Procedure

1. Inspect `git status` and the diff.
2. Stage only the intended files.
3. Commit, push, and open a draft PR linked to the issue.

## Pitfalls

- Do not stage unrelated user changes.
- Do not mark the PR ready until checks pass.

## Verification

- `git status --short` shows no unintended files.
- The PR body links the issue and lists the tests that ran.
```

## Frontmatter

The parser accepts a small YAML subset: scalar `key: value` pairs, inline lists
like `[git, github]`, and block lists with `- item`. Complex YAML features are
not required.

| Field | Required | Notes |
| --- | --- | --- |
| `name` | Yes | Slug used as the skill id and directory name. Non-slug text is normalized. |
| `description` | Recommended | One-line summary shown in the skill index. |
| `version` | No | Defaults to `1.0.0`. Quote values if you need exact formatting. |
| `category` | No | Defaults to `general`; used for grouping. |
| `tags` | No | List of search/filter tags. |
| `platforms` | No | Optional platform gate such as `linux`, `macos`, or `windows`. |
| `requires_toolsets` | No | Hide unless all listed toolsets are active, for example `[bash]`. |
| `fallback_for_toolsets` | No | Hide when any listed toolset is active. |
| `status` | No | Use `published` for integrator-provided skills. `draft` is for work in progress. |
| `confidence` | No | Float from `0` to `1`; defaults to `0.8`. |
| `source` | No | Free-form provenance such as `learned`, `taught`, or `imported`. |
| `teacher_model` | No | Optional model name for teacher-generated skills. |
| `owner` | No | Set in multi-user installs so only that user sees the skill. |
| `created` | No | ISO 8601 UTC timestamp; generated on save if omitted. |

Usage counters such as `uses` and `last_used` are not stored in `SKILL.md`.
They live in `data/skills/_usage.json`, keyed by owner plus skill name.

## Body Sections

The body is split by `##` headings. Known headings are:

- `## When to Use` -> stored as plain text.
- `## Procedure` -> stored as ordered steps.
- `## Pitfalls` -> stored as bullet items.
- `## Verification` -> stored as bullet items.

Unknown headings and any other paragraphs are preserved as `body_extra`, so
manual additions round-trip when the skill is saved again.

For list sections, use either bullets (`- item`, `* item`) or numbered steps
(`1. item`, `2) item`). Continuation lines are folded into the previous item.

## Discovery Rules

The agent sees published skills in the available-skills index when they match
the current owner and tool/platform gates. Draft skills are normally hidden from
that index unless they were created by the teacher-escalation loop.

For external integrations, the safest default is:

```yaml
status: published
source: imported
owner: <target username>
```

Leave `owner` blank only for single-user or legacy deployments where skills are
intentionally shared by the process owner.
