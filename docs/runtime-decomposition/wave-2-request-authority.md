# Wave 2: request authority

Base: `d6c3c98c75e03f70c05ebe4058c6fa12e0395f62`, branch
`feature/runtime-request-authority`. Discovery and this plan precede production
changes. No later runtime waves are included.

## Discovered call paths

`routes/chat_routes.py` parses mode, toggles, workspace, approval decisions and
runtime context. User intent can promote Chat to Agent. Owner privileges,
global disabled tools, compare/incognito and plan restrictions produce
`ToolPolicy`. Compact/native routes resolve `TurnContract`; regular/full models
can receive the full enabled schema inventory. The route calls
`_stream_agent_with_execution_bridge` and `stream_agent_loop`. Detached runs
retain this generator; reconnecting subscribes to it rather than creating a new
invocation. Their stream IDs are distinct from journal IDs.

`src/turn_contract.py` classifies request families and selected tools, resolves
exact safe reads, and filters schema availability. Empty-family routing has a
legacy core inventory. Warm tools and editor availability may enlarge offers.
Transcription, OCR and tasks have narrow selection; static web retrieval may
offer private_browser for fallback. These routing choices are not grants.

`src/agent_loop.py` selects provider/profile transports, parses native or textual
tool blocks, repairs calls, performs deterministic preflights and retries, and
calls `src/tool_execution.py:execute_tool_block`. Compact preview uses
`src/clean_agent_preview.py` but reaches the same dispatcher. The dispatcher
checks run security, exact approval, contract membership, disabled tools,
ToolPolicy, owner restrictions and bridges before MCP/dynamic/built-in handlers.
It forwards policy to dynamic handlers. Legacy loop reconciliation removes
disabled names found in a contract's offered inventory. This must not erase a
request-authority denial.

Approvals use `src/tool_approvals.py`. A server record binds tool/content, owner,
session, workspace, document id/version/digest, origin run and continuation
state. Consume is destructive; claim is one-use. Task/chat scopes bypass an
existing run-security gate; they do not define the requested operation classes.
Approval continuation executes the sealed action in round zero. Denial exits
the route without execution.

Generic app_api forwards both the internal token and the caller's owner to
loopback HTTP. Its blocklist does not exclude Chat/skill approval ingress.
Matching owner/session/input bindings alone therefore cannot distinguish a
model-produced HTTP decision from a user approval. Those existing ingress
points need an explicit internal-tool rejection before consuming approval.
Internal HTTP skill-test task bodies likewise cannot mint fresh authority.
The same origin rule applies to generic Chat HTTP entry: a loopback generated
message is not a new trusted user request, even with correct owner attribution.
Both Chat entry points use the existing non-persistence switch for these
messages and append explicitly untrusted transient context instead. Later
referential turns cannot inherit their operation class as prior user intent.

Teacher takeover is queued by the student, then owned by the outer adapter in
`src/teacher_escalation.py`. It invokes a child loop after the student gate closes
and forwards policy, contract, workspace and runtime context. The teacher's
synthetic user message is model context, not a new authority source.

`src/task_scheduler.py:_execute_assistant` composes crew/global restrictions and
RAG/default shell availability. `_run_agent_loop` supplies task.prompt or a
synthetic override as a user message, with background provider fallback. Exact
approval pauses are retired because there is no interactive approver.
`_execute_action` invokes BUILTIN_ACTIONS directly, with a separate admin gate.
`src/tools/system.py:do_manage_tasks` and `routes/task/task_routes.py` create/edit
persisted tasks. No authority snapshot currently survives scheduling.

Detached Bash dispatch launches `bg_jobs.launch` and returns bg_job_id.
`src/bg_monitor.py:_run_followup` appends an explicitly untrusted result to session
context and re-enters the loop. It currently forwards neither the originating
authority nor its request restrictions. Skill tests/audits in
`routes/skills_routes.py` also invoke the loop with task/user messages; generated
audit context must not manufacture grants.

| Question | Current source |
| --- | --- |
| Requested operation | User intent classifiers, exact safe-read resolver; ultimately parsed/repaired model tool block |
| Available capabilities | Registry/MCP inventory, profiles, RAG, TurnContract and request-specific schema filters |
| Authorized capabilities | Fragmented policy, privileges, run security and approval checks; no independent envelope |
| Restrictions | Route toggles, owner/global policy, plan/compare/incognito, dispatcher owner/workspace checks |
| Approval required | Deterministic run-security decision; model output can propose the action but cannot consume approval |
| Approval input scope | Server-sealed exact tool/content and owner/session/workspace/document binding |
| Nested state | Explicit policy/contract/workspace/context forwarding and journal lineage; no authority snapshot |
| Model influence | Tool/input proposals, repairs, recovery choices, generated task/audit prompts; availability currently participates in execution gating |

