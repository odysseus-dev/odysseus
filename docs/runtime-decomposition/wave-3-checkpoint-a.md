# Wave 3 Checkpoint A: process and job authority

This checkpoint binds native process creation and background-job operations to
server-owned resources. It consumes the reconciled Wave 5B `ProcessIdentity`
and leaves lifecycle and signalling mechanics unchanged. Browser document
authority remains deferred; no browser session/page adapter is added here.

## Baseline and boundaries

Starting branch: `feature/runtime-resource-authority`.

- HEAD: `d0d1b3697ccd567dad9f812ed9f4f4d4f7d0044f`.
- Tree: `9a8a7fd490d18ab5ad9d627b41ddad81206017f2`.
- Clean worktree, with `4052eecc`, `8ae6ee43` and `c3ad4d0b` as ancestors.
- Unchanged Wave 3 + Wave 5B baseline: 2902 passed, 2 skipped, 2 existing
  xfails across 100 files, using functional bubblewrap.

The new identities add no operations to RequestAuthority or TurnContract.
Transcription, OCR and tasks restrictions remain in force. There is no default
DATA_DIR creation floor, PID grant, job wildcard or automatic descendant grant.
Wave 4 effects, evidence, provenance and egress policy remain outside this
checkpoint. Existing runtime outcome fields continue to report actual execution
and teardown if identity attachment fails after execution.

## Typed contracts

`src/agent_runtime/resources.py` defines three immutable contracts:

| Type | Binding | Source and validation |
| --- | --- | --- |
| `ProcessResource` | Producer namespace, application owner, originating request/thread, one nested Wave 5B `ProcessIdentity`, role, optional job and receipt linkage | Producer observation at spawn, or an already frozen containment lifecycle record. `owned()` and `exited()` validate the OS incarnation; they never establish application ownership. |
| `ProcessLaunchResource` | Native producer, owner/request/thread, server UUID generation, exact normalized tool/input digest, native backend, sealed creation boundary, inherited authority digest | Reservation created during server normalization before spawn. Publication is exclusive for that generation. No PID is predicted or recovered from model text. |
| `BackgroundJobResource` | Exact native store namespace, job ID, launch generation, owner/origin request/thread, containment ID, role-labelled process resources | The native producer registers the frozen supervisor observation before releasing the workload. Store, launch publication, authority sidecar and receipt must agree. |

The admitted process producers are `native:containment` (leader and namespace
init) and `native:bg_jobs` (supervisor). Manager/PTY/service observations are not
silently enrolled; they require their own producer adapter. Leader, supervisor,
namespace init and server manager remain distinct in Wave 5B records. Legacy
flat PID/token fields remain for existing mechanics and are checked against the
nested identity; the new envelope does not duplicate incarnation fields.

`ProcessLaunchScope` binds a native Bash/Python backend, a sealed filesystem
root, required containment dimensions, observed read-only runtime roots,
network selector and maximum runtime. The producer compares its actual spec to
the reservation. Changed roots, broader mounts, longer runtimes and changed
backends fail closed. Credentials and command/environment contents are not
serialized into resource identities.

## Normalization and admission

`src/agent_runtime/process_resources.py` centralizes scope sealing, resolution,
validation, publication and ContextVar binding.

1. RequestAuthority grants the semantic operation and explicitly seals existing
   workspace/backend scope. Without a sealed creation scope, Bash/Python cannot
   fall back to the server's working directory.
2. Launch normalization issues one exact reservation. Job normalization resolves
   the selector only within the immutable set of already admitted jobs.
3. The dispatcher validates the exact resources before the approval claim and
   binds the normalized operation in a ContextVar.
4. Native producers revalidate operation, application binding, roots and spec.
   Native Bash/Python dispatch remains pinned to the native backend and passes
   owner/session context explicitly.
5. Foreground publication precedes containment execution. Resulting process
   envelopes reference the frozen leader/namespace-init records, never a fresh
   capture of their numeric PIDs.
6. Detached launch holds the supervisor on stdin. It observes its incarnation,
   persists job/store/launch/sidecar linkage, then releases the command. The
   worker independently checks those records, the supervisor, receipt and spec.
   Publication failure closes the held worker and uses existing Wave 5B cleanup.

Publication uses the existing atomic file/fsync and store-transaction APIs.
There is no new effect journal or distributed commit protocol. Partial metadata
cannot admit a job or release its workload.

