# Claude Code Review — Maintainer Setup

This repo ships an automated code-review workflow at
[`.github/workflows/claude-code-review.yml`](../../.github/workflows/claude-code-review.yml).
When a pull request is marked **Ready for review**, Claude reads the diff,
posts inline comments on specific issues, and leaves one summary comment.
If it finds issues, it flips the PR back to **draft** so the author can fix
everything in one pass instead of triggering a re-review on every push.

It will not run until a maintainer completes the two one-time setup steps
below. Both require **admin** access to the repository (or the org).

---

## What you need

1. The **Claude GitHub App** installed on this repository — gives the
   workflow a bot identity to post reviews as and grants the GitHub API
   access the action needs.
2. An **`ANTHROPIC_API_KEY`** repository secret — pays for the model calls
   that actually do the review.

You need both. The app handles *GitHub* auth; the API key handles
*Anthropic* (model) billing.

---

## Step 1 — Install the Claude GitHub App

1. Go to **https://github.com/apps/claude** and click **Install** (or
   **Configure** if it is already installed on your account/org).
2. Choose the account or organization that owns this repo
   (`pewdiepie-archdaemon`).
3. Under **Repository access**, either select **All repositories** or pick
   **Only select repositories** and add **odysseus**.
4. Confirm. The app requests read/write on pull requests, issues, and
   contents — that is what lets it post review comments.

> Shortcut: if you have the Claude Code CLI installed locally, running
> `/install-github-app` from inside the repo walks you through both this
> step and Step 2 interactively.

---

## Step 2 — Add the `ANTHROPIC_API_KEY` secret

1. Create an API key at the **Anthropic Console**:
   **https://console.anthropic.com/settings/keys** → *Create Key*.
   Use a key from a workspace/project you are comfortable billing PR
   reviews against — you can set a monthly spend limit on that workspace.
2. In GitHub: **odysseus → Settings → Secrets and variables → Actions →
   New repository secret**.
3. Name it exactly **`ANTHROPIC_API_KEY`** and paste the key as the value.
4. Save.

> The name must match exactly — the workflow reads
> `secrets.ANTHROPIC_API_KEY`. A typo here is the #1 cause of "the action
> ran but did nothing."

That is it. The next PR marked *Ready for review* triggers a review.

---

## How it triggers

| Event | Reviewed? |
|-------|-----------|
| PR opened as **draft** | No |
| PR opened as **ready** (non-draft) | Yes |
| Draft → **Ready for review** | Yes |
| New push to an already-ready PR | **No** — does not re-run on every push |
| PR with only doc/media/etc. changes (no matched paths) | No |

The workflow only watches code-ish paths (`*.py`, `*.js`, `*.css`,
`*.html`, `*.sh`, `*.yml`/`*.yaml`, `Dockerfile`, `docker-compose.yml`,
`requirements*.txt`) and skips itself. To get a fresh review after pushing
fixes: keep the PR in draft while you work, then click **Ready for review**
again.

If you would rather review on *every* push too, add `synchronize` to the
`on.pull_request.types` list in the workflow.

---

## Extending the review rules

The workflow itself is thin — it routes to a rules folder, so you tune the
review by editing Markdown, not YAML:

```
.claude/rules/
  review-prompt.md          # the review prompt itself — the workflow loads this verbatim
  INDEX.md                  # routing table: changed-path glob → reviewer file
  review-guidelines.md      # how a review is conducted (always loaded)
  review-checks-common.md   # cross-cutting checks + false-positive gate (always loaded)
  reviewers/
    backend-python.md       # routes/**, src/**, *.py (incl. tests)
    frontend-web.md         # static/js, *.html, *.css
    infra.md                # Dockerfile, compose, *.sh, CI
    docs.md                 # *.md, docs/**
```

The workflow loads `review-prompt.md` verbatim and hands it to Claude, so
the prompt lives in Markdown — not in the GitHub Action. On each run that
prompt tells Claude to read `INDEX.md`, classify the changed files, and
load only the reviewer file(s) for the domains that actually changed.

- **Change how the review runs / what it does overall** → edit
  `review-prompt.md`. No workflow edit needed.
- **Tweak a domain's checks** → edit the matching `reviewers/*.md`.
- **Add a new domain** → create `reviewers/<domain>.md` and add a row to
  the routing table in `INDEX.md`. No workflow edit needed.
- **A rule that applies to every file** → put it in
  `review-checks-common.md`, not a single domain file.

> The prompt is read as literal Markdown, so GitHub Actions `${{ ... }}`
> expressions do **not** work inside `review-prompt.md`. Use the
> `PR_NUMBER` and `REPO` environment variables (the workflow sets them) in
> any `gh` commands instead. The only things that stay in the workflow are
> the trigger, the model (`claude_args`), and the tool allowlist.

See [`INDEX.md`](INDEX.md) for the
authoritative routing table and the "How to extend" notes.

## Cost & model

The workflow runs on **`--model haiku`** (`claude-haiku-4-5`) — fast and
cheap, sized for routine review passes. To trade cost for depth, edit the
single `claude_args` line in the workflow:

```yaml
claude_args: '--model sonnet ...'   # deeper reasoning, moderate cost
claude_args: '--model opus ...'     # most thorough, highest cost
```

Set a spend cap on the Anthropic workspace tied to the key so a busy PR day
cannot surprise you.

---

## What the review can and cannot touch

The action runs with a tightly scoped tool allowlist (see `claude_args`):
read-only file access (`Read`, `Grep`, `Glob`), the GitHub inline-comment
tools, and read-only `gh` commands plus `gh pr review` / `gh pr ready`. It
**cannot** push code, merge, change settings, or run arbitrary shell. The
GitHub token is scoped to this PR via the `permissions:` block in the
workflow (`contents: read`, `pull-requests: write`, `issues: write`).

Code in the diff is sent to the Anthropic API to perform the review.
Anthropic does not train on API traffic, but treat it like any third-party
service: do not put real secrets in code (use the `ANTHROPIC_API_KEY`
secret and `.env`, which is git-ignored). This matches
[`SECURITY.md`](../../SECURITY.md) and [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

---

## These reviews are AI-generated — verify before acting

Claude's review is a helpful first pass, **not** a substitute for human
judgment. It is biased toward the patterns in its training data and only
sees the diff plus the few files it reads — it does not know the full
runtime behavior, your roadmap, or context outside the PR. It can be
confidently wrong, miss real bugs, and flag non-issues.

- Authors: treat each comment as a suggestion to evaluate, not a command.
  Push back in the thread when it is wrong.
- Reviewers: a clean Claude pass does **not** mean the PR is approved. A
  human still owns the merge decision.
- Never merge solely because the bot was happy.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Workflow never starts | PR is a draft, or the diff touched no matched `paths`. Mark it Ready, or push a code file. |
| Run starts then fails at the Claude step | `ANTHROPIC_API_KEY` missing/misnamed/expired, or the workspace hit its spend limit. Re-check Step 2 and the Anthropic Console. |
| "Resource not accessible by integration" | Claude GitHub App not installed on this repo, or its permissions were declined. Redo Step 1. |
| Forked-PR runs have no secret access | GitHub does not expose secrets to workflows triggered from forks by default. Reviews run for branches in this repo; decide your policy for fork PRs (e.g. a maintainer pushes the branch, or use `pull_request_target` with appropriate caution). |
| Duplicate reviews on one commit | The workflow already de-dupes by SHA; if you still see dupes, check for a second copy of this workflow file. |

Logs live under the repo's **Actions** tab → **Claude Code Review**.
