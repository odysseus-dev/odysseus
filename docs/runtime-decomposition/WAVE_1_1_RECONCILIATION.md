# Wave 1.1 final post-PR40 reconciliation

This is the one-time local reconciliation of completed Wave 1.1 with the
authoritative post-PR40 lab commit. It does not start another runtime wave.

## Verified starting state

- Wave branch: `feature/agent-runtime-wave-1-1`.
- Original Wave HEAD: `63457367aeed431b2c48967988259e5861f19916`, clean.
- Canonical branch: `lab`.
- Canonical HEAD: `9557b8d5909eb4a885c3bf49e19a65dd904f8c1d`, clean.
- Merge base: `f0761641a12b63e401960f596d3d1be8fc90fbea`.
- Divergence: 10 Wave-only commits and 47 lab-only commits.
- Changed-file overlap: `src/agent_loop.py`, `src/tool_execution.py`,
  `tests/test_tool_policy.py`, and `tests/README.md`.

The Wave-only commits were `d57d5c58`, `dfeab64a`, `ae2445d6`, `7d84f3fe`,
`1470dbb2`, `32830918`, `ba29afb9`, `bdfcbc0a`, `70cbaf81`, and `63457367`.
Their completed behavior is retained. The canonical worktree is read-only;
the exact canonical SHA was merged once with `--no-ff --no-commit`.

## Semantic integration

The only textual conflict was in `src/tool_execution.py`, where Wave 1.1
wrapped dynamic dispatch with `dispatched(...)` and lab added `disabled_tools`
and `tool_policy` forwarding. The resolution retains both inside the wrapper.
Lab's new owner-aware image-generation dispatch also receives that wrapper.
The image regression checks that explicit denial never invokes the backend,
actual dispatch has an execution identity, and a backend without an explicit
exit code does not manufacture an authoritative success receipt.

Broad validation exposed narrow adapter incompatibilities beyond the textual
conflict. Native host-shell JSON now uses the same decoded command classification
as journal evidence. The exact existing TUI interpreter-selection string is
shared with the evidence parser: a following foreground verifier keeps its
exit status, while generic conditional discovery, help/collection modes,
variable arguments, and status-masking tails remain insufficient test proof.
The generated interpreter-selection command itself is unchanged.

The generated environment reference is refreshed with the canonical generator
so its source-location links match the reconciled code.

Structured native patch arguments retain artifact targets. A pre-edit
inspection cannot invalidate a later passing executable verifier, but still
cannot verify the edited artifact by itself; failed post-edit inspections
remain failures. Artifact recovery's terminal round-text revisions retract buffered rejected drafts
before presentation; their replacement prose is still gated by journal
evidence. Explicit final-response events retain precedence, safe reasoning
survives, and provider-error partials and diagnostics retain their ordering.

Existing subprocess doubles now carry PIDs. Execution simulations use the
existing receipt-aware test helper. Contract tests assert the additional
completion-decision event and retain their no-inference/no-execution spies.
TUI tests retain tool order, retry behavior, and positive explicit-verifier
coverage while additionally rejecting completion from an opaque fallback.
Round-control fixtures explicitly fail unconfigured direct-provider synthesis
instead of contacting their fake endpoint, and supply the synthetic context
window while retaining real compaction logic. Conversational round provenance is
preserved outside artifact recovery.

The agent-loop changes merged automatically: lab's weather relevance and
policy-gated browser fallback coexist with Wave's action receipts, completion
gate, and deferred teacher handoff. The fallback dispatcher runs inside the
current invocation's journal. No generic tool floor was restored.

`src/agent_runs.py`, `routes/chat_routes.py`, `static/js/chat.js`,
`static/js/chatRenderer.js`, `src/tool_policy.py`, `src/tool_capabilities.py`,
`src/turn_contract.py`, `src/model_profiles.py`, and
`src/clean_agent_preview.py` retain the exact canonical lab content.

## Identity audit

These classifications describe every relevant identity use across the
detached-run manager, chat routes/browser consumers, completion gate, journal,
teacher handoff, and existing server-owned security provenance.

