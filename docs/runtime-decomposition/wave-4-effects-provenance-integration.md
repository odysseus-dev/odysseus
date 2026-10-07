# Wave 4 effects, provenance, freshness and truthful completion

Branch: `feature/effects-provenance-wave4`.
Exact base: Wave 3 PR #60 head `80a962d96af5f85c785bd517ae6af8e90a8b0d38`
(tree `bba4adfc9ff1628d96daeee57640be46a3f5d270`), clean at admission.
Historical references: foundation `9012e208` (parent `1e3c50d2`),
`wave-4-effects-provenance-foundation.md` and
`wave-4-canonical-refresh-a80c164d.md` in the old worktree (read only).

## Foundation decision: recreated, not cherry-picked

`9012e208` was **not** cherry-picked. Its semantics were sound, but its types
encoded assumptions that final Wave 3 made wrong:

| Historical type | Problem against final Wave 3 | Recreated as |
| --- | --- | --- |
| `resource_keys: tuple[str, ...]` | Opaque string tokens; Wave 3 now has typed exact identities. Strings would make names/paths authority-shaped. | `ResourceRef`, built only by `resource_ref()` from typed Wave 3 objects; anything else is a `TypeError`. |
| `may_have_changed: bool = False` | Defaults to "no impact"; conflates known no-op with unknown. | `Impact.NONE` only with `ExecutionOutcome.NOT_EXECUTED`; everything that reached a backend is `POSSIBLE`. |
| `EffectStatus` (claimed/reported/verified/failed/unknown) | Mixes execution outcome with verification; one FAILED cannot carry "effect done, cleanup failed". | Separate `ExecutionOutcome`, `Impact`, `CleanupState`, and derived `EffectVerdict`. |
| `verification_for` attestation | An adapter label asserted that an observation checked a postcondition. | `predicate_holds()` evaluates the explicit `Postcondition` against the observed state itself. |
| `EvidenceOrigin` (3 labels) | Cannot express coverage, mechanism admission or lifecycle-only facts. | `ObservationMechanism` + `Coverage`; only admitted readback mechanisms can verify, per resource kind. |

Preserved semantics: request ≠ admission ≠ dispatch ≠ execution ≠ verification;
failed and unknown executions may have partially changed state; stale evidence
stays historical and refresh appends; the newest check wins with no fallback to
an earlier complete one; equal positions are rejected; unknown scope invalidates
conservatively; receipts are never invalidated; matching state after unknown
execution is observation, not causation.

## Runtime chain

```
ExactOperation + Wave 3 bound operation (contextvars set by the dispatcher)
  -> mark_dispatch(): durable EffectClaim (fsync) BEFORE execution_id/backend
  -> backend invocation (unchanged producers)
  -> record_action(): EffectOutcome from typed ProducerFacts (before receipt reduction)
  -> admitted reads: Observation of the exact bound resource
  -> EffectHistory: invalidation / freshness / assess()
  -> EvidenceLedger.record_effects() -> existing evaluate() -> CompletionDecision
  -> existing buffered presentation gate (completion_answer)
```

## Contracts (`src/agent_runtime/effects.py`)

- `ResourceRef(kind, role, location, incarnation, snapshot_sha256)`. Location is
  "where" including the sealed root/namespace identity; incarnation is the object
  seen there. Kinds and their Wave 3 sources:
  - filesystem: `FilesystemResource` — root scope/owner/path/device/inode + path;
    incarnation = file/dir device:inode + ancestor-chain digest, or `absent:`.
  - process: `ProcessResource` — namespace/owner/request/thread/PID/**start token**/role.
    PID reuse is a different location.
  - process_launch: `ProcessLaunchResource` — generation (the exact launch→job linkage
    validated by `job_from_record`).
  - background_job: `BackgroundJobResource` — job id + generation.
  - owned: `OwnedResource` — namespace/owner/thread/collection/record; incarnation =
    revision. `*` collection bindings overlap their records.
  - external: `ExternalResource` — namespace/owner/endpoint/server/tool; incarnation.
  - browser_session: `BrowserSessionResource` — owner/thread/session key; incarnation
    = session incarnation. `BrowserPageResource` is refused.
- `EffectClaim`: run/action identity, sequence, `OperationRef` (final normalized
  tool/action/input digest/request), `impact_scope` (empty = unknown), `dependencies`,
  `obligations` (each must target a claimed binding), `parent_run_id`, `external`.
  No status field: a claim is intent, not dispatch.
