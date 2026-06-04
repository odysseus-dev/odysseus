# Reviewer — Infrastructure

**Scope:** `Dockerfile`, `docker-compose.yml`, `docker/**`, `**/*.sh`,
`*.service`, `*.ps1`, `.github/workflows/**`, `requirements*.txt`.

Apply [`../review-checks-common.md`](../review-checks-common.md) first.
Trust stated design intent in the PR — frame concerns as "consider
whether", not "this is wrong".

## Check

- **Docker** — no secrets baked into image layers or `ENV`; pin base
  images to a tag (not a moving `latest` where reproducibility matters);
  minimize layers and copied context (`.dockerignore`); run as non-root
  where practical; `docker compose config` would still parse.
- **Shell scripts** — check the shebang before flagging syntax (bash vs
  sh vs fish differ); quote variable expansions (`"$VAR"`); `set -euo
  pipefail` for bash where failure should abort; no `curl | sh` of
  untrusted sources; no silent error swallowing (`|| true`, `2>/dev/null`)
  on steps whose result drives later logic.
- **GitHub Actions** — least-privilege `permissions:` block; pin actions
  to a major version or SHA; do not echo secrets into logs; secrets are
  unavailable to fork PRs by default (don't assume they exist);
  `concurrency` set for PR-triggered workflows to avoid pile-ups.
- **Dependencies** (`requirements*.txt`) — new deps are justified, not
  obviously abandoned, and reasonably pinned; flag a dependency added for
  a one-line utility.
- **Systemd / service units** — correct `ExecStart`, restart policy, and
  no plaintext secrets in the unit.

## Common false-positives — do NOT flag

- A deliberately unpinned base image in a dev-only file.
- `|| true` on a genuinely optional cleanup step.
- Different scripts using different shells — that is allowed.
- Questioning a design choice the PR body already explains.

## Extend

Add checks here as deployment conventions firm up (e.g. a required CI
gate, an image-scanning step). Cross-cutting rules go in
[`../review-checks-common.md`](../review-checks-common.md).
