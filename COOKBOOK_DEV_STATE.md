# Cookbook reliability development

Independent NTFS clone: `F:\Sol-Workspaces\odysseus-cookbook`.
Base production revision: `4f5b68d0b7fb99d1dbadaad7cf74a5a14a0f2b43`.
Branch: `fix/cookbook-windows-authoritative`.

The user explicitly approved this exact checkout and its workspace grant.
The official roots command preserves the four pre-existing grants; the new
checkout is an independent repository with no alternates and pack link count 1.
This document's file-tool write exercises the effective grant before source edits.

## Retained regression evidence

Task `cookbook-b167833a` requests repository
`google/gemma-3-1b-it-qat-q4_0-gguf` with include `*Q4_K_M*`.
Production state records `done`, output empty. The retained 374-byte log has
valid UTF-8, actual ANSI escapes, `Fetching 0 files`, and `DOWNLOAD_OK`.
Read-only cache inventory found only `refs/main` (40 bytes), no GGUF.
No model download is needed to reproduce this failure.

## Boundaries

No production edits, deployment, restarts, pushes, GPU/model loading or large
downloads. Sonnet's checkout and work are untouched. Use focused tests and an
isolated loopback UI preview with separate data, logs and browser session.

## Corrective milestone verified

- Windows now enters the same durable outcome handling as other platforms.
  Success candidates are verified against the requested cache pattern after
  every platform-specific liveness branch; generic exit 0 cannot bypass it.
  Nonzero matched files are required. Terminal outcomes are atomically saved,
  with compare-and-set protection against a concurrent cancellation.
- Frontend status reconciliation no longer promotes DOWNLOAD_OK over a backend
  error. Download cards use this authoritative poll instead of a competing
  pane-based completion classifier. Server-resolved tasks cannot self-resurrect.
- Native PowerShell capture explicitly reads and emits UTF-8. Live snapshots
  use the same display-only ANSI cleaner as restored/finished cards. Raw ANSI
  remains in diagnostics (credentials still redacted).
- API tails are redacted before comparing to stored tails, preventing duplicate
  log appends. Only the two exact credential-free runner token-status messages
  bypass the generic value redactor; actual token values remain redacted.
- The isolated browser run discovered a new-task race: the first poll can run
  before the debounced state POST, see no tasks and stop the monitor. It now
  keeps polling while a local task is still active.

### Execution evidence

Focused tests use `F:/Odysseus/venv/Scripts/python.exe -m pytest` in this clone.
The initial Gemma replay failed in all four variants before implementation:
no persistence without a generic exit marker; false success with exit 0.
Zero-byte matched-file variants also failed before tightening the cache probe.
Native PowerShell capture passed with the retained UTF-8 em dash/checkmark and
unmodified Windows path. The empty-initial-poll regression failed before the
monitor fix, then passed.

The focused group passed 99 tests before the final monitor regression was added;
the final frontend file passed 10 tests afterward. Existing asyncio/SQLAlchemy
warnings and an untouched legacy test's invalid escape warning remain.
LongCat's broader dependency/installer evidence is reused: those files are not
changed. No full suite, GPU test, install or model download was run.

`node tests/manual/cookbook_ui_verify.mjs` passed against the loopback lab at
7012, using an independent headless Edge profile, actual Cookbook modules and
real state/status/cache routes. The transfers are EXPLICITLY LABELLED FIXTURES,
not real HuggingFace downloads: failure replays the retained log byte-for-byte;
success creates a 16-byte file for existence/cache-invalidation testing only,
not a valid inference model. Verified: automatic Active selection/expansion,
35% then 70% progress, error plus visible diagnosis without clicks, clean UTF-8
and ANSI-free display with raw ANSI preserved, successful completion, manual
collapse preserved through polls, Launch gains the fixture without reload,
and refresh restores error/success statuses. Browser error arrays were empty.

Evidence (ignored, local only):
`.odysseus-dev/cookbook-preview/evidence/ui-results.json`,
`gemma-error-clean-utf8.png`, `launch-fixture-appears-without-refresh.png`.

The browser-use daemon reset its tab during the first verification attempt;
the direct isolated Edge/CDP run above replaced it. This was not an app defect.

## Next milestone

Continue model-serving lifecycle work separately from the corrective commit.
Preserve existing discovery/install selection and endpoint contracts; no model
loads, runtime downloads or production actions without separate approval.

## Standalone Windows restart infrastructure — 2026-11-16

Branch `feature/windows-cooperative-restart` starts directly at production
`164861eff5aa111f894bcc0eb83f002f81323e11`, excluding model-serving lifecycle.
Development only: production code, services, task/VBScript and grants unchanged.

Implemented protected local Windows named-pipe control (same-user SID checks,
kernel peer PID/creation identity and nonce HMAC), guarded cooperative Uvicorn
shutdown, cross-process operation lock, RunOnly launcher entry before all
maintenance, exact new-app listener/identity/health verification and preserved
gateway/Chroma identity/health verification. Busy/unknown jobs, leases, live or
invalid PID receipts and unaccounted app descendants block stop. No force or
console-signal fallback exists in operational code. Timeout reports failure and
does not spawn a replacement while the previous process remains alive.

Eleven unique focused checks passed across incremental runs. The real Windows
rehearsal used PowerShell, the actual venv redirector and Uvicorn, native ACLs and
named pipes, a gateway child UNDER the app, persistent direct gateway TCP, and
an independent Chroma HTTP stand-in. Guards, wrong key/identity, unknown workers,
shutdown timeout without force, and shared-service preservation were exercised.
Evidence: `.odysseus-dev/windows-restart-evidence.json` with JUnit/proof paths.
Final health-input guard followed by its negative case and another real restart;
unchanged earlier cases reused. No full repository/UI suite or inference run.

A separate legacy LAB rehearsal pinned exact handles and stopped only its own
old PowerShell/redirector/app chain, supervisor first to avoid Chroma cleanup;
gateway/Chroma survived and the new RunOnly launcher became controllable. An
already-exited handle initially returned WinError 5; the corrected rehearsal
checks handle exit state before termination and passed. No broader permission
or kill was introduced. This exception is NOT in the operational CLI and was
not executed against production.

First-activation gates and limitations: `docs/windows-cooperative-restart.md`.
The old live process cannot gain IPC from a merge. Production bootstrap still
needs separate approval and an external zero-turn/job maintenance window.
The existing autostart task must explicitly select RunOnly after a backed-up,
reviewed configuration change; its old default installer path is unchanged.
Direct gateway connections survive where independent of the app; proxied app
connections reconnect. No global gateway admission fence is claimed.

Integration candidate is this standalone restart commit on the current
production base; do not merge the model-serving branch with it. Forward revert
is the rollback strategy; never restore old task data. Real production startup,
Gemma post-activation smoke, real Chroma/Hermes boot and other-user logon remain
unperformed, not reported as tested.

Final hardening: atomically replace the private key file object rather than
overwriting an old explicit permissive ACL; a real permissive-file regression
was reproduced and passed after correction. Retry only pre-auth pipe opening
across observed transient instance gaps, never an action already sent. Reject
non-finite/invalid deadlines before I/O. The real authenticated restart passed
again after these changes. Earlier unchanged cases are reused, not rerun broadly.

Final standalone integration branch:
`integration/windows-restart-ready-164861ef`, a single consolidated infrastructure
commit directly on production `164861ef`. It supersedes prototype `0fbeab22`;
do not integrate the prototype. Development history and the separate model
readiness increment `de10bf76` are retained on their own branches, not merged.

## Code-only integration and real-health follow-up

With explicit approval, production was clean at `164861ef` and fast-forwarded
only to `3ce9bff2c1d14339ca5d0474904c94c0e7ced436`; no rebase was required.
Private source/config/state/task/wrapper backup and forward-only inverse-tree
verification are in `.odysseus-dev/windows-restart-deploy-20261116-121811`.
The original app/gateway/Chroma identities remain unchanged. All real public
JSON health endpoints responded correctly. Task XML, wrapper and configuration
hashes remained unchanged; actual private backup ACLs were read back and permit
only the owner and SYSTEM. No activation, autostart mutation, push or release.

Actual guards refuse activation: an active turn; missing `data/bg_jobs.json`;
two foreign/recycled Cookbook PID receipts (Gemma's PID is now Discord); and
unaccounted app descendants. No receipt/registry/worker was modified. Live path
bindings need independent verification; missing evidence is not an idle proof.
The staged permanent-startup proposal inserts only RunOnly in the existing
UTF-16 wrapper and leaves all task XML/settings/account/trigger fields unchanged.

Final real probes caught a missed contract: app `/health` is a login redirect,
gateway `/health` is SPA HTML, and their actual health API is `/api/health`.
The installed base would fail app-health verification and falsely accept a
gateway SPA page. This new DEVELOPMENT-ONLY follow-up fixes paths, bounded JSON
schemas and role-preserving protected-service rechecks. It requires its own
code-integration approval before first activation. No guard was relaxed.

New regression first failed on the real redirect behavior, then on HTML/invalid
role responses. Fourteen latest unique focused real-HTTP cases pass across
incremental runs: `.odysseus-dev/restart-health-evidence.json`. The corrected
predicate also passed read-only checks of the real listeners. Accepted 11
supervised process cases were not repeated; changed stand-in payloads match
real contracts, but no new process rehearsal/full real-app boot is claimed.

Lifecycle remains separate: `4c6636c499cef5a845fa7f9d3e56b485a2a05456` adds
receipt-bound direct-child exit diagnostics; 45 focused exit/control/API checks
passed. It is neither merged nor a dependency of this health correction.
Public activation/recovery plan: `.odysseus-dev/WINDOWS_FIRST_ACTIVATION_APPROVAL.md`.

## Health correction integrated; inventory reconciliation development

With separate explicit approval, clean production `3ce9bff2` was fast-forwarded
only to `a86c1c7b346256354d49520e63ea705525f47748`. Fresh private backup and
verified inverse-tree rollback: `.odysseus-dev/windows-restart-deploy-20261116-165732`.
Task XML, live wrapper and configuration hashes stayed unchanged. No restart,
autostart change, process termination, push or release; 14 HTTP checks reused.

Actual DATA_DIR was proved from the live app's open `logs/app.log` handle name,
not environment/credential reads or an assumed default. The job store really
is `F:\Odysseus\data\bg_jobs.json`. It is lazy-created; absent job/containment/
publication artifacts plus a complete OS supervisor scan establish pre-first-
launch idle state. A new observational candidate handles that strict case without
inventing a registry and retains unknown/partial/racing evidence as blocking.

Fresh receipt audit found 13 gone and two obsolete/foreign incarnations. The
candidate rereads paired files and kernel identities, never deletes history,
changes recorded outcomes or signals current PID occupants. All original
receipts/task bytes were verified unchanged. Seventeen focused guard checks pass.

All 19 non-gateway descendants have source-traced app-private MCP ownership
(four built-in Python services and Playwright, plus wrappers). None was whitelisted
or stopped. Their in-flight activity is NOT authoritatively observable through
the current interfaces: `/api/busy` is image-only and authenticated. The request
for a normal signed-in read timed out; no authentication bypass was attempted.
The full descendant guard and the current active-turn guard remain blocking.

Details: `docs/windows-activation-inventory.md`; actual evidence under the fresh
backup directory. This new guard candidate is DEVELOPMENT ONLY and needs separate
integration approval. Do not request a legacy transition until complete native
MCP quiescence is established; known purpose/low CPU/ping is not an idle proof.