- `EffectOutcome`: `NOT_EXECUTED | REPORTED_SUCCESS | FAILED | TIMED_OUT | CANCELLED |
  RUNNING | INTERRUPTED` (`ATTEMPTED` is derived for a claim without outcome), `Impact`,
  bounded `ProducerFacts` (exact scalar types only), `CleanupState`, `replayed`.
- `Observation`: exact resource, mechanism, coverage, source action/execution, `exists`,
  complete-content digest. Admitted readbacks require their source action.
- `EffectHistory`: unique positions; RUNNING may be followed by one settled outcome;
  a settled outcome is never replaced.

### Invalidation and freshness

`invalidated_by(observation)` = later claims that may touch it (overlap or unknown
scope; a refused no-op excluded) + later observations of the same location with a
different incarnation (replacement). `freshness()` is STALE, UNSETTLED (an earlier
overlapping effect was still attempted/running at observation time) or FRESH.
Receipts/acknowledgements are never invalidated. Filesystem overlap is
ancestor-or-self within one sealed root identity (listings, parents, rename-style
dependencies); no alias discovery is attempted.

### Verification

`assess(claim)` per obligation uses the newest observation of the target **after
settlement**, through a verifying mechanism for that kind (filesystem read, owned
record read, remote readback). It must be FRESH, and the predicate must be decidable
(partial coverage cannot decide content). Results: VERIFIED only with
`REPORTED_SUCCESS`; STATE_OBSERVED for timed-out/cancelled/interrupted execution
(causality unknown); FAILED execution never becomes success; CONTRADICTED when the
fresh check is false; UNVERIFIED otherwise. Process ownership, job state, browser
session, receipts and acknowledgements can stale evidence but never verify.

## Durable persistence (`src/agent_runtime/effect_log.py`)

- One append-only JSONL file per root run lineage under `DATA_DIR/effects`
  (`0600`, directory `0700`, `O_NOFOLLOW`, `st_nlink == 1` required).
- Every append takes an exclusive `flock` on the log, merges the durable records other
  writers appended (repairing a torn tail left by a crashed writer), allocates the next
  position from that merged tail, rejects a record the merged history makes invalid
  (an outcome for an effect another writer already settled, a recovery outcome for a
  claim another writer settled or marked RUNNING), then appends, fsyncs and releases.
  Independent `EffectLog` objects, threads and processes therefore never reuse a
  position and never settle an effect twice. `history()` merges others' records
  under a shared lock.
- `claim()` writes and fsyncs before returning; the first append of each log object
  also fsyncs the log's directory, and every directory created for it is fsynced in
  its parent, all under the lock and before the claim returns. A failed write or
  directory fsync truncates the record back and raises
  `EffectPersistenceError` (a `ResourceIdentityError`). `mark_dispatch` claims before
  assigning `execution_id`, so the dispatcher returns BLOCKED and the backend is never
  invoked; `dispatched()` closes the un-awaited coroutine.
- Outcomes/observations are appended; a failed non-claim write sets `degraded` (the
  on-disk claim then replays as unknown). Claim-free (read-only) runs create no file.
- `load()` validates every record strictly, tolerates only a torn final line, and
  fails closed on corruption, forged enum values, inconsistent history or aliasing.
  `recover_interrupted()` appends INTERRUPTED/possible-impact outcomes for unsettled
  claims, leaves RUNNING alone, and is idempotent. `open()` returns the live log or the
  recovered durable one.
- `launch-<generation>.json` maps a background launch generation to its claim so a
  later run can settle it: temp file written and fsynced, `os.replace`d, then the
  directory fsynced. Durability is POSIX-only (`flock`, directory fsync); neither is
  claimed elsewhere.
- The store is a Wave 3 control-plane path (prefix check), so filesystem tools cannot
  read or write it. Hardlink aliases are caught by `_aliases_effect_store`: logs and
  index files refuse `st_nlink != 1` and the store is flat, so only a multiply linked
  regular file on the store's device is checked, by inode, against one non-recursive
  listing. The store is never added to the recursive control-plane inventory, so cost
  never grows with accumulated runs. Existing containment/process/job stores are not
  reused.

## Adapters (`src/agent_runtime/effect_adapters.py`)

Inputs are only the bound operations live at `mark_dispatch` (filesystem, owned,
process, backend, browser). Classification failure claims unknown scope; it never
blocks dispatch.

