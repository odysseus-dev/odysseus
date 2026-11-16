# First Windows activation: decision and approval boundary

**Preparation only. Neither option is authorized for execution.**

Production code baseline last checked: `a86c1c7b346256354d49520e63ea705525f47748`.
The running interpreter is still legacy. Integrating source is not activation.
The accepted `WINDOWS_ACTIVATION_READINESS.md` remains historical evidence, not a
fresh idle certificate. Do not repeat the same legacy descendant investigation
in an attempt to create an exhaustive counter that the old process lacks.

## What is and is not established

| Boundary | Evidence / limit |
|---|---|
| Cookbook task records | Accepted snapshot had zero recorded nonterminal tasks. Recheck before maintenance; preserve all tasks and logs. |
| Worker receipts | 13 gone and 2 obsolete incarnations were reconciled observationally. A current foreign/recycled PID is not the recorded worker. Files and outcomes remain intact. |
| Native background jobs | Accepted evidence established the actual data directory and never-launched state: no companion launch/publication artifacts plus complete native supervisor visibility. Missing JSON alone does not mean idle. Revalidate if anything changed. |
| Legacy MCP descendants | Their private image/memory/RAG/email/Playwright ownership was explained, NOT their current in-flight activity. Neither low CPU nor process existence establishes idle. |
| Hermes turns | Accepted snapshot contained an active turn. It is not an authorization to stop, and must not be reused as a zero-turn claim. Maintenance must run after active turns finish, not from an executing agent turn. |
| Shared services | Gateway 9130 and Chroma 8100 have service-specific JSON health contracts and creation identities. Preserve them; re-pin immediately before any approved transition. |

## A. Strictly verified-idle transition

**Not currently establishable with the available legacy instrumentation.**

The old process exposes no exhaustive native-MCP in-flight counter. Its image
busy endpoint is not evidence for memory, RAG, email or Playwright. New source
cannot retrofit that running interpreter. Therefore option A must remain blocked
unless genuinely new authoritative evidence becomes available. More descendant
snapshots are not a solution, and no maintenance exception is silently applied.

## B. Scheduled maintenance with one explicit legacy exception

This is the practical first-activation option, but needs a separate explicit
approval acknowledging **unobservable legacy native-MCP in-flight work may be
interrupted**. It is a one-time manual maintenance decision, NOT an automatic
restart-guard bypass or a future policy change.

### Interruption and preservation

- The app listener on 7000 and its in-memory native UI/chat/MCP requests may be
  interrupted. Unsaved conversation or UI state may be lost.
- Private image generation, memory writes, RAG indexing, email operations and
  browser navigation/downloads may be in progress without observable evidence.
  Interrupted writes/indexing may be partial. An email or other external action
  may already have happened even if its response is lost: do not blindly retry.
- Ending the legacy interpreter can close stdio or Windows job handles. Private
  MCP servers/children may exit through EOF or OS job cleanup. Their survival
  must NOT be promised merely because they were not directly signalled.
- Preserve the original gateway, active sessions/data, Chroma and every unrelated
  process. Do not terminate or restart them as a convenience. Only identified
  app-private services fall within the residual MCP interruption exception.

### Exact boundary to approve and pin at execution

Only the **verified original application launcher chain, venv redirector (if
present), and port-7000 application interpreter** may be termination targets.
Re-identify current executable/parentage, owner and creation identity and pin
native handles; do not use the historical PID list as authority. Stop the
verified supervisor first to prevent respawn, then the individually pinned app
chain according to the already prepared maintenance procedure.

There is no tree-wide termination, console broadcast, guessed-PID action,
automatic escalation, or direct signalling of MCP/Chroma/gateway/unrelated
children. If private MCP processes survive, account for them and stop before
starting duplicate replacements; any additional direct termination needs its
own explicit scope/approval. New permanent instrumentation does not adopt
surviving legacy servers just because they respond.

### Gates that the exception does NOT waive

Abort on any of these, even with option B approved:

