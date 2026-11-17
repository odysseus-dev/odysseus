# Wave 3-S delivery record

Branch: `feature/runtime-containment`. The final production/delivery commit
contains namespace-init verification, this record and validation evidence;
its exact HEAD is in the delivery message. All commits are local. No push,
PR, merge into lab, branch switch,
reset, rebase, merge abort, cleanup, or other Odysseus worktree mutation occurred.

## Reconciliation

| Revision | Exact commit |
| --- | --- |
| Original containment head | `8e101fdcb8e775105bd4297298be580988bc7ad0` |
| Frozen integration lab | `1e3c50d2dd66484dd515c8caff3614e4ee9cea20` |
| Merge base | `d6c3c98c75e03f70c05ebe4058c6fa12e0395f62` |
| Reconciliation checkpoint | `083a573f7eab63d014331e669178cc367c22a2c8` |

The checkpoint has exactly the original containment head and frozen lab as its
two parents. The in-progress merge was recovered, not restarted. Its only
unmerged path was `website/configuration-reference.md`. All three conflict
stages were inspected; regenerating the reference from the merged sources
preserved containment references and newer lab references together.

Automatic merges of `src/agent_tools/subprocess_tools.py`,
`src/tool_execution.py`, and `tests/test_agent_bash_windows.py` preserved the
Windows Bash environment/cwd/capture contract and authority before dispatch.
The checkpoint also corrected two test assumptions: exact result equality after
adding containment metadata, and an approval-test database stub that needed to
be isolated to that test. Reconciliation validation passed 1,224 tests before
the merge was committed.

RequestAuthority, SemanticIntent, ExactOperation, OperationGrant, TurnContract,
approval policy, and trusted/untrusted request boundaries were preserved.
Since reconciliation, `src/agent_runtime/authority.py`, `src/turn_contract.py`,
and `src/tool_approvals.py` have no changes. The edits to tool execution pass the
existing trusted environment into the contained background launcher and report
its refusal; authority evaluation and background authority sealing retain their
original ordering and owner.

## Subsequent commits

| Commit | Change |
| --- | --- |
| `5bb1326183306e8341d3ca1e6e6f31e4bf9cb0b3` | ODY-152: shared native execution, capture, persistence and teardown |
| `765d79cadf3113e973048ff2e04b0c51d64a88b6` | ODY-143: unconditional native Python containment |
| `f48931407a81bac138cd231d95b95ec0b326ad5b` | Correct the Python namespace test's outside-sibling fixture |
| `127f9b0836456cd95ac8fe4bd5a7ee0c238d8f0d` | ODY-145: contained detached Bash supervisor |
| `f63d333a61404656885be9546e5102f46c248b1c` | ODY-147: retire automatic tmux sessions and reap verified legacy sessions |
| `929987dde7920afb90f0590c24474ae3fa2b4e58` | ODY-150: replace pane capture with bounded, explicit output capture |
| `865968c8d5c0ff72c3faeeaa993705064dca33d9` | ODY-141 LAST: functional namespaces, readiness, cancellation and enforcement |
| `a655abf69839f5a83f14bd48675a9fb178a9b028` | Release and report a background supervisor's failed initialization |
| Commit containing this record | Verify namespace-init death, pin the probed binary, make completed release idempotent, and record final validation |

## Item status

| Item | Status and evidence |
| --- | --- |
| ODY-152 | Implemented. Native tools, detached jobs and compatibility callers use shared containment/teardown; transactional stores preserve concurrent job receipts. |
| ODY-143 | Implemented. Every native Python execution takes the shared boundary, independent of source content. Final-expression output and configured imports remain supported. |
| ODY-145 | Implemented. `#!bg` acquires the same required dimensions before supervisor launch; the supervisor receives the command only after durable ownership/job recording. |
| ODY-147 | Implemented. Chat IDs no longer create tmux shells. Legacy cleanup checks launcher, runtime HOME, session generation, server/pane lineage and start tokens. Ambiguous sessions remain unsignalled and reported. |
| ODY-150 | Implemented. Native Bash no longer reads a 2,000-line pane. A 3,002-line result is complete; actual byte/presentation truncation has metadata and a visible notice. |
| ODY-141 | Implemented last. Shipped mode is enforcing. Missing required dimensions or failed namespace initialization refuse execution deterministically. No tool/configuration host-access mode was introduced. |

## Final containment architecture

`agent_spec` fixes the required dimensions from trusted runtime configuration;
tool text cannot weaken them. `acquire` selects capabilities without examining
the command. Installed bubblewrap must pass a functional PID/mount namespace
probe. Launch uses the absolute trusted binary path, so the execution environment
cannot substitute a workspace binary through PATH. `run` checks the declared mechanism's dimensions again, establishes the
namespace, and consumes a private readiness receipt before acknowledging the
trusted wrapper and starting model code. Bind/setup failure cannot produce a
successful containment result.