| Family | Claim | Observations / settlement | Verification available |
| --- | --- | --- | --- |
| Filesystem write/edit/patch | exact bindings; CONTENT_SHA256 of the exact bytes the producer's own transformation writes: `write_file` after fence unwrapping, `edit_file` via the shared pure `_edit_file_text` on the identity-checked pre-state (no newline translation), `apply_patch` add=content / delete=ABSENT / update=`_apply_patch_hunks` on the universal-newline pre-state. If any target's state cannot be derived (unreadable, oversized, undecodable, non-`\n` platform, hunk mismatch) the claim carries no postcondition and stays UNVERIFIED | — | via later admitted complete `read_file` |
| `read_file` | none (admitted read) | re-reads the exact bound source (identity checked before/after) → COMPLETE digest, or PARTIAL for offset/limit/truncation/structured extraction | decides predicates when COMPLETE |
| `ls`/`glob`/`grep` | none | PARTIAL existence of the search root | existence only |
| bash/python launch | unknown scope + launch generation dependency | outcome from containment envelope: TIMED_OUT (`timed_out`), cleanup from `teardown.dead`, RUNNING for `bg_job_id` with a launch reservation, or the host bridge's server-set `detached` | none (process exit is not a postcondition) |
| `manage_bg_jobs` read | none | JOB_STATE observation; settles the RUNNING launch of the exact generation | none |
| `manage_bg_jobs` kill | job + its processes | settles the launch as CANCELLED | none |
| Owned mutation | exact revisioned records (+attachments as dependencies) | — | none (no independent readback contract) |
| Owned reads (`vault_get`, ...) | none | PARTIAL OWNED_RECORD_READ per exact revision | existence only |
| External/MCP | external backend ref, `external=True`; `remote_acknowledged` on exit 0 | none | none: no independent authorized readback exists, so it stays UNVERIFIED |
| Browser `session_info` | none | BROWSER_SESSION lifecycle observation of the session incarnation | none |
| Unbound tools (incl. `manage_tasks`) | unknown scope | — | none |

Producer seams added: `job` lifecycle facts on job reads/kills
(`job_lifecycle_facts`), `timed_out` on containment timeouts, and
`mutation_attempted` when `write_file`/`edit_file` fail after their truncating open.

Trust boundary: result keys carry lifecycle meaning only from the producer the
dispatcher actually bound. An unbound dynamic/registry tool contributes its exit
status alone (`ProducerFacts(exit_code=...)`); the MCP bridge builds only
stdout/stderr/exit_code, and `external`/`remote_acknowledged` come from the captured
`ExternalResource`, not the result. RUNNING requires a bound process producer (and a
launch reservation for `bg_job_id`); cleanup is attested only by a bound process
producer; job settlement only by a bound `manage_bg_jobs` read/kill of exactly one
Wave 3-validated job.

## Completion integration

No second policy. `completion._ledger()` builds the single `EvidenceLedger` used for
the decision, `ask_user` filtering and prose filtering, then calls
`record_effects(entries, action_order, partial_reads)`. Effects change the existing
`evaluate()` as follows:

- a fresh contradicting readback of a required artifact → FAILED;
- a required artifact is **unsettled** (BLOCKED, "a later operation may have changed a
  required artifact without settled evidence") when, after its last successful
  mutation, an effect with unresolved impact may have touched it: explicit targets
  with unknown/cancelled/timed-out outcomes or failures after `mutation_attempted`;
  unknown-scope effects that were cancelled/interrupted, still RUNNING, or failed
  teardown. Settled shell changes remain tracked by existing artifact version capture;
- partial `read_file` validation events become non-authoritative;
- `_supports_artifact_claim` applies the same rules, so prose cannot claim the write;
- with or without declared artifacts, the **latest** effect on any changed file being
  contradicted by a fresh readback → FAILED (a superseded earlier effect is history);
- a passing verifier followed by an effect that may have changed state without
  settled evidence → BLOCKED (the verifier is stale);
- executed external effects that are not VERIFIED cap the decision at UNVERIFIED
  (`EXTERNAL_EFFECT_UNVERIFIED`; the run may still end), and `completion_answer`
  always appends server-authored facts for them ("reported success; any external
  change it made was not independently verified", "reported failure", "unknown outcome"). This
  disclosure is structural: it does not depend on recognizing the model's wording.
  Prose filtering is additionally tightened (remote verbs are mutation claims; an
  unnamed "I updated it" cannot borrow the single required artifact; bare "Done." is
  a terminal claim) but is not relied on. A passing verifier still supports test
  claims beside an unverified external effect; it never speaks for that effect.

A RUNNING background launch alone does not block a run without declared obligations:
it completes UNVERIFIED.

Ordinary conversation and read-only synthesis are unchanged (no claims, no file).
`effect_assessments` are added to terminal metrics metadata.

## Browser, scheduler and background

Browser page/document operations still fail closed before dispatch (verified through
the real dispatcher with effects enabled: no claim, never dispatched). Only
`session_info` produces session lifecycle observations; replacement stales them.

The background monitor, after its existing `job_from_record` + `validate_job`, settles
the exact launch claim from the server-owned record's typed lifecycle facts
(idempotent across retries). The delivered report remains untrusted attributed
content; it is never an observation. Scheduler triggers are unknown-scope claims
whose replies verify nothing; scheduled runs use their own journals/logs.

## Files

Production: `effects.py`, `effect_log.py`, `effect_adapters.py` (new);
`journal.py`, `completion.py`, `agent_evidence.py`, `bg_monitor.py`,
`agent_tools/{filesystem_tools,subprocess_tools,bg_job_tools}.py` (seams);
`resources.py` (effect store added to control-plane paths; strengthening only).
Not changed: `authority.py`, containment, process ownership/reaper, browser
authority, context resolution, runtime selection, agent loop.

Tests: `test_effects_foundation.py` (recreated), `test_effect_journal_persistence.py`,
`test_effect_resource_bindings.py` (real dispatcher), `test_effect_verification_adapters.py`;
`tests/conftest.py` redirects the store to a session tmp directory.

## Residual limitations (none weakens authority or manufactures success)

- **P2 durable integrity:** records carry no MAC. A writer with access to `DATA_DIR`
  outside the tool layer could forge records that a later `load()` accepts — the same
  trust class as the existing job/containment stores.
- **P2 concurrent recovery:** a process that opens a log not live in that process
  recovers its unsettled claims as INTERRUPTED. If the owning run is live in another
  process at that moment, its later settlement is rejected as a replacement and the
  effect stays INTERRUPTED (unknown, never success).
- **P2 unobserved writers:** freshness is relative to recorded history; an external
  change after the last observation is detected only by a new observation.
- **P2 scope of verification:** VERIFIED is reachable only for filesystem effects.
  Owned/external effects have no independent readback contract and stay UNVERIFIED.
- **P2 conservatism:** unbound tools are unknown scope, so cancelling/interrupting
  even a read-only unbound tool, or a RUNNING background job, blocks later-unsettled
  required artifacts until a new successful mutation.
- **P2 replay is lazy:** interrupted claims are recovered when a log is opened (e.g.
  background settlement); there is no startup scan. Unopened claims remain on disk
  as unsettled (assessed PENDING/unknown, never success).
- **P2 retention:** no pruning of effect logs or launch index files.

## Corrective pass (adversarial review verdict B)

| Finding | Disposition |
| --- | --- |
| P0-1 log creation lacked directory fsync | Fixed: created directories and the log's entry are fsynced under the lock before the first claim returns; a failed directory fsync rolls the record back and refuses dispatch. |
| P0-2 `edit_file` verified from existence | Fixed: exact final-content digest from the producer's own pure transformation. A generic "content changed" predicate was rejected: an unrelated write satisfies it. |
| P0-3 `apply_patch` update verified without the patch | Fixed as P0-2 (universal-newline pre-state, shared hunk application); an underivable target drops all postconditions. |
| P0-4 unsupported external/MCP prose survived | Fixed structurally: decision cap + mandatory server disclosure; regex tightening is secondary. |
| P0-5 empty `required_artifacts` bypassed effect obligations | Fixed: latest-effect contradiction, verifier staleness and the external cap apply regardless of declared artifacts. A blanket "any RUNNING effect blocks" rule was rejected (it blocks legitimate background launches and fails runs on superseded effects). |
| P1-1 result dictionaries influenced RUNNING/cleanup | Fixed: facts scoped to the bound producer (see Adapters). |
| P1-2 launch index lacked directory fsync | Fixed: fsync temp → replace → fsync directory. |
| P1-3 `EffectLog.open` not thread-safe | Fixed: `_OPEN_LOCK` around the live check and load; correctness no longer depends on it (file lock + merge). |
| P1-4 hardlink protection incomplete | Fixed without inventorying the store: `_aliases_effect_store`. |
| P1-5 child unknown-scope invalidation | Rejected as intended: an unknown-scope child (e.g. a shell command) runs on the parent's host and can change any parent resource, so invalidation is required. Known-scope child effects invalidate only overlapping resources (regression test). |
| P1-6 concurrent settlement could duplicate sequences | Fixed: lock → merge durable tail → allocate → validate → append → fsync. |

## Wave 3 rebase compatibility checklist

Overlap with the corrective range is `resources.py`, `bg_monitor.py` and
`subprocess_tools.py`. Trial `git merge-tree` onto `bf697084`: the original candidate
merges textually clean; the corrected series conflicts in `resources.py` only. After
the rebase:

1. `resources.py`: Wave 3 splits `_control_plane_path` into `_control_plane_snapshot()`
   and `_control_plane_path(path, *, snapshot=None)`. **Semantic conflict even where
   the text merges:** the Wave 4 effect-store prefix check
   (`if any(Path(path).is_relative_to(d) for d in effect_dirs): return True`) lands
   inside `_control_plane_snapshot()`, which has no `path` (NameError on first use).
   This is true of the original candidate's "clean" merge as well. Resolve by putting
   `_effect_store_dirs()` into the snapshot's prefix `directories` (not the rglob
   inventory), and calling `_aliases_effect_store(candidate, effect_dirs)` after the
   candidate `os.stat` in `_control_plane_path` (it needs `st_nlink`, which the identity
   set does not carry). Keep the alias check per call, not snapshotted: it reads one
   flat directory, only for multiply linked candidates.
2. `bg_monitor._run_followup`: Wave 3 returns `FollowupResult`, makes linkage and
   authority mismatches terminal, and revalidates after the drain. Keep
   `_settle_launch_effect(resource, rec)` immediately after the first successful
   `validate_job`, before the authority comparison: settlement is execution evidence
   from the validated identity only. Confirm a TERMINAL_UNFOLLOWABLE job still settles
   and that `mark_unfollowable` retirement does not block settlement on later retries.
3. Launch publication retirement (`retire_launch(..., job=)`,
   `prune_foreground_publications`): confirm `job_from_record`/`validate_job` still
   validate a finished background job after its publication is retired, and that the
   job record keeps the exact launch `generation` used as claim lineage. Otherwise a
   launch claim stays RUNNING (conservative, but it blocks later artifacts).
4. `subprocess_tools._run_owned_command`: Wave 3's `finally` retirement block sits
   next to Wave 4's `"timed_out": True` hunk; keep both.
5. Process launch validation cost/identity changes (`e23b9b39`, `7445ba70`): confirm
   `ProcessLaunchResource`/`BackgroundJobResource` fields used by `resource_ref`
   (`namespace, owner, request_id, thread_id, generation, job_id`) and `to_dict()` are
   unchanged, and that native `#!bg` launches still bind `process.launch` (RUNNING
   gating depends on it).
6. Native local-control capability authorization and scheduled backend authority:
   confirm newly authorized operations still reach the backend through
   `dispatched()`/`mark_dispatch`, so each gets a durable claim before invocation, and
   that no new path invokes a backend outside it.
7. Diagnostics: Wave 3's preserved resource-denial diagnostics must stay pre-dispatch
   refusals (no claim, no execution id).
8. Rerun the four Wave 4 suites plus `test_runtime_resource_integration.py` and the
   `test_wave3_*` suites on the rebased tree.

## Integration with frozen lab `b1666951` (Wave 3 merged)

Merged (not rebased) so the Wave 4 commit SHAs are preserved. Resolution:

- `resources.py`: Wave 3's `_control_plane_snapshot()` / `_control_plane_path(path, *, snapshot=None)`
  architecture is kept. The snapshot computes `_effect_store_dirs()` and adds them to
  the returned prefix directories only after the recursive `job_dirs` inventory, and
  never references `path`. `_control_plane_path` checks inventoried identities after
  its `os.stat`, then calls `_aliases_effect_store` only for `st_nlink > 1`.
- `bg_monitor.py`: settlement stays immediately after the first successful
  `validate_job`, before the authority comparison; Wave 3's post-drain revalidation is
  unchanged. The deleted-session branch (terminal before linkage validation) now also
  settles a validated launch, because that job is later pruned and its publication
  retired, which would otherwise leave its effect RUNNING.
- Background publication is retired only by `bg_jobs._prune`, after a job is followed
  up or terminal-unfollowable, so every path that reaches retirement has already had
  its settlement attempt. A job with invalid linkage is never settled (no authority).
- Scheduled builtin actions (e.g. `cookbook_serve`) run in the scheduler outside any
  agent journal and never reached `mark_dispatch`; Wave 3 only added their backend
  authority. Agent-dispatched local control (`download_model`, `serve_model`,
  `serve_preset`) is claimed by `dispatched()` before its handler mints a capability.