## Implementation plan and contract

1. Add immutable `ExactOperation`, `OperationGrant` and `RequestAuthority` in
   `src/agent_runtime/authority.py`. Normalize canonical tool identity and JSON
   inputs (reject duplicate keys/non-finite values); retain exact raw text for
   Bash/Python, built-in scheduled actions and non-JSON inputs. Grants contain an operation class/tool identity, optional
   action limits and exact input limits. Authority has its own request id,
   owner/session/workspace binding, immutable grants and hard denials. It is
   independent of schema presence, model/profile, stream/journal/receipt IDs.
2. Create authority from trusted request text/history and deterministic policy
   at the chat route before availability reconciliation. The general loop
   boundary creates it for other trusted direct callers, without consulting
   schemas, relevant_tools, forced_tools or model output. Authority family
   inheritance reads only trusted user history. Tool-history exact reads may
   narrow an already admitted class, never create a class. Unknown intent grants
   no execution floor. Neutral interaction/planning controls remain explicit.
3. Keep semantic classification and availability in TurnContract. Resolve
   authority grants separately from those semantic facts and hard policy.
   Exact safe reads restrict action/identifiers. Static web fallback authorizes
   browser reading/navigation, not arbitrary click/evaluate/form operations.
   Media/task families do not inherit the shell inventory.
4. Bind authority around the whole logical stream, including teacher takeover;
   forward it explicitly to teacher children and approval records. Children
   inherit the parent or intersect explicit authority with it. Policy denials
   union; grants intersect; a child cannot replace the parent scope. Restore
   the parent on close/error/cancellation. Capture restrictions before legacy
   offered-tool reconciliation can erase them.
5. Enforce at `execute_tool_block`, before approvals are claimed or handlers,
   bridges/MCP/process dispatch begin. Current policy/disabled gates still win.
   Missing/malformed dispatcher state fails closed. Standalone callers/tests
   must supply explicit server authority. Journal ownership remains unchanged;
   denied calls produce no authoritative execution receipt.
6. Existing approvals remain one-use exact claims. Seal the originating
   authority in the approval digest. Resumption keeps original class limits and
   current hard restrictions. The approved exact operation may cross its
   original class boundary only through the consumed, matching server record
   at that call; it does not mutate authority for subsequent calls. Nested
   execution cannot use an approval to exceed its parent ceiling. Existing
   task/chat UI and run-security scope semantics are unchanged.
   Chat/skill approval ingress rejects validated internal-tool requests before
   consumption; identity impersonation is not a user approval decision.
   A shared HTTP factory admits trusted user requests and produces an empty,
   policy-restricted envelope for known internal-tool Chat/skill requests.
7. Persist a server-only authority snapshot and task-input binding on scheduled
   records. Direct authenticated task ingress can admit its user-supplied task;
   task creation inside model execution intersects with parent authority.
   Scheduler overrides, retries and provider fallbacks reuse that snapshot.
   Missing/stale snapshots grant no tool authority. Newly seeded server-owned
   housekeeping jobs receive exact snapshots at their static creation point;
   existing rows are not retrospectively authorized by their names/actions.
   Internal tool HTTP task payloads cannot become fresh user requests across an
   ASGI context boundary. Built-in actions receive
   an exact admission check. Persist detached-job authority in a separate
   authority sidecar at dispatch; monitor continuations reuse it and current
   denials. Do not edit bg_jobs/process containment implementation.
8. Production files: new authority module; routes/chat_routes.py;
   src/agent_loop.py; src/tool_execution.py; src/teacher_escalation.py;
   src/tool_approvals.py; core/database.py; routes/task/task_routes.py;
   src/tools/system.py; src/task_scheduler.py; src/bg_monitor.py;
   routes/skills_routes.py. Change preview only if direct-entry binding is
   required by validation. No TurnContract/profile/schema redesign.
9. Shared hotspots: route/loop/dispatch, approvals and task/database integration.
   One coordinator writes all production files. Keep changes confined to
   authority creation, forwarding, persistence and admission. Do not modify
   containment, provenance/effect classification or egress implementation.
10. Focused regressions: available schema/bridge/dynamic handler without grants;
    model-selected unrelated tool/action; explicit class admission; exact read
    arguments; narrow transcription/OCR/tasks/browser fallback; hard denials
    despite offered-tool reconciliation; retry/fallback stability; child and
    teacher non-widening and restoration; malformed/missing state; exact
    approval mismatch/replay and continuation scope; scheduled snapshot/input
    binding and synthetic override; detached followup inheritance; journal
    denial evidence. Preserve existing policy-forwarding and Ajax assertions.