The shared bubblewrap recipe uses a private root, private PID namespace, private
`/proc` and devices, read-only system/interpreter mounts, private `/tmp`, and
writable workspace mounts. Extras are mounted before the workspace, so a
read-only ancestor cannot hide its writable workspace bind. Active Python
environments under `/home` are bound explicitly rather than assumed visible.
The compatibility namespace builder also uses this shared recipe.

Spawn is shielded until its process handle is recovered. Timeout, initialization
failure, clean exit and cancellation converge on shared teardown. Repeated
cancellation cannot interrupt TERM, bounded wait, KILL and death verification.
Bubblewrap's separate info pipe records the namespace's PID 1 before model
execution starts. Linux held owners and namespace init use pidfds when available.
Release verifies death of both, including init's kernel cleanup of descendants
that used `setsid()` or double-fork/session escape. Outer-owner exit alone cannot
claim whole-tree death. The receipt retains a live/unverifiable init after failed
signals; recovered teardown validates its start identity before signalling it.
Completed release is idempotent and cannot signal a reused PID; a released grant
cannot execute again. The namespace target uses the same escalating teardown
primitive, not a second escalation implementation.

Detached jobs run a trusted supervisor, not model code outside the boundary.
Its child executes through `containment.run`; completion metadata is published
before the exit receipt. Failed log initialization releases an unstarted grant
and still publishes failure metadata when those destinations are available.
An owned live supervisor remains responsible across server restart; killing a
job validates ownership and checks actual teardown before claiming it was killed.

Process ownership compares PID plus start identity. Linux tokens now include
boot identity, preventing a receipt from matching the same start tick after a
reboot. Recovered teardown validates identity and the recorded PGID before
signals, including again before escalation. EPERM means unknown/live, never
verified death. A gone leader with a populated but unowned group is retained as
a failed cleanup rather than signalled. Foreign/unverifiable receipts remain
visible. JSON read/modify/write operations are serialized across processes.

`src/path_confinement.py` remains the centralized canonical path boundary for
in-process tools. It was preserved rather than replaced by a second policy.

## Explicit dimensions

| Dimension | Native contract |
| --- | --- |
| Filesystem | Required. Functional mount namespace and the trusted workspace/mount recipe. No alias-rewrite fallback in shipped enforcement. |
| Process tree | Required. Private PID namespace and parent-death semantics. Process groups and Windows taskkill do **not** advertise this dimension. |
| Wall clock | Required. Startup/readiness, stdin backpressure and child waiting share the execution timeout; teardown then has bounded escalation waits. |
| Network | Inherited by default, explicitly reported, not isolated. Explicit `none` requests add a real network namespace or refuse at initialization. Loopback sidecars remain reachable by default. |
| Memory | Optional existing Linux RLIMIT_AS hook when the requested hard limit can be applied. No generic resource authority was added. |
| Process count | Optional existing RLIMIT_NPROC hook where supported and not root. This is a user-level limit, not a per-grant quota. |
| Output | Bounded bytes per stream, fully drained to avoid pipe deadlock; UTF-8 decoding spans chunks. Truncation is visible and reported. Presentation caps also carry a notice. |

## Production and test inventory

Production changes after the reconciliation checkpoint:

```text
core/atomic_io.py
core/platform_compat.py
src/agent_tools/bg_job_tools.py
src/agent_tools/subprocess_tools.py
src/bg_jobs.py
src/containment.py
src/containment_worker.py
src/process_ownership.py
src/process_reaper.py
src/tool_execution.py
website/configuration-reference.md
```

Tests changed or added after reconciliation:

```text
tests/containment_helpers.py
tests/test_agent_bash_tmux_env.py
tests/test_agent_bash_windows.py
tests/test_agent_tmux_retirement.py
tests/test_background_containment.py
tests/test_bg_job_tools.py
tests/test_containment_contract.py
tests/test_containment_enforcement.py
tests/test_containment_process_tree.py
tests/test_execution_filesystem_boundary.py
tests/test_native_execution_containment.py
tests/test_orphan_reaping.py
tests/test_process_ownership.py
tests/test_workspace_artifact_tool_floor.py
tests/test_workspace_confine.py
```

The reconciliation commit additionally imports the frozen lab's production/test
changes, including its authority and PTY changes; these are distinct from the
Wave 3-S edits above. `git diff --name-only
8e101fdcb8e775105bd4297298be580988bc7ad0
083a573f7eab63d014331e669178cc367c22a2c8` gives that exact inventory.
The only additional test edits made while reconciling were the Windows result
assertion and `tests/test_tool_approvals.py`'s isolated stub.

## Validation

