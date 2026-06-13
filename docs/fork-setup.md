# Fork Setup — maylortaylor/odysseus

This doc covers how to configure the local clone of Matt's personal fork so git and `gh` commands all default to the right place.

---

## Remote Configuration

After cloning, rename the remotes so the fork is `origin` and the upstream source is `upstream`:

```bash
git remote rename origin upstream
git remote add origin https://github.com/maylortaylor/odysseus.git
git branch --set-upstream-to=origin/dev dev
```

Expected result:

| Remote | URL | Role |
|--------|-----|------|
| `origin` | `https://github.com/maylortaylor/odysseus.git` | **Default push target** |
| `upstream` | `https://github.com/pewdiepie-archdaemon/odysseus.git` | Pull-only — never push here |

---

## Branch Strategy

- **`dev`** — default branch, tracks `origin/dev`. All personal feature PRs merge here.
- **`main`** — stable/release branch. Periodically fast-forwarded from `dev`.
- **Feature branches** — `feature/<description>`, branch off `dev`, PR back into `dev`.

---

## Starting New Work

```bash
# Sync with upstream before branching
git fetch upstream
git merge upstream/dev
git push

# Create feature branch
git checkout -b feature/my-thing
```

---

## Syncing with Upstream

```bash
git fetch upstream
git merge upstream/dev   # or: git rebase upstream/dev
git push
```

Run this before any new feature branch to pick up upstream changes.

---

## Personal IaC Integrations

Personal integrations (Gmail, CalDAV, Fallow MCP, Filesystem MCP) are provisioned via:

```bash
./venv/bin/python scripts/setup-integrations.py
```

Requires a `.env` file at the repo root. See `.env.example` for the full variable list.
Add `GMAIL_PERSONAL_USER` and `GMAIL_PERSONAL_APP_PASSWORD` at minimum for email + calendar.

Re-run after any Odysseus restart — it is fully idempotent.

---

## PR Workflow

```bash
# Push feature branch to fork
git push -u origin feature/my-thing

# Open PR against fork's dev branch
gh pr create --repo maylortaylor/odysseus --base dev \
  --title "feat(scope): description" \
  --body "## Summary\n- ...\n\n## Test plan\n- [ ] ..."
```

> **Always use `--repo maylortaylor/odysseus`** with `gh pr create` to avoid accidentally opening a PR on the upstream project.

---

## Contributing Back Upstream

If a change is generic enough to benefit all Odysseus users, open a **separate PR** from `maylortaylor/<branch>` → `pewdiepie-archdaemon/odysseus`. Do not mix personal config or IaC scripts in upstream PRs.
