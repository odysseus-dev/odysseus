# Windows activation inventory — development reconciliation candidate

Production health-only integration is complete at
`a86c1c7b346256354d49520e63ea705525f47748`. No production restart, process
termination, autostart/task edit, push or release was performed.

## Authoritative path and native background-job evidence

The live app's existing disk handle resolves to `F:\Odysseus\data\logs\app.log`.
The inspector queried only the filename, not file contents, process memory or
environment variables. `app.py` builds that path from `core.constants.DATA_DIR`;
`core/constants.py` reexports `src.constants`, the same source used by `bg_jobs`.
Thus this instance's job store really is `F:\Odysseus\data\bg_jobs.json` and the
job directory is `F:\Odysseus\data\bg_jobs`. The default path was not wrong.

The missing registry is expected before the first native background launch:
`src/bg_jobs.py:launch` creates the job directory before acquiring containment,
then persists the job/publication before releasing the worker's stdin execution
barrier. `_load` tolerates an absent store; `refresh` does not create one merely
by polling. Pruning removes job files, not the job directory.

This installation has no job directory, containment store or process-publication
JSON. `process_resources` contains only its existing `publication.lock`. A real
OS query had complete visibility of Python command lines and found no native
background supervisor. These combined observations support uninitialized/idle;
missing JSON by itself does not.

The development guard recognizes this state only when all artifact checks and
an independent complete supervisor scan agree, and rechecks artifacts after the
scan. Partial publication, an existing job directory, any containment state,
unknown process visibility, a live supervisor, an artifact race or an access
error still blocks. It never creates `{}` or calls `bg_jobs.refresh` (which can
reap/prune jobs and is not a read-only inventory API).

## Obsolete worker receipts

All 15 original PID receipts and their available creation-token sidecars remain
unchanged: 13 original processes are gone and two have foreign/recycled creation
identities. The Gemma receipt now points numerically at Discord; the other points
at a shell in the app's Playwright MCP launch chain. Neither current occupant is
a Cookbook download and neither may be signalled through these old receipts.

The development observer compares fresh creation identities and rereads both
receipt files and process identity before accepting its observation. Matching
live workers, missing/malformed live tokens and changed observations still
block. `obsolete` is an observation, not authority to stop or delete anything.
The audit links the retained Gemma task's existing `done` value without rewriting
it; other task rows were already absent from the current state and their outcomes
are not reconstructed. Original logs/receipts are retained. The known Gemma
completion correction still awaits actual backend activation.

## Descendant ownership versus activity

All 19 non-gateway app descendants were traced to the built-in image, memory,
RAG and email stdio MCP servers, their venv/console wrappers, and the registered
Playwright MCP NPX/shell chain. `src/builtin_mcp.py` specifies those entry points;
`src/mcp_manager.py` owns their private stdio connections. Current creation
identities and same-user ownership were checked. These are app-private MCP
services, not unexplained download workers or independently shared Chroma.
The gateway branch, including its own tools, is separately protected in full.

**This does not prove MCP inactivity.** No process subtree was whitelisted,
detached, killed or discarded. `/api/busy` is authenticated and reports image
operations only; it is not an exhaustive native MCP in-flight inventory. The
current public interfaces do not supply a complete native MCP quiescence proof.
The conservative descendant guard remains blocking. A source merge cannot
retrofit a new inventory endpoint into the old live Python process.

No legacy-transition authorization should be requested while this evidence is
unknown. An external zero-turn maintenance window and a reviewed authoritative
native-MCP quiescence method remain required. Do not equate known service purpose,
zero CPU, an empty pipe, a ping response or an image-only busy counter with idle.

## Verification and boundaries

17 focused receipt/background-inventory guard checks passed. New behaviors were
first observed failing; tests cover matching/foreign/malformed/racing identities,
expected pre-launch state, partial artifacts, live/unknown supervisors, access
failure and publication races. The OS query was also exercised against the real
host. Existing 14 health-contract checks and 11 supervised-process checks were
reused, not repeated. No full repository/UI suite or process rehearsal was run.

Evidence in the authorized checkout:
- `.odysseus-dev/live-data-dir-evidence.json`
- `.odysseus-dev/activation-inventory-final.xml`
- `.odysseus-dev/windows-restart-deploy-20261116-165732/reconciliation-audit.json`
- `.odysseus-dev/windows-restart-deploy-20261116-165732/descendant-ownership.json`

This candidate changes only observational guard logic; it is not integrated or
activated. The installed production guard still has its conservative old
receipt/missing-registry behavior. Actual readiness remains FALSE, both because
this agent turn is active and because complete native-MCP quiescence is unknown.
