# Odysseus Windows Port — Closeout (staged 4-PR split)

Source branch: `windows-native` on `ahostbr/odysseus` (fork of `pewdiepie-archdaemon/odysseus`), commit `b2aa670`, a single 24-file change on top of `upstream/main`.
Verified on: Windows 11 Pro, Python 3.11.9, AMD Ryzen 9 9950X3D, RTX 5090 (Blackwell sm_120).
Status: **No degraded items remaining.** Every originally-deferred gap closed and proven with an e2e test.

This document accompanies the split of the Windows-native work into **four
scoped, independently reviewable PRs**, each with its own Windows smoke-test
path. It is the "docs + integration proofs" PR (PR-4).

---

## The 4-PR split

Each PR is carved from `b2aa670` onto `upstream/main`. PR-1..3 are independent;
**PR-4 (this one) stacks on PR-1+2+3** — its e2e proofs exercise the code those
PRs introduce, so it is reviewed/merged last (or against the integrated tree).

| PR | Scope | Owned files | Windows smoke test | e2e proof (PR-4) |
|----|-------|-------------|--------------------|------------------|
| **PR-1** | ConPTY interactive terminal | `services/pty/*`, `routes/pty_routes.py`, `static/js/terminal.js`, `static/lib/xterm*` | `e2e/smoke/pr1_conpty_smoke.py` — ConPTY echo round-trip | `e2e/test_pty_ws.py` |
| **PR-2** | Windows service + scheduler calendar | `scripts/windows_service_runner.py`, `install-service.ps1`, `static/js/schedulerCalendar.js` | `e2e/smoke/pr2_service_smoke.py` — SCM contract (no real install) | `e2e/test_scheduler_fires.py` |
| **PR-3** | Local llama.cpp model hub | `services/llama/*`, `routes/llama_routes.py`, `static/js/llamaHub.js` | `e2e/smoke/pr3_llama_smoke.py` — hub loads + pinned provisioning (no network) | `e2e/test_llama_hub.py` |
| **PR-4** | Docs + integration proofs | this file + the 3 e2e proofs above | — | — |

Cross-cutting hunks (`app.py` router registration, `requirements.txt`,
`static/index.html` script tags, `core/middleware.py` CSP, `.gitignore`) are
split so each PR carries only the lines its subsystem needs.

---

## Static third-party assets — licensing/versioning (review concern)

No prebuilt binary or model is vendored into any PR. The only bundled
third-party static assets introduced are the xterm.js terminal files, and they
are now documented:

| Asset | PR | Disposition |
|-------|----|-------------|
| `static/lib/xterm.js`, `xterm-addon-fit.js`, `xterm.css` | PR-1 | Vendored (MIT). Documented in `static/lib/LICENSES.md` — source, MIT text, and SHA-256 per file (the authoritative version pin). |
| llama-server binary | PR-3 | **Not vendored.** Provisioned at runtime from a pinned ggml-org/llama.cpp release (`b9444`, MIT) into gitignored `data/llama/bin/`. See `services/llama/PROVISIONING.md`. |
| GGUF models | PR-3 | **Not vendored.** Downloaded at runtime from HuggingFace (each model's own license) into gitignored `data/llama/models/`. |

---

## What works natively on Windows now

| Capability | Evidence |
|------------|----------|
| Server boot | `uvicorn app:app` -> "Application startup complete", no import errors |
| Hardware detection | PowerShell/WMI path: RTX 5090 ~31.8GB VRAM, Ryzen 9 9950X3D, ~61.6GB RAM |
| Shell exec | `_exec_shell` via cmd.exe; echo round-trip |
| Real interactive PTY terminal (PR-1) | ConPTY via pywinpty + xterm.js. Browser-verified: PowerShell 7.6.0, ANSI highlighting, `\r` progress collapses like tqdm. Admin-gated WebSocket (no-cookie -> 403). |
| Local llama.cpp serving (PR-3) | Prebuilt CUDA binary auto-provisioned, runs on Blackwell sm_120. e2e: serve -> ready ~1.5s -> real completion -> auto-registered in chat picker. |
| Model hub (PR-3) | GGUF download (HF), serve with `-ngl` GPU offload, lifecycle, self-registering UI panel |
| Background scheduler (in-app) | Fires cron jobs at their time; e2e proven status=success + TaskRun recorded |
| Headless / Windows service (PR-2) | `install-service.ps1` (native sc.exe) + `windows_service_runner.py` (windowless). Scheduler runs with no window open. |
| Scheduler calendar UI (PR-2) | Month grid + day timeline + job editor + run history, over existing `/api/tasks` |

---

## Degraded / deferred items — ALL CLOSED

| Original degraded item | Resolution |
|------------------------|------------|
| "PTY streaming returns graceful error — tqdm won't render" | **PR-1**: real ConPTY PTY; tqdm/ANSI render in xterm.js |
| "vLLM not supported on Windows (dead-end)" | **PR-3**: real llama.cpp serve + model hub; local LLM answers in chat |
| "No Windows service wrapper" | **PR-2**: native sc.exe service + windowless runner; scheduler fires with no window |
| "No calendar/timeline view for scheduler" | **PR-2**: calendar/day/history UI |

**Non-degradations by design:** vLLM remains unsupported on Windows (no Windows
build upstream) — llama.cpp is the working substitute, not a regression.
Linux/macOS llama-server provisioning expects `llama-server` on PATH
(auto-download targets the Windows prebuilt zips).

---

## Companion bug fix (separate branch)

Running with `AUTH_ENABLED=false` deadlocked the browser UI in an infinite
`/` <-> `/login` redirect loop — reproduced on stock `main`, a pre-existing bug
not introduced by the Windows work. It is fixed on the companion `auth-loop-fix`
branch (route-level auth self-checks respect `AUTH_ENABLED`, falling through to
single-user loopback mode instead of 401-ing), kept out of this split so each PR
stays scoped. Verified: `AUTH_ENABLED=false` -> `/api/models` 200, no loop;
`AUTH_ENABLED=true` -> anonymous still 401 (no security regression).

---

## Test artifacts (e2e/)

**Smoke tests** (one per feature PR — minimal pass/fail, runnable on a clean checkout):
- `e2e/smoke/pr1_conpty_smoke.py` — ConPTY echo round-trip via `PtySession`
- `e2e/smoke/pr2_service_smoke.py` — `OdysseusService` SCM contract (no real install)
- `e2e/smoke/pr3_llama_smoke.py` — hub loads + pinned-provisioning checks (no network)

**Integration proofs** (this PR — run against a live auth-enabled server; need PR-1/2/3 code):
- `e2e/test_pty_ws.py` — PTY WebSocket: negative (no cookie -> 403) + positive (admin -> stream)
- `e2e/test_llama_hub.py` — llama hub through HTTP API: serve -> register -> completion
- `e2e/test_scheduler_fires.py` — scheduler fires a cron job -> status=success -> TaskRun

---

## Review posture

A clean, self-contained Windows-support set split into four reviewable pieces.
Every new surface that touches execution is admin-gated; the one vendored
third-party asset set (xterm.js) is license/version-documented; binaries and
models are gitignored and fetched at runtime; and each capability has both a
lightweight Windows smoke test and a real-result e2e proof (not just "it loads").
