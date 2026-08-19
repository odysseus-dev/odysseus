# gstack /cso Security Posture Report — odysseus

- **Target:** `C:\Users\Ore\odysseus` (self-hosted AI workspace: Python 3.11 / FastAPI, AGPL-3.0)
- **Date:** 2026-07-23
- **Mode:** daily (8/10 confidence gate), **full audit** (all phases per user selection via clarify())
- **Tool:** gstack `/cso` skill, ported to Hermes Agent. Substitutions applied:
  AskUserQuestion→`clarify()` (scope selection), Grep→`terminal grep` (search_files choked on MSYS paths — noted), Bash→`terminal()`, gstack-* bin→skipped.
- **Live target:** server confirmed up at `http://localhost:7000` (HTTP 302 → login).

## Attack Surface Map

```
CODE SURFACE
  Public endpoints:      auth routes (login/create-account) — unauthenticated by design
  Authenticated:         ~40 route modules (chat, cookbook, calendar, contacts, codex, ...)
  Admin-only:            admin_wipe_routes, api_token_routes — gated by require_admin
  API endpoints:         api_token (bearer) issuance
  File upload points:    data/uploads, data/mail-attachments, data/personal_uploads
  External integrations: SEARXNG_INSTANCE, LLM_HOST, OAuth device_flow, email accounts
  Background jobs:       cleanup_routes, cron-style tasks
  WebSocket channels:    (not enumerated — out of quick-scan scope)

INFRASTRUCTURE SURFACE
  CI/CD workflows:       9 (ci, container-scan, container-trivy, dependency-review,
                          docker-publish, issue/pr-description-check, secret-scan,
                          workflow-security)
  Webhook receivers:     (none identified in quick scan)
  Container configs:     Dockerfile, docker-compose*.yml
  IaC configs:           (none)
  Deploy targets:        docker-publish (ghcr)
  Secret management:     .env (PLACEHOLDER ONLY — see finding F1)
```

**Architecture mental model:** FastAPI app (`app.py`) with per-feature route
modules under `routes/`, shared services in `core/` (auth, db, middleware,
log_safety, atomic_io). Auth = bcrypt password hashing + random session tokens
(7-day TTL) stored in `data/auth.json` + `sessions.json`. Admin actions guarded
by `core.middleware.require_admin`. An internal-tool token
(`X-Odysseus-Internal-Token`) gates service-to-service calls. Some routes shell
out to `tmux`/`ssh` for the cookbook model-serve feature — these are the
highest-risk paths and were actively verified (see F2).

## Findings

Confidence gate (daily, 8/10) applied. Candidates scanned: .env secrets,
git-history, dependency CVEs, CI/CD, web/code sinks (subprocess/shell, eval,
SQLi, SSRF), auth/session handling, CORS, admin authorization, internal token.

**Reported findings: 1 (one INFO-level observation), 0 vulnerabilities at ≥8/10.**

### F1 — [INFO] (confidence: 9/10) `.env` contains only placeholder values
`app.py:128` reads `ALLOWED_ORIGINS`; `core/middleware.py:16` reads
`ODYSSEUS_INTERNAL_TOKEN`. The committed `.env` has only:
```
LLM_HOST=localhost
SEARXNG_INSTANCE=http://localhost:8080
```
Verified by reading the file directly — no API keys, DB passwords, or tokens are
present. This is GOOD posture (no secrets in VCS). Listed only as an observation:
if you later populate real secrets here, ensure `.env` stays gitignored (it is,
per AGENTS.md) and add a `.gitleaks.toml` pre-commit (the repo has
`secret-scan.yml` CI but no local hook).
**Exploit scenario:** none — not exploitable.
**Recommendation:** keep `.env` out of git; consider a local gitleaks pre-commit to match the CI `secret-scan` job.

