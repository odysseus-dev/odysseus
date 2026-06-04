# Reviewer Routing Index

This is the routing table for automated code review. The
[`Claude Code Review` workflow](../../.github/workflows/claude-code-review.yml)
reads this file, classifies the files changed in a PR, and loads the
matching reviewer rule file(s) below — so each review only pulls in the
rules relevant to what actually changed.

For one-time setup (installing the Claude GitHub App and the
`ANTHROPIC_API_KEY` secret), see
[`CLAUDE_CODE_REVIEW.md`](CLAUDE_CODE_REVIEW.md).

**Always loaded** (every review, regardless of domain):

- [`review-guidelines.md`](review-guidelines.md) — how to conduct the
  review: threads, summaries, scope, draft handling.
- [`review-checks-common.md`](review-checks-common.md) — cross-cutting
  checks and the false-positive gate that applies to all files.

## Routing table

Match each changed file against the patterns top-to-bottom; load the
reviewer file for every domain with at least one matched file. Skip
domains with no changed files to conserve context.

| Changed path matches | Reviewer rules |
|----------------------|----------------|
| `app.py`, `routes/**/*.py`, `src/**/*.py`, `services/**/*.py`, `mcp_servers/**/*.py`, `tests/**/*.py`, any other `**/*.py` | [`reviewers/backend-python.md`](reviewers/backend-python.md) |
| `static/**/*.js`, `static/**/*.css`, `static/**/*.html`, `**/*.html`, `**/*.svelte` | [`reviewers/frontend-web.md`](reviewers/frontend-web.md) |
| `Dockerfile`, `docker-compose.yml`, `docker/**`, `**/*.sh`, `*.service`, `*.ps1`, `.github/workflows/**`, `requirements*.txt` | [`reviewers/infra.md`](reviewers/infra.md) |
| `**/*.md`, `docs/**`, `LICENSE`, `licenses/**` | [`reviewers/docs.md`](reviewers/docs.md) |

A file may match more than one domain (e.g. a `.github/workflows/*.yml`
and a `.md` in the same PR) — load every matching reviewer.

## How to extend

To add a new reviewer domain:

1. Create `reviewers/<domain>.md` (copy an existing one as a template).
   Keep it to a focused checklist of what to check **and** common
   false-positives to avoid.
2. Add a row to the routing table above mapping the file patterns to it.

To tighten or relax an existing domain, edit its `reviewers/*.md` file —
no workflow YAML change needed. Cross-cutting rules that apply to *every*
file belong in [`review-checks-common.md`](review-checks-common.md), not
in a single domain file.