| Class | Uses and boundary |
| --- | --- |
| 1. Live/detached stream-run identity | `agent_runs._Run.run_id`, `get_run_id`, and the chat response's `X-Odysseus-Run-Id` identify the detached stream. The browser's `_streamRunIds` is populated from the response header. |
| 2. Stop/resume/replay identity | `expected_run_id` in `stop` and `request_finish`, route request headers, `_postExactStop`, the finish-editor request, `streamRunId`, and `resumeRunId` refer to that same detached stream. `subscribe` binds the exact `_Run` object returned by start/resume. |
| 3. Stream metrics/cost identity | `_metricsCostRecordId` uses the header-derived stream ID plus `primary`/`teacher`; `metrics._costRecordId` and the cost renderer's local `runId` refer to this accounting key. Neither uses terminal metadata's journal `run_id`. |
| 4. Logical nested invocation identity | `ActionJournal.run_id` is generated per completion-gated invocation. `action_id` is derived from it. The completion gate's terminal metadata `run_id` identifies this logical invocation. Existing `ToolRunSecurityContext.run_id` and `origin_run_id` values identify separate server-owned invocation/skill provenance operations; they are neither stream IDs nor journal lineage. |
| 5. ActionJournal parent/child identity | `ActionJournal.parent_run_id`, the gate's parent lookup, `_parent_run_id`, `request_teacher_takeover`'s captured parent ID, and `run_teacher_inline(parent_run_id=...)` link journal invocations. The completion metadata's `parent_run_id` preserves that lineage. |

No invocation ID is passed to stream stop/finish/replay APIs. No stream ID is
inserted into journal lineage. A new detached-stream regression creates nested
gates, rejects both journal IDs at stop/finish, accepts the stream ID for finish,
and verifies identical replay and unchanged journal metadata.

## Runtime invariants and final lab behavior

Every gated invocation creates a distinct journal, including children using
the same workspace. Journal and action bindings restore on normal unwind,
exception, cancellation, and generator close. Child awaiting/exhausted/error
state cannot rewrite the parent's completion decision or receipts.

The teacher adapter runs after the student gate closes. It forwards the parent
turn contract, tool policy, disabled tools, plan, client runtime context, and
external-untrusted-context restriction. Teacher execution receives a new
journal whose parent is the student invocation. Inner terminal frames are
consumed; only the outer adapter emits final termination. Exact framed
`data: [DONE]` events are distinguished from ordinary content containing the
literal marker.

Provider failures retain live events, then safe partial content when present,
then a non-completing decision, terminal metadata, and the original error last,
without DONE. A bare error remains a bare error. Completion gating does not
add provider calls or turn missing evidence into extra provider rounds.

Lab's server-owned authority remains narrower than inventory or availability.
Transcription, OCR, tasks, browser fallback, request-specific capability
selection, compact contracts, and provider-compatible tool choice retain the
canonical implementation. Model ID `Ajax` selects the Odysseus compact profile;
its selected schema boundary survives compatible `auto` tool choice, explicit
no-tools remains explicit, and transport remains OpenAI-compatible. No
benchmark-runner code was independently edited or executed.

## Validation records

The current requirements were installed in an isolated environment under this
worktree's ignored `.cache/wave1-1-reconciliation` directory. The shell's
unrelated `python` environment was not used for the accepted validation.
Canonical full pytest uses the repository's default data directory and allows
dotenv loading so research-path and setup tests can exercise their own fixtures;
the focused Wave script retains its explicit runtime isolation settings.
Optional live Ajax tests retain their opt-in skips; no live model or benchmark
run is part of this reconciliation.

- [Focused tests](validation/wave-1-1-reconciliation-focused.txt)
- [Wave 1.1 validation script](validation/wave-1-1-reconciliation-wave-validation.txt)
- [Broad affected runtime suite](validation/wave-1-1-reconciliation-broad.txt)
- [Canonical full pytest](validation/wave-1-1-reconciliation-pytest.txt)
- [Compileall, JS/MJS syntax, diff checks, and conflict-marker scan](validation/wave-1-1-reconciliation-gates.txt)

The focused records include the final relevant rerun after the reconciliation
audit was written. Full pytest and canonical static gates run afterward. The
local merge is committed only after the required checks pass. No push, PR,
deployment, or later-wave work is authorized by this reconciliation.

## Maestrum limitations encountered

The normal read-only pre-merge comparison stalled without a completion or
failure payload; its execution cell was terminated and the investigation was
not retried. Exact-path inspection proceeded using `local_only` with
`scope_mode="worktree"`.

The Context Firewall rejected an unbounded `git diff --cached --check` command
and withheld raw log output after the inspection allowance was exhausted.
Requests for ignored `.log` files were rejected with
`scope_rejected: ignored_by_git`. Unignored `.txt` validation records were
subsequently admitted by exact path. Canonical checks themselves run as
validation operations and record their exit status in the admitted gate log.
No epoch waiting or alternative worker mechanism was used.
