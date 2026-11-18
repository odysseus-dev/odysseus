# Durable native worker index and producer admission

This is a companion to existing Cookbook tasks, background jobs and containment
publications, not another task scheduler or a source of process-signal authority.

## Storage and observations

`src/native_workers.py` stores `native_workers/index.json` and its initialization
witness under the existing durable `process_resources` application-data directory.
A producer records an intended generation before creating an OS process, then
binds the returned PID and `process_ownership` creation token. No command,
environment, credential or task-output text is stored in this index.

Existing task/job IDs remain the source linkage. Actual direct-child wait results
record completed/failed/cancelled process outcomes. Cancellation intent does not
claim exit or signal anything. Cookbook cancellation and background-job kill
retain their existing authorization/identity gates and link metadata only after
those boundaries. History is retained, not pruned to make a guard pass.

A reconstructed reader observes a live worker with a dead/replaced manager as
orphaned. A lost identity, recycled slot, unreadable/corrupt inventory, missing
previously initialized index, or unobserved exit is unknown, not success or idle.
The separate witness prevents silently replacing a lost index with an empty one.

Completed here means the recorded **direct process exited**, not that every
possible descendant exited or a model download/inference succeeded. Existing
containment, requested-file verification and restart descendant checks remain
necessary. The index always reports `signal_authorized: false` and
`legacy_inventory_verified: false`.

## Producer coverage

The adapters cover typed llama.cpp/Ollama serving and native Cookbook detached downloads/servers, Windows
detached shell, shell execution/PTY, local pip installation, Bash compatibility,
background supervisors and containment execution. Background-job records retain
the generation for cancellation linkage. Shell completion no longer deletes its
log and exit diagnostic, although compatibility artifacts still use their old
session directory. Deleting Windows Temp does not delete this durable index.

POSIX direct/PTY helpers share the adapters; no native Linux host was exercised.
Remote execution and tmux daemon/pane lifetime are not certified by observing a
local SSH/tmux CLI process. New plugins or future model-serving launch paths must
use the producer adapter rather than treating an HTTP endpoint or arbitrary PID
as enrollment. Typed serving records its own existing session ID before invoking
its runtime factory and links cancellation only after its existing receipt and
current PID/creation-identity control checks.

Controller reconstruction without a child-exit observer cannot recover an exit
code. After an authorized stop confirms direct-process death, the exact linked
identity can instead record `cancelled`, `completion_basis: verified-stop`, and
`exit_code: null`. This does not fabricate a return code or grant signal rights.
Live, unverifiable and mismatched identities cannot use that transition. An
actual child wait may later provide the stronger `child-wait` numeric evidence.
Windows identity reads also check liveness: a retained creation time on an open
handle to an exited process is not a running worker or a live fence owner.

## Admission and recovery

The persisted fence serializes with sync launch/publication across processes.
Async admission first records intent; a spawn accepted before fencing remains
visible until it binds/exits. It is never dropped merely because its caller was
cancelled. Pending, running, orphaned or unknown records block shutdown.

The Windows coordinator fences producers before evaluating mutable guards and
before MCP drain. It pauses HTTP admission and resumes its own producer fence on
refusal. An observed deadline still prevents a late shutdown commit; no force
fallback is added. RunOnly's controlled startup initializes a genuine producer
epoch before admitting work.

Ordinary app startup also awaits `initialize_producers` before background work.
Lost initialized inventory, corrupt state or a live fence aborts startup. A
fresh index is not retrospective evidence that pre-instrumentation work is idle.

On new-epoch initialization, a previous fence is retired only when its owner's
PID/creation identity is proven gone or foreign. Live/unknown owners block it.
This releases the dead admission lease, **not its workers**: their identity and
history remain for reconciliation. Merely reading diagnostics never initializes
or repairs evidence.

Legacy Temp receipt guards are retained. A new registry cannot retrospectively
prove that pre-instrumentation workers were idle. Missing legacy evidence is not
waived, and no production launcher or first-transition procedure is activated.

## Read-only frontend contract

Admin `GET /api/cookbook/workers/status` returns schema version 1, `inventory`,
the actual process-local `mcp` activity snapshot, and `session_directory`.
It does not construct an MCP manager, connect peers, create inventory or grant
control. Consumers must distinguish missing/unknown evidence from an empty known
set and must not infer tree termination from a terminal direct-process row.

Task-status rows additionally expose `log_availability` (`available` means
nonempty observed/persisted output) and `log_unavailable_reason`. Missing output
stays empty; the explanation belongs in the UI, not fabricated runner output.
No historical archive is restored. Frontend rendering belongs to its owner.

## Windows interpreter identity correction

Windows venv `Scripts/python.exe` may be a redirector whose PID differs from the
interpreter running the trusted supervisor. That broke the detached producer's
self-identity check. `core.platform_compat.native_python_command` resolves only
the current `sys.executable` to its known base executable on Windows and sets
`__PYVENV_LAUNCHER__` to preserve the same venv prefix. Other interpreters and
POSIX commands are unchanged. Real Windows execution verifies PID equality,
preserved venv prefix and successful background output/teardown; no new runtime
or package is installed.

The older background fixture forced POSIX process-group selection on Windows.
Its failure was reproduced on the unmodified baseline. The fixture now selects
the actual Windows mechanism and still asserts that it does **not** establish
filesystem/process-tree containment in report-only mode. No production sandbox
requirement was weakened.