### F2 — [VERIFIED NON-FINDING] Shell-exec sinks are properly guarded
`routes/codex_routes.py` shells out via `asyncio.create_subprocess_shell` at
lines 592, 654, 813. Phase 12 requires quoting the motivating lines, so I traced
each:
- L651 `ssh {port_flag}{host} "tmux kill-session -t {session_id}"` → `host` comes
  from `_ssh_prefix_for_task()`, which runs `validate_remote_host()`
  (`routes/_validators.py:12`). That function rejects anything not matching
  `_REMOTE_HOST_RE` (strict `host`/`user@host`, no SSH-option or shell syntax) with
  HTTP 400. `session_id` is `fullmatch(r"[a-zA-Z0-9_-]+")`. **SAFE.**
- L810 `ssh {shlex.quote(host)} 'tmux has-session -t {shlex.quote(sess)}'` →
  `shlex.quote` on both `host` (also pre-validated) and `sess`. **SAFE.**
Conclusion: the docstring's claim ("validate them the same way the cookbook
routes do ... rejected with 400 rather than injected") is **accurate and
verified**. Discarded as a vulnerability with evidence.

### F3 — [INFO] (confidence: 8/10) CI security hygiene is exemplary
`workflow-security.yml` uses `permissions: {}` (default-deny), pins every action
to a full SHA (`actions/checkout@9c091bb...#v7.0.0`), checksum-verifies the
actionlint binary, sets `persist-credentials: false`, and runs `zizmor` (Actions
SAST). `secret-scan.yml`, `dependency-review.yml`, `container-trivy.yml` add
secret/dep/image scanning. No `pull_request_target`+code-checkout, no `secrets`
passed to untrusted steps observed. This is above-average supply-chain posture.

### Below-gate / discarded candidates
- No SQLi: DB layer uses SQLAlchemy ORM / parameterized statements (no raw
  `%`-interpolation `where()` found in `core/database.py`).
- No `eval`/`exec`/`pickle.load`/`yaml.load` on untrusted input found.
- No `verify=False` / disabled-TLS found.
- CORS defaults to `http://localhost,http://127.0.0.1` with
  `allow_credentials=True` — safe by default; only becomes risky if an operator
  sets `ALLOWED_ORIGINS=*` (trusts the env, not a code bug).
- Internal token: `INTERNAL_TOOL_TOKEN = os.environ.get(...) or secrets.token_hex(32)`
  → random 32-byte token per process when unset. Not a hardcoded shared secret.
- bcrypt + 7-day TTL sessions with on-disk pruning — standard, acceptable.

## Trend Tracking
First run on this project. No prior `.gstack/security-reports/` baseline →
`first_run`. Filter stats: ~9 pattern classes scanned → 0 confirmed
vulnerabilities, 1 INFO (F1), 1 verified non-finding (F2), 1 positive-observation
(F3).

## Protection file check
No `.gitleaks.toml` / `.secretlintrc` present locally. CI covers secrets
(`secret-scan.yml`) but a local hook would catch them pre-push. Low priority
given current empty `.env`.

## Remediation Roadmap
None required. The app's posture is strong: no committed secrets, hardened CI,
consistent admin authorization, and correctly-guarded shell execution. If you
add real secrets or expose the server beyond localhost, re-run `/cso` and watch
for: (1) `ALLOWED_ORIGINS` loosened to `*` with `allow_credentials=True`, (2) any
new shell-exec path that interpolates user input WITHOUT `validate_remote_host`
or `shlex.quote`, (3) `ODYSSEUS_INTERNAL_TOKEN` set to a static string in `.env`.

## Disclaimer
This tool is not a substitute for a professional security audit. /cso is an
AI-assisted scan that catches common vulnerability patterns — it is not
comprehensive, not guaranteed, and not a replacement for hiring a qualified
security firm. For production systems handling sensitive data, payments, or PII,
engage a professional penetration testing firm. Use /cso as a first pass to catch
low-hanging fruit between professional audits.
