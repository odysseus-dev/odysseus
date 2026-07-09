# SKILL.md format

Odysseus agent skills are plain Markdown files with YAML frontmatter. The agent discovers them via the `manage_skills` tool and loads full procedures on demand.

## Where skills live

On disk, skills are stored under your workspace data directory:

```text
data/skills/<category>/<name>/SKILL.md
```

Optional supporting files (references, templates, scripts) live in subfolders under the same skill directory. Usage counters (`uses`, `last_used`) are kept in `data/skills/_usage.json` so the SKILL.md file does not change on every retrieval.

The canonical parser and writer live in `services/memory/skill_format.py` if you need to match behavior exactly.

## Frontmatter (YAML)

Required fields for a usable skill:

| Field | Description |
|-------|-------------|
| `name` | Kebab-case identifier (matches the directory name). |
| `description` | One-line summary shown in the skills index. |

Common optional fields:

| Field | Description |
|-------|-------------|
| `version` | Semver string, e.g. `1.0.0`. |
| `category` | Grouping folder under `data/skills/`. |
| `tags` | Inline list, e.g. `[git, github]`. |
| `platforms` | e.g. `[linux, macos]`. |
| `requires_toolsets` | Toolsets the skill expects. |
| `fallback_for_toolsets` | Toolsets this skill can stand in for. |
| `status` | `draft` or `published`. |
| `confidence` | `0`–`1` for learned skills. |
| `source` | `learned`, `taught`, or `imported`. |
| `teacher_model` | Model that authored a learned skill. |
| `created` | ISO-8601 timestamp. |
| `owner` | Username for multi-user installs; omit for shared skills. |

Example:

```yaml
---
name: open-pr-from-branch
description: Open a GitHub pull request from the current branch.
version: 1.0.0
category: dev
tags: [git, github]
platforms: [linux, macos]
requires_toolsets: []
fallback_for_toolsets: []
status: published
confidence: 0.8
source: taught
created: 2026-05-09T21:43:00Z
---
```

## Body sections

Use Markdown headings. Any subset is valid; unknown trailing content is preserved on round-trip.

### When to Use

Plain-language trigger conditions — when the agent should load this skill.

### Procedure

Numbered steps the agent should follow.

### Pitfalls

Common failure modes and how to recover.

### Verification

How to confirm the task succeeded.

Example body:

```markdown
## When to Use

User asks to open a PR from the current branch against `dev`.

## Procedure

1. Confirm the branch is pushed to the remote.
2. Run tests relevant to the change.
3. Open the PR with `Fixes #N` when applicable.

## Pitfalls

- Base branch must be `dev`, not `main`.
- Do not open duplicate PRs for the same issue.

## Verification

- PR URL is reachable.
- CI checks are triggered.
```

## Agent API

Integrators and the agent use `manage_skills` with actions such as `list`, `view`, `search`, `add`, `edit`, `patch`, `publish`, and `delete`. `list` returns the index; `view name=<skill>` returns the full SKILL.md.

## Related reading

- [Contributing](../../CONTRIBUTING.md)
- Hermes skills format (design inspiration): [Skills user guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)