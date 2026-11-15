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