Validation: new focused tests; existing contract/policy/capability/profile
tests; scripts/validate_runtime_wave1.sh; broad affected runtime tests; full
pytest; compileall; JS/MJS syntax; diff check and conflict-marker scan. Any
production edit after full pytest requires affected tests and full pytest again.

## Implemented boundaries and remaining limits

The preview entry also binds authority because it supports direct callers.
Research task admission binds the snapshot around the researcher, so nested
execution cannot infer grants from generated research context. LAN lookup
intent has a narrow host_shell-only admission rule; it adds neither Bash nor
Python and does not alter Ajax schemas or profiles.

Scheduled loop entry explicitly forwards the restored workspace as well as
the envelope; rebinding the continuation session never drops confinement to
the original workspace. Only the actual server Bash launch seals a detached
job sidecar. A handler/bridge result claiming a job id cannot create one.

Snapshots are trusted server state, stored in the task database and detached
job authority sidecars. Missing, malformed, changed-input, wrong-owner or
wrong-session snapshots fail closed. Legacy tasks need a trusted task-input
save to obtain a snapshot; legacy detached jobs have no execution grants on
followup. No broad backfill, authority-mode UI, containment, effect/egress or
receipt/journal redesign is included. Sidecars follow the detached job's server
storage trust assumptions; retention/integrity hardening is outside this slice.

Class admission deliberately reuses the deterministic semantic classifiers.
Unrecognized intent has only explicit ask_user/update_plan controls. This can
deny unsupported phrasing and generated default skill tests/audits; model
prompts and tool inventory cannot repair that denial. Existing exact approvals
can admit one sealed root operation, never widen subsequent calls or nested
authority. They still require the existing armed security context, matching
bindings, one-use claim, document checks and current hard restrictions.

Standalone dispatcher test fixtures now supply explicit registry grants to
continue exercising their original handler/policy/confinement assertions.
New authority tests use the raw dispatcher and prove denial before dispatch.

## File ownership and reasons

| Production file | Wave 2 change |
| --- | --- |
| src/agent_runtime/authority.py | Immutable intent/admission/operation API, trusted factory, intersection/context binding, task/job snapshots |
| routes/chat_routes.py | Capture authority before availability reconciliation; pass it into execution; guard approval ingress |
| src/agent_loop.py | Bind logical-invocation authority; capture it in approvals and teacher takeover |
| src/tool_execution.py | Normalize/check operations before dispatch and approval claims; bind handler context; seal actual detached launch |
| src/teacher_escalation.py | Explicit child/approval inheritance without synthetic-prompt grants |
| src/tool_approvals.py | Bind immutable originating authority into exact approval digest |
| src/clean_agent_preview.py | Bind authority at the supported direct preview entry |
| core/database.py | Add nullable server-only scheduled snapshot column and additive migration |
| routes/task/task_routes.py | Seal direct user task inputs; deny fresh grants to internal-tool HTTP payloads |
| src/tools/system.py | Cap model-created/edited task snapshots by active authority |
| src/task_scheduler.py | Restore original scope/workspace for loops, admit exact built-ins/research, seal new static defaults |
| src/bg_monitor.py | Restore original detached-job scope and current hard restrictions |
| routes/skills_routes.py | Separate explicit user task authority from generated/internal skill prompts; guard approval ingress |

Shared hotspots touched: chat routes, agent loop, central dispatcher, preview,
teacher escalation, approvals, task CRUD/scheduler/system handlers, database,
background monitor and skill entry routes. All production edits have one writer.
TurnContract, tool schemas, model profiles, journal/completion foundations,
bg_jobs/process containment and effect/egress implementations are untouched.

`tests/test_request_authority.py` adds the focused authority regressions.
`tests/runtime_evidence_helpers.py` adds explicit standalone server fixture
grants. Original assertions are preserved in these adapted fixture suites:

- tests/test_agent_external_tool_schemas.py
- tests/test_ask_user_tool.py
- tests/test_client_tool_routing.py
- tests/test_edit_file.py
- tests/test_execution_bridge.py
- tests/test_external_context_tool_gate.py
- tests/test_image_creation_routing.py
- tests/test_review_regressions.py
- tests/test_runtime_evidence_contract.py
- tests/test_task_cookbook_admin_gate.py
- tests/test_task_scheduler_cancel.py
- tests/test_tool_approvals.py
- tests/test_tool_path_confinement.py
- tests/test_tool_policy.py
- tests/test_turn_contract.py
- tests/test_turn_contract_integration.py
- tests/test_update_plan_tool.py
- tests/test_weather_search_recovery.py
- tests/test_workspace_confine.py

`website/configuration-reference.md` is regenerated solely to update the
chat-route environment-read line number. This document records discovery,
the pre-edit plan, implementation boundaries and file ownership. The validation
report records final commands/results. No production files in parallel lanes
are claimed.