RequestAuthority snapshot version 4 carries explicit process, job and launch
scopes. Older snapshots restore empty scopes; missing identities are never
reconstructed by observing today's processes or jobs.

## Approvals and child ceilings

Proposal capture includes the exact reservation or job resource, including its
nested process, role, producer, ownership, generation and receipt. The approval
digest covers those resources and the existing exact operation/backend binding.
Execution validates before the one-use claim and at producer entry. Restoring an
exact operation restores no general process, job or launch scope. Unsupported
standalone PID controls have no adapter and cannot create an approval identity.

Child process scopes intersect by full identity equality after validating both
parent and child observations. Jobs intersect by full store/ID/generation/
owner/thread/receipt/process equality. Creation scopes may narrow roots, mounts,
runtime or network limits while retaining the backend and parent boundary
requirements. Semantic operation grants are intersected independently. A stale
parent fails before a newly observed child can renew it. Discovering descendants
or siblings adds no authority.

ContextVar binding restores state on success, ordinary exception, cancellation
and nesting. Existing lifecycle tests exercise cancellation during spawn and
repeated cleanup; the new integration test also checks native dispatch context
restoration during cancellation.

## Job history and continuations

`peek()` and resolution do not refresh or reap jobs. Output refresh reconciles
only the selected job. It polls a cached subprocess handle only while the
selected record is running and its frozen start token still verifies as owned;
historical or unverifiable identities cannot poll a replacement handle under
the same numeric PID. Global service refresh still reaps completed handles.
Stop/output/ack
require the caller's exact expected resource and revalidate linkage. Results
can update only an explicit result-field whitelist, never identity, owner,
generation, receipt, PID, command, path or authority fields.

Completed generations remain readable if their lifecycle receipt has been
pruned, provided their application publication and sidecar remain exact.
Completed stop is a no-op and cannot signal a reused PID. Active jobs require
the exact native receipt and live supervisor; an existing receipt with changed
producer/owner/incarnation or external semantics is rejected even for history.

The monitor checks sidecar, launch generation, job resource and session owner
before invoking a continuation and acknowledging that same generation. Missing
legacy sidecars do not acquire authority. Service-owned maintenance/reaping
remains independent of model authority; lookup never invokes it for siblings.
Research records in `background_tool_jobs.py` remain records, not OS processes.

## Reachable production seams

| Production call path | Enforcement or explicit boundary |
| --- | --- |
| `agent_loop` / native executor -> `tool_execution.execute_tool_block` -> `BashTool.execute` / `PythonTool.execute` -> `_run_owned_command` | Exact reservation, native backend pin, explicit owner/session context, sealed spec and pre-execution publication. |
| `execute_tool_block` -> `#!bg` -> `bg_jobs.launch` -> `containment_worker.supervise` | Held release until durable linkage; independent worker validation. |
| Dispatcher -> `ManageBgJobsTool.execute` -> `bg_jobs.get` / `kill` | Exact captured job set/selector, owner/thread binding and revalidation; no implicit list refresh. |
| App startup -> `bg_monitor._loop` -> `_run_followup` / `mark_followed_up` | Exact generation and sidecar/owner/thread validation before continuation and ack. |
| `TaskScheduler._execute_action` -> `action_run_local` / `action_run_script` / local `action_ssh_command` -> `_run_subprocess` | Existing scheduler authority must permit the exact operation; new runner consumes a sealed launch ceiling through containment. Missing workspace/legacy creation scope fails closed. |
| Dispatcher -> Cookbook native tools -> `/api/model/download`, `/api/model/serve`, `/api/cookbook/state`, `/api/cookbook/kill-pid` | Internal native mutation is rejected: UI state/session/PID discovery is not an application process registry. |
| Dispatcher -> `stop_served_model` / `cancel_download` -> `_cookbook_kill_session` | Local targets fail closed before OS discovery, signalling or state changes. |
| Generic `app_api` -> loopback shell/model/Cookbook namespaces | Generic private/owned route admission rejects these process-control namespaces. |
| Direct labelled or unlabelled loopback -> shell native controls / local Cookbook launch/control | Internal markers confer no admin floor. Anonymous/auth-disabled native control fails closed, including missing auth-manager configurations. Authenticated human-admin control remains a separate administrative boundary. |
| App startup -> process reaper / `bg_jobs.refresh` / `disown_unverified` / containment reaping | Existing service maintenance and frozen Wave 5B signal mechanics remain unchanged. |