- Any active Hermes turn or admitted user operation known to be active; do not
  terminate this agent session to make the procedure possible.
- Any active download/installation, native background job, or live matching
  worker receipt. Keep stale/obsolete history, not fabricated empty registries.
- Missing/stale/ambiguous identities, guard output, ownership or task inventory.
- Any unexplained descendant, unknown worker outside the explicitly acknowledged
  legacy MCP observation gap, or shared service inside an unsafe stop boundary.
- Changed gateway/Chroma identity or failed actual JSON health checks.
- A changed/dirty production baseline, missing private recoverable backup,
  inconsistent task registration/wrapper/config hashes, or concurrent changes.
- Failure to confirm the exact old interpreter/launcher exited. Never start a
  replacement while teardown or the old listener is unconfirmed.

The narrow exception concerns only the established legacy MCP observability
gap. It is NOT permission to ignore a known busy MCP operation or arbitrary
unknown work. Ask users to stop starting work for the maintenance window; that
is an operational precaution, not a claim that hidden work has been measured.

### Backup, transition and recovery (not executed)

1. Recheck production SHA/cleanliness and the chosen approved code-only candidate.
   Preserve Sonnet's changes and existing workspace grants/configuration. Keep
   the model-serving lifecycle branch out of this integration.
2. Verify existing private backup manifests/bundles and forward-only inverse-tree
   rehearsals; take a fresh recovery snapshot of current task/config state before
   the window. Use online-consistent SQLite backup for the live gateway if
   needed. Never print credentials or overwrite new gateway state with an old
   snapshot. Existing sets are in the independent clone under
   `.odysseus-dev/windows-restart-deploy-20261116-121811` and
   `.odysseus-dev/windows-restart-deploy-20261116-165732`.
3. Back up and verify the original scheduled-task XML and hidden wrapper bytes,
   encoding and ACL. Keep the task's registration, account, triggers and settings
   intact. Only the already staged wrapper-only `-RunOnly` proposal is in scope
   for a separately authorized activation; no startup change has been applied.
4. After separate integration AND maintenance/startup approval, obtain fresh
   zero-turn/worker checks, protected service identities/JSON health and pinned
   app-chain handles. Run the narrowly approved one-time transition; preserve
   the gateway/Chroma branches and verify old app teardown before launch.
5. Start once through the cooperative RunOnly path. Verify a new port-7000
   identity, `/api/health` JSON, authenticated control status, initialized MCP
   counter, unchanged gateway/Chroma identities and their proper JSON health.
   Check the retained Gemma failure, readable logs and persisted corrected state;
   no model re-download is needed. Do not repeat the accepted full UI sequence.
6. On failure, leave shared services running, do not force-escalate or repeatedly
   launch. Preserve diagnostics and any newer task state. A forward-only source
   rollback and exact wrapper-byte/ACL restoration must be separately authorized
   if needed. Do not restore historical Cookbook/gateway state over new writes.
   Re-pin and inspect a partially started app before any recovery stop; a failed
   activation does not authorize terminating unknown processes.

## Reconciliation candidate review

`10b7ed5cf531389568ab71f625630dfe5c94b6bf` is ready for **code-only integration
review**, not runtime activation. Its observer never signals, retires or rewrites
receipt/task files; compares current creation identities; rereads receipts and
identity to reject races; and permits only gone/obsolete worker evidence. Live,
malformed or unreadable evidence blocks. Lazy background idle requires absence
of companion artifacts, complete supervisor visibility and an absence recheck.
The accepted 17 focused inventory/guard cases are reused; no broad audit was
repeated. The permanent MCP addition is a separate commit on this candidate.
Neither candidate has been merged by this task.

## Decision requested when implementation review is complete

Choose A (remain blocked waiting for new authoritative legacy instrumentation)
or separately approve B's scheduled maintenance interruption risk and exact
startup/termination scope. Approval to review or integrate source is not
approval to execute B. No termination/startup command is embedded here as an
implicit authorization.