| Check | Result |
| --- | --- |
| Reconciliation overlap | 1,224 passed |
| ODY-152 focused | 193 passed, 2 skipped |
| ODY-143 focused, corrected sibling fixture | 186 passed |
| ODY-145 focused | 205 passed, 1 skipped |
| ODY-147 focused, including private real tmux server | 71 passed |
| ODY-150 focused | 64 passed |
| ODY-141 focused | 306 passed, 1 skipped |
| Final containment/path/background/authority/PTY/Windows overlap | 657 passed, 2 skipped |
| Supervisor follow-up plus containment/authority/bridge/PTY/Windows tests | 426 passed, 1 skipped |
| Namespace-init ownership/teardown follow-up | 626 passed, 2 skipped |
| Final delivery containment/background/authority/turn-contract/PTY/Windows overlap | 1,608 passed, 2 skipped |
| Full Python suite, single completed run | 11,727 passed; 118 failed; 8 errors; 68 skipped; 2 xfailed; 6 subtests passed; 182 warnings |
| Exact failed/error nodes after environment repair | All 126 passed; 4 deprecation warnings |
| `compileall app.py core routes src tests` | Passed, including final production revision |
| JS/MJS syntax | Not applicable: no JS/MJS changed from the original containment head; affected browser tests were exercised by targeted recovery. |
| Whitespace, conflict markers and unmerged paths | Checked at reconciliation and delivery; no remaining conflict markers or unmerged paths. Captured log trailing whitespace normalized for the final diff check. |

Counts overlap and must not be summed. The initial system-Python full attempt
stopped at collection with 16 missing-dependency errors and ran no tests. It is
preserved as `validation/wave-3-s-full-collection.txt`. An isolated ignored
`.venv` with system packages was created in this worktree. Missing test/runtime
dependencies from `requirements.txt` were installed there; `npm ci` used the
existing lockfile in this worktree. No package manifest or lockfile was changed.

The completed full run is preserved as `validation/wave-3-s-full.txt`; it was
**not green**. Its failures included missing bcrypt/calendar/cron/PDF-rendering
dependencies, import mocks following failed ORM pre-import, and absent Node
test packages. Repairing those dependencies and executing exactly its 126
failed/error node IDs produced 126 passes. The full suite was not repeated, in
accordance with the one-run instruction. This proves targeted recovery, not a
new all-green full run in the repaired environment. The final supervisor and
namespace-init fixes were validated by focused follow-ups after that full run.

Focused commands and summaries are retained under `validation/wave-3-s-*`.
Real tests cover private PID namespaces, a hidden host sibling, sidecar
connectivity, explicit network isolation or deterministic refusal, escaped
session death on timeout and clean parent exit, startup failure, stdin closure,
cancellation during spawn, repeated cancellation during escalation, denied
namespace-init signals after owner death, recovered/reused init identities,
idempotent release, the old PATH substitution and its pinned-path fix, concurrent
job recording, server restart ownership, verified legacy tmux cleanup and
output above 2,000 lines. Existing request-authority and #44/#45 regression
tests passed in the overlap runs.

## Limits, concerns and independent review

No unresolved P0/P1 was observed in the tested Wave 3-S native execution paths.
The implementation and focused Wave 3-S validation are complete. The original
full-run failure result remains part of the delivery evidence.

Platform support is deliberately truthful. Native required containment refuses
on macOS/Windows without a suitable mechanism and on Docker/Linux where
bubblewrap is missing or namespace creation is blocked. Windows Bash contract
tests used platform simulation; no real Windows/macOS machine was validated.
Installing bubblewrap alone does not establish Docker namespace support.
Network egress/LAN access remains inherited by default. Existing externally
owned Wave 2 bridges are not attested as locally contained by this work.

P2 follow-up concerns: independently validate the entire suite in the repaired
environment/CI; adversarially review identity/token and PGID races in recovered
or legacy processes that lack a retained kernel handle; inspect migration of
older identity receipts and ambiguous legacy sessions. Token granularity remains
finite (Linux clock ticks, macOS seconds); boot identity removes cross-boot
matches, not every inspection-to-signal race. Failed/unverifiable receipts are
kept visible rather than expired as if teardown succeeded. Remote bridge
containment claims require an independent assessment of the remote owner.

Maestrum was used for bounded read review. An earlier audit identified the
functional namespace, session escape and cancellation gaps that were verified
and addressed. Its suggestion to signal a group after losing leader identity
was rejected; retaining uncertain receipts is deliberate. Its store-lock claim
did not account for the current transactional writer decorators. The final
review of `865968c8d5c0ff72c3faeeaa993705064dca33d9` failed before any worker ran
because Maestrum placement selected an unrecognized model. The current
orchestrate-work skill assigns placement/retries to Maestrum and directs failed
work to targeted local inspection; no native worker fallback was used. Final
independent adversarial review remains outstanding, especially for detached
supervisor cancellation and recovered ownership under hostile timing.

Work stops at Wave 3-S. No subsequent authority, provenance/egress, browser,
generic lifecycle or decomposition wave was started.