No production caller of `services/shell/service.py` was found; it is unchanged
and not claimed as covered. Browser lifecycle, research/private browsers and
their producer contracts are unchanged and outside Checkpoint A.

## Unsupported paths and deployment consequences

- Local Cookbook agent launch/control has no trustworthy application registry;
  it is disabled instead of enrolling tmux/PID/UI observations.
- Legacy Cookbook scheduled auto-stop uses the rejected internal shell route
  and cannot silently resume control of editable UI-backed sessions. Its
  absence of a trustworthy producer registry is an explicit remaining gap;
  native background-job and containment reapers continue to work.
- Auth-disabled native shell/Cookbook UI controls are unavailable: an anonymous
  human request cannot be distinguished securely from a workload's loopback
  request. No Origin header, browser key or local address substitutes for
  resource authority.
- Legacy tasks without creation scope and jobs without exact generation/sidecar
  linkage do not gain authority during restoration.
- Raw scheduled SSH execution fails closed until an exact external backend
  producer exists. Existing remote Cookbook routes/MCP/bridges remain external;
  a local SSH client is never enrolled as its remote workload.
- Standalone existing-process/PTY/manager control, new producer registration,
  browser session/page/document authority and general outbound-effect policy
  are not implemented by this slice.

## Control state and adversarial verification

`PROCESS_RESOURCES_DIR`, the active launch directory, job store/sidecars and
containment records are protected by central filesystem resource resolution.
Native writable launch boundaries containing control state or existing
symlink/hardlink aliases are rejected. Tests cover direct access, symlinks and
hardlinks to launch records, job stores, authority sidecars and receipt files.
These are pathname/inode observations. They do not claim race freedom against
concurrent link replacement after validation; Wave 3-S containment mechanics
have not been redesigned.

The three new test files are `test_process_resource_identity.py`,
`test_background_resource_identity.py` and `test_runtime_resource_integration.py`.
They cover PID reuse/unverifiable or malformed observations, role/receipt/owner/
request/thread substitution, generation replacement, publication failure and
held release, immutable result fields, historical reads, sidecar mismatch,
side-effect-free lookup, exact approval first use/replay/restoration, child
ceilings, context restoration, external refusal, native routing, scheduler and
anonymous/internal loopback bypasses, and TurnContract exclusions.

The integrated manifest `wave-3-checkpoint-a-tests.txt` contains 145 files,
including every file in the previous exact 88-file Wave 3 gate. It adds relevant
Wave 5B lifecycle, shell, scheduler, Cookbook, background, browser transport and
research fallback regressions. Run in an environment with functional bubblewrap:

```sh
python3 -m pytest -q -rs $(cat docs/runtime-decomposition/wave-3-checkpoint-a-tests.txt)
python3 -m compileall -q app.py core routes services src tests scripts
git diff --check
git grep -n -E '^(<<<<<<< |=======$|>>>>>>> )' || true
git ls-files -u
```

The final pre-commit gate passed 387 focused tests and 3364 integrated tests,
with 3 platform skips and 2 existing xfails. The focused gate spans 12 files;
the integrated gate spans the 145-file manifest. Validation used
`/tmp/odysseus-wave3-validation/bin/python` with functional bubblewrap.
Compileall, diff whitespace, conflict-marker and unmerged-index gates passed.
The post-commit integrated result is recorded in the final checkpoint report.
Final adversarial review found a numeric-PID-only cached-handle lookup in that
commit. A follow-up patch adds frozen-token validation and four PID-reuse/
unverifiable history regressions, plus a service-cleanup regression. The patched
focused gate passes 392 tests; the patched 145-file integrated gate passes 3369
tests, with the same 3 platform skips and 2 existing xfails. Static gates pass.
Platform skips remain
explicit: `/tmp` is not a symlink, RLIMIT_AS can be lowered on this host, and the
Windows-specific Ollama startup guard is not applicable on Linux. No missing
browser dependency is converted into a passing test.

## Remaining review concerns

No known P0 admission bypass remains in the supported process/job paths.
P1 compatibility gaps are the deliberately unsupported local Cookbook registry
and auth-disabled native administration, plus legacy/unscoped scheduled work.
P2 concerns are linear workspace/control-file scans and retention of private
launch publications beyond job/receipt retention; a future server-owned
maintenance policy must preserve exact historical linkage. Existing filesystem
observation races and outbound-effect boundaries remain explicit limitations.
Browser authority still requires the independent producer-contract lane.
