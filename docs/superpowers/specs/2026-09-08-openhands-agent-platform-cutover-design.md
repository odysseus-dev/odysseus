# OpenHands Agent Platform Cutover Design

**Date:** 2026-09-08  
**Status:** Approved design baseline; four integration details remain gated by Phase 1 feasibility spikes  
**Scope:** Replace Odysseus agent and unmanaged product-LLM machinery with an OpenHands-based agent platform and governed model jobs

## 1. Decision

Odysseus will make a coordinated platform cutover to OpenHands. OpenHands will own agent execution, communication, conversations, workspaces, and canonical agent events. OpenHands Automation will own scheduled and background dispatch, run history, and sandbox lifecycle orchestration. Odysseus will retain product policy, domain behavior, schedules, governed archetypes, delegated authorization, domain-facing MCP capabilities, and product UI projections.

This is a replacement, not a compatibility migration. Work may proceed in dependency-ordered vertical slices on a branch and test stack, but production will not retain parallel agent runtimes, old event semantics, or a legacy fallback.

Four details are intentionally provisional until tested against pinned upstream versions:

1. whether Automation can create a new run against an existing Agent Server conversation;
2. whether delegated credentials can rotate inside a live native or ACP execution;
3. exact MCP credential/configuration propagation through each ACP profile;
4. plumbing from OpenHands confirmation to an Odysseus-signed approval grant.

These gates refine integration mechanics. They do not reopen the ownership, archetype, domain, security, failure, retry, or projection models.

## 2. Goals

- Make OpenHands canonical for agents, conversations, events, workspaces, and execution lifecycle.
- Ship OpenCode and Hermes as first-party ACP profiles and OpenHands native agents as first-party native profiles.
- Provide a compositional `OdysseusAgent` for daily product tasks.
- Represent chat, coding, deep research, and other work as independent versioned archetype contracts.
- Route agent-facing Odysseus access through a thin, governed MCP boundary.
- Keep Odysseus domain services authoritative for business behavior and data.
- Use short-lived, execution-bound delegated authority with separate workload authentication.
- Provide seamless native Odysseus UI while preserving Agent Canvas as full engineering UI.
- Replace all unmanaged product reasoning/generative calls with governed agent or model-job executions.
- Delete the old agent loop, tool loop, direct product-LLM paths, detached-run system, and compatibility-only tests after acceptance.

## 3. Non-goals

- Preserve `stream_agent_loop()` or its SSE protocol.
- Maintain old agent/runtime behavior for backward compatibility.
- Duplicate AgentProfile schema inside Odysseus.
- Store a second canonical transcript in Odysseus.
- Normalize native and ACP internal reasoning semantics.
- Put Odysseus business logic in MCP tools or agent classes.
- Give agents durable credentials or refresh authority.
- Wrap embeddings, speech, image generation, moderation, or capability probes in fake agents.
- Provision a conversation and sandbox for small typed inference jobs.
- Build an Odysseus scheduler, queue, sandbox orchestrator, or run-history platform where Automation already suffices.
- Subclass OpenHands agents before composition proves insufficient.
- Expose arbitrary REST as an ACP fallback when MCP forwarding fails.

## 4. Canonical ownership

| Concern | Canonical owner |
|---|---|
| Product schedules, recurrence, and user intent | Odysseus |
| Archetype contracts and product policy | Odysseus |
| Profile capability/policy metadata | Odysseus |
| AgentProfile schema and resolved launch configuration | OpenHands |
| Scheduled/background dispatch and run history | OpenHands Automation |
| Sandbox lifecycle orchestration | OpenHands Automation/runtime |
| Conversation and canonical event history | OpenHands Agent Server |
| Workspace and agent runtime envelope | OpenHands Agent Server/runtime |
| Native reasoning, tools, and condensation | OpenHands native agent |
| ACP reasoning, model calls, and internal tool loop | ACP implementation |
| Domain behavior and authoritative data | Odysseus services |
| Agent-facing domain capability interface | Odysseus MCP |
| Delegated domain authorization | Odysseus token authority and MCP |
| Domain-verifiable approval grants | Odysseus Approval Authority |
| Product run, status, and artifact projection | Odysseus |
| Full agent engineering UI | Agent Canvas |
| Product-focused agent UI | Odysseus |

OpenHands owns the outer envelope for ACP agents—conversation, workspace, lifecycle, events, interruption, and provenance—but not Hermes or OpenCode's internal reasoning loop.

## 5. Distribution topology

First-party deployment contains separate components:

- Odysseus application/API and domain workers;
- Odysseus MCP service;
- OpenHands Agent Server control plane;
- OpenHands Automation service;
- Agent Canvas;
- per-run sandbox/runtime containers;
- an optional trusted MCP credential broker only if live token injection proves infeasible.

Agent Server and Canvas remain independently addressable. Odysseus embeds no browser tab and does not host Canvas inside its UI. Odysseus provides its own native textual/product interaction and a link to open the same conversation in Agent Canvas.

Docker Desktop requirements:

- explicit network boundaries;
- explicit workspace mounts and grants;
- no implicit host filesystem exposure;
- read-only mounts where sufficient;
- no shell-argument secrets;
- service health checks and pinned images;
- distinct Tailscale exposure policy for Odysseus and Agent Canvas.

## 6. Identity model

Three IDs remain distinct:

```text
conversation_id
    Persistent dialogue and agent state; canonical in Agent Server.

automation_execution_id
    One Automation-controlled invocation; canonical in Automation.

agent_profile_id + revision
    Immutable launch provenance; resolved and snapshotted at launch.
```

Odysseus adds correlation, not competing authority:

```text
odysseus_request_id
    Product request/idempotency key.

model_job_id
    Identity for bounded inference that does not use Automation.
```

```text
GovernedExecutionReference
├── AutomationExecutionRef(automation_execution_id, conversation_id)
└── ModelJobRef(model_job_id)
```

Automation 1.11.0 can route a new external event to an existing live subject/run, append a turn to its derived conversation, and wake the loop without creating a new run. It also relies on Agent Server's ability to append a user event to an idle conversation with `run: true`. This proves persistent Automation-driven conversations, but does not prove that one conversation can contain multiple first-class Automation executions. Phase 1 must answer that narrower question.

## 7. Agent profiles

First-party profiles are heterogeneous:

```yaml
AgentProfile: opencode
  transport: ACP
  command: opencode acp

AgentProfile: hermes
  transport: ACP
  command: hermes acp

AgentProfile: openhands
  transport: native
  agent: CodeActAgent
```

Upstream `AgentProfile` remains canonical. Odysseus stores adjacent metadata keyed by immutable profile identity/revision:

```yaml
OdysseusProfilePolicy:
  agent_profile_id: hermes
  agent_profile_revision: 12
  capabilities: [research.invoke, documents.read]
  archetypes: [chat, deep-research]
  risk_class: medium
  delegation_depth: 1
  workspace_classes: [research]
  budget_class: research-standard
  distribution_metadata: {}
  policy_revision: 7
```

Odysseus never adds unsupported fields to upstream profile payloads. Profile resolution is tested against the pinned upstream package, including `extra="forbid"`, snapshot immutability, and native/ACP launch behavior.

## 8. OdysseusAgent

`OdysseusAgent` is first-party OpenHands behavior for daily product tasks. Initial implementation uses composition:

- `CodeActAgent`;
- Odysseus system suffix;
- selected skills;
- Odysseus MCP configuration;
- archetype context;
- profile policy and workspace grants.

Subclass/plugin work is allowed only after a concrete lifecycle, result, delegation, or tool behavior cannot be expressed through supported composition. `OdysseusAgent` contains no business rules, direct database access, provider client, scheduler, transcript store, or independent tool loop.

## 9. Archetype contracts

Archetypes describe work, not agent identity. They are immutable, independently versioned execution contracts.

```yaml
TaskArchetype:
  id: deep-research
  version: 2
  execution_kind: agent
  input_schema: DeepResearchInputV2
  result_schema: DeepResearchResultV2
```

Shared fields:

- ID and version;
- typed input and result;
- required/optional artifacts;
- required/optional capabilities;
- risk class and approval policy;
- resource constraints;
- budget and timeout;
- audit requirements;
- completion criteria;
- failure and retry policy;
- compatible runtime/model traits.

### 9.1 Agent archetypes

Examples: `chat`, `coding-task`, `deep-research`, `document-rewrite`, `email-draft-reply`, `task-execute`, `personal-assistant-checkin`, `skill-test`.

Additional fields:

- preferred profile traits;
- conversation policy;
- workspace policy;
- delegation policy and depth;
- checkpoint/resume policy;
- interaction policy.

### 9.2 Model-job archetypes

Examples: `session-title`, `calendar-classify-event`, `memory-extract`, `structured-extraction`, `speech-transcript-cleanup`.

Additional fields:

- input/output schema;
- model capability requirements;
- token limit;
- temperature/determinism policy;
- retry rules;
- tool prohibition or explicit bounded allowlist.

Model jobs use a small governed Odysseus executor unless upstream gains a lightweight suitable primitive. This executor rejects open-ended loops, arbitrary MCP, durable conversations, delegation, and workspaces.

Canonical rule: all product reasoning and generative model work executes through a versioned governed archetype. Agentic work runs through OpenHands; bounded inference may run as a model job.

## 10. Execution control

OpenHands Automation is first-party execution control for scheduled, background, event-driven, and—if Phase 1 succeeds—ad-hoc interactive executions.

Odysseus does not create disposable Automation definitions for chat turns. Required ad-hoc primitive:

- no persistent definition;
- accepts an existing `conversation_id`;
- creates a distinct Automation execution ID;
- supports repeated executions against one conversation;
- targets Canvas-created conversations;
- supports idempotent creation using `odysseus_request_id`;
- exposes cancellation, lineage, and conversation association.

If this requires a small upstream extension, contribute or maintain a narrow patch. If it requires recreating a general orchestrator or conflicts with Automation's model, interactive runs may talk directly to Agent Server as an explicit documented exception. Scheduled/background work remains Automation-owned. No hidden fallback is permitted.

## 11. Delegation classes

### 11.1 Local native subagent

```text
Automation execution R100
└── Conversation C10
    ├── parent native agent
    └── OpenHands subagent S1
```

Properties:

- same Automation execution;
- OpenHands-native subagent lifecycle;
- separate child identity and capability subset;
- optional child conversation reference;
- no new Automation execution ID.

Audit identity:

```text
delegation_id
parent_execution_id
child_agent_identity
child_conversation_ref
```

### 11.2 Managed child execution

Use for another AgentProfile/runtime, independent lifecycle, background work, separate cancellation/budget, or another workspace.

```text
R100 / OdysseusAgent
  → delegated deep-research
  → R101 / Hermes ACP
```

Constraints:

```text
child scopes       ⊆ parent scopes
child resources    ⊆ parent resources
child expiry       ≤ parent expiry
child budget       ≤ parent remaining budget
child depth        < parent remaining delegation depth
child archetype    ∈ parent allowed delegations
child profile      compatible with child archetype
```

This distinction preserves OpenHands `TaskToolSet` behavior rather than rebuilding native subagent orchestration.

## 12. Odysseus MCP

MCP is the canonical agent-facing capability boundary, not the domain implementation.

```text
agent action/resource request
  → workload authentication
  → delegated authorization
  → agent-facing schema validation
  → audit/rate/budget enforcement
  → Odysseus domain service/API
```

Candidate namespaces:

- `notes`;
- `documents`;
- `mail`;
- `calendar`;
- `memory`;
- `research`;
- `tasks`;
- `sessions`;
- `gallery`;
- `notifications`;
- `skills`.

Reads should use MCP resources where appropriate. Mutations use semantic tools. Native OpenHands tools are exceptions for streaming, high-frequency workspace access, large/binary transfer, checkpoints, cancellation, or deep sandbox integration. Any native adapter still calls the same Odysseus service/API and cannot introduce independent domain semantics.

ACP forwarding failure may use a narrow trusted MCP adapter/proxy. It may not weaken policy through arbitrary REST access.

## 13. Delegated authorization

Workload authentication answers who is connecting. A run token answers what this execution may do. Both are required for owner-level domain access.

Minimum run-token claims:

- issuer and audience;
- token ID;
- owner, workspace, and organization where applicable;
- AgentProfile ID and immutable revision;
- archetype ID and version;
- conversation ID;
- Automation execution ID;
- scopes and resource constraints;
- budget;
- allowed MCP servers/tool groups;
- delegation parent and remaining depth;
- policy revision;
- issue and expiry times.

Agents receive no refresh credential and cannot broaden authority. Scheduled executions receive fresh tokens and current policy/profile resolution on every invocation. Conversation lifetime never implies authorization lifetime.

### 13.1 Runtime credential rotation gate

Phase 1 must prove this without restarting conversation/workspace:

```text
T1 active
  → T2 issued by orchestrator-controlled authority
  → live native/ACP execution obtains T2
  → T1 stops being accepted
  → agent and MCP continue normally
```

Environment reinjection is not assumed. If direct rotation is unreliable, preferred fallback is a trusted local MCP broker:

```text
agent/ACP
  → stable local MCP connection
  → trusted broker holds current T1/T2/T3
  → Odysseus MCP
```

Broker remains thin: workload binding, current-token attachment, rotation, redaction, and transport. It contains no domain behavior and grants no broader scope.

## 14. Approval

OpenHands confirmation and Odysseus authorization are distinct:

```text
agent proposes action
  → OpenHands creates canonical pending ActionEvent
  → OpenHands confirmation policy pauses execution
  → user confirms exact pending action
  → Odysseus Approval Authority verifies confirmation and execution
  → authority signs ApprovalGrant
  → actual MCP invocation
  → MCP verifies delegation and ApprovalGrant
  → domain service performs side effect
```

OpenHands supplies lifecycle and evidence. Odysseus controls domain PKI. MCP validates approval; it does not originate confirmation.

ApprovalGrant binds:

- issuer/audience;
- owner;
- execution and conversation;
- delegation, if any;
- profile ID/revision;
- tool name;
- normalized arguments digest;
- resource-constraint digest;
- policy revision;
- approval ID, nonce, issue time, and short expiry.

Assertions are single-use or bound to a domain idempotency key. Any material argument change invalidates approval.

## 15. Interactive and scheduled data flow

### 15.1 Interactive agent request

1. Odysseus authenticates user and resolves archetype/version.
2. Dispatcher filters compatible profiles and product policy selects an immutable revision.
3. Odysseus creates pending projection and stable request ID.
4. Execution control creates an ad-hoc Automation run if Gate 1 passes; otherwise documented interactive Agent Server path applies.
5. Existing or new Agent Server conversation is selected by contract.
6. Token authority authenticates workload and mints bounded delegation.
7. Runtime launches native or ACP profile with workspace, archetype, and MCP access.
8. Agent Server stores canonical events; Automation stores run lifecycle where used.
9. Agent domain access passes through MCP.
10. Odysseus reconciles and projects product state.
11. Result validator checks schema, artifacts, domain commits, and approval obligations.
12. Execution ends; token expires/revokes; projection records terminal product outcome.

### 15.2 Scheduled execution

Odysseus stores schedule intent, recurrence, archetype/version, inputs, enabled state, and preferred profile policy. It stores no capability token or reusable agent credential.

At every fire, Automation creates a new execution. Current owner policy, profile revision, resources, budget, and MCP capabilities are resolved. A fresh token is minted. Execution events/artifacts project back to Odysseus. Token dies at completion.

## 16. Canonical events and product projection

Agent Server owns conversation history. Automation owns its run history. Odysseus stores only foreign IDs and compact product projections.

Projection state intentionally composes:

```text
AutomationRunStatus
  + ConversationExecutionStatus
  + local dispatch/reconciliation state
  = OdysseusProjectedRunStatus
```

States such as `pending_dispatch`, `dispatching`, `starting`, `awaiting_approval`, `cancelling`, and `reconciliation_degraded` are derived, not competing canonical states.

Projection ingestion uses:

- WebSocket incremental events;
- REST history synchronization/reconciliation;
- `(source_system, source_event_id)` idempotency;
- event parent/source ordering metadata;
- local arrival sequence for diagnostics only;
- periodic full reconciliation.

No exactly-once or monotonic `events-after-cursor` API is assumed. A stored reconciliation position is an Odysseus hint, not necessarily an Agent Server cursor.

Unknown/malformed persisted events place projection in `reconciliation_degraded`; they do not imply conversation deletion. Projector preserves diagnostics, continues with readable history where safe, and retries after upstream compatibility is restored.

Agent Canvas and Odysseus address the same canonical conversation. Initial concurrency rule permits one active writer turn per conversation. Conflicts surface explicitly; clients do not merge turns. Cancellation targets an execution, not a conversation. Resume creates a new execution when prior execution ended.

## 17. Result and artifact boundary

Runtime completion is necessary but insufficient:

```text
runtime completed
AND result schema valid
AND required artifacts resolvable
AND required domain writes committed
AND approval obligations satisfied
AND projection reconciled
```

Otherwise product result becomes `incomplete` or `contract_failed` even when upstream runtime says completed.

Artifacts carry execution, conversation, profile revision, archetype version, producer, source, content type, integrity hash where practical, and promoted Odysseus domain ID. Odysseus copies artifacts only when product semantics require durable domain ownership; otherwise it stores foreign reference and display metadata.

## 18. Failure taxonomy

Normalized failure fields:

```yaml
code: string
class: dispatch | runtime | authorization | approval | domain | projection | contract
owner: odysseus | automation | agent-server | agent | acp | mcp | domain
retryability: automatic | manual | prohibited
retry_scope: same-attempt | new-execution | new-conversation | none
safe_message: string
internal_ref: opaque-string
partial_effects: none | possible | confirmed
```

### 18.1 Major classes

- **Pre-dispatch:** invalid archetype/profile/policy/budget; no upstream execution exists.
- **Ambiguous dispatch:** request may have been accepted. Reconcile by stable idempotency key; never retry blindly.
- **Automation launch:** queue/sandbox/image/entrypoint/watchdog failure. Retry as new execution with lineage.
- **Agent Server:** conversation/run/event persistence failure. Reconcile canonical history before retry.
- **Native agent:** provider/context/tool/subagent/contract failure; OpenHands owns internal semantics.
- **ACP:** process/handshake/protocol/model/MCP/cancel/resume failure; retain ACP attribution.
- **MCP authorization:** deny and audit; missing scope never triggers live privilege expansion.
- **Approval:** reject, expire, invalidate, or replay-protect exact action.
- **Domain:** semantic errors and retry classification come from domain service.
- **Projection:** execution continues; UI marks stale/degraded and reconciles.
- **Result contract:** upstream can complete while product outcome remains incomplete.

Secrets, raw credentials, sensitive provider payloads, and unrestricted stack traces never enter user-facing failures or canonical agent events.

## 19. Cancellation and retry

Cancellation targets execution class. It does not delete conversation or roll back completed side effects.

Automation flow:

```text
cancellation_requested
  → cancelling
  → cancelled | cancel_failed | completed_before_cancellation
```

Token authority rejects new high-risk operations after cancellation. In-flight domain operations may finish and must be reconciled by domain idempotency/audit records.

Local subagents share parent cancellation unless stable upstream targeted cancellation exists. Managed child executions default to cancellation with parent; detached continuation requires explicit archetype policy and visible user control.

Agent retry creates a new Automation execution and records `retry_of`, request lineage, and attempt number. Conversation reuse is archetype-specific. Automatic retry requires transient classification, no ambiguous side effect, remaining family budget, permitted policy, and compatible runtime capabilities. Rejected approval, missing scope, cancellation, revocation, and unknown destructive side effects are never blindly retried.

## 20. Degradation

- **Automation unavailable:** scheduled work delays; interactive work reports unavailable. No legacy fallback.
- **Agent Server unavailable:** preserve request/projection; no local agent fallback.
- **MCP unavailable:** domain-dependent work pauses/fails; never bypass through REST.
- **ACP profile unavailable:** alternative profile only through new, explicit, compatible execution provenance.
- **Canvas unavailable:** Odysseus UI and execution continue.
- **Odysseus UI unavailable:** execution may continue; Canvas remains usable where authorized.
- **Projector unavailable:** execution continues; projection marks stale and later reconciles.
- **Token authority unavailable:** no new delegation. Existing token behavior follows TTL and online high-risk checks.

## 21. Security model

Agent runtimes, generated code, ACP implementations, and sandboxes are untrusted workloads. Protected assets include owner domain data, provider credentials, run tokens, approval grants, conversations/events, workspaces, profile/policy definitions, and audit integrity.

Required controls:

- separate workload identity and delegated authority;
- short TTL, audience, execution, profile, archetype, scope, and resource binding;
- no agent refresh authority;
- child delegation subset enforcement;
- OpenHands pre-execution confirmation;
- Odysseus-signed exact-action grant;
- MCP/domain reauthorization and idempotency;
- explicit workspace grants and sandbox cleanup;
- runtime secret injection and aggressive redaction;
- authenticated event sources and reconciliation;
- idempotent dispatch and external side effects;
- one-writer conversation policy initially;
- attributable audit fields: owner, profile revision, conversation, execution, archetype, request, delegation parent.

## 22. Legacy surgery map

Structural search found five production callers of `stream_agent_loop()`:

- `routes/chat_routes.py`;
- `routes/skills_routes.py`;
- `src/teacher_escalation.py`;
- `src/bg_monitor.py`;
- `src/task_scheduler.py`.

Current `src/agent_loop.py` is approximately 6,453 lines and combines provider streaming, tool loops/schemas, local-model repair, tool retrieval, MCP lifecycle, workspace confinement, approvals, detached/background runs, context compaction, fallback models, teacher escalation, and SSE events. These responsibilities move to owners defined above; they are not ported wholesale.

Direct product-LLM work also spans chat, documents, deep research, email, memory, tasks, sessions, skills, calendar, notes, webhooks, presets, compaction, teacher escalation, and model-interaction tools. Every path receives a disposition ledger entry:

```text
old location
current purpose
new archetype or deterministic service
execution kind
new owner
replacement test
deletion status
```

Repository contains roughly 248 related tests. Tests validating desired product behavior should move to new boundaries. Tests encoding legacy loop/event implementation should be deleted after replacement acceptance.

## 23. Cutover gates

### Gate 0: pinned stack

- pin SDK, Agent Server, Canvas, Automation, native agent, ACP clients, OpenCode, and Hermes versions;
- record compatibility matrix;
- prove Docker Desktop control plane and per-run sandbox;
- prove typed OpenHands clients and profile resolution.

### Gate 1: Automation/existing-conversation execution

- new first-class run targets existing conversation;
- no persistent/disposable automation definition;
- distinct run ID per invocation;
- repeated runs work against one conversation;
- Canvas-created conversation works;
- dispatch idempotency, cancellation, lineage, and association work.

If upstream only continues the existing run, decide explicitly between narrow upstream extension and interactive Agent Server exception before platform construction proceeds.

### Gate 2: live delegated-token rotation

- T2 reaches live native and ACP execution;
- T1 stops being accepted;
- workspace/conversation need not restart;
- reconnect continues normally;
- secrets remain absent from logs/events/profiles.

If direct rotation fails, validate thin trusted MCP broker fallback.

### Gate 3: ACP–MCP contract

- OpenCode and Hermes receive correct MCP configuration;
- headers/secrets survive forwarding without persistence/logging;
- reconnect, reissue/broker behavior, scope denial, and cancellation are proven;
- narrow proxy is used only when direct forwarding cannot satisfy contract.

### Gate 4: approval plumbing

- OpenHands pending ActionEvent is stable and addressable;
- user confirmation can be verified by Odysseus;
- Approval Authority signs exact action/context;
- MCP rejects altered, expired, replayed, or cross-execution grants.

### Gate 5: domain boundary

MCP, REST, GUI, and workers call shared domain services. No rule remains exclusively inside legacy agent tool code.

### Gate 6: archetypes/model jobs

- versioned contracts and deterministic compatibility resolution;
- typed result, budget, approval, and audit validation;
- bounded model jobs cannot obtain agent-loop or arbitrary MCP power.

### Gate 7: projection

- REST/WebSocket race, duplicate, restart, late, unknown, and malformed events tested;
- full reconciliation works;
- Automation and conversation status composition works;
- Canvas/Odysseus concurrency works;
- stale/degraded UI state is visible.

### Gate 8: side effects

- confirmation precedes MCP invocation;
- exact-action grant verifies;
- changed arguments and replay fail;
- ambiguous provider results reconcile;
- mail/calendar/task duplicates are prevented.

### Gate 9: migration coverage

- disposition ledger covers every old entry point/direct model call;
- no production caller remains for old loop, tool executor, provider loop, SSE protocol, detached machinery, or unmanaged product LLM call.

### Gate 10: macOS acceptance stack

- Odysseus, Automation, Agent Server, Canvas, MCP, and sandbox start;
- intended endpoints work through Tailscale;
- notes/documents do not return 404;
- native OdysseusAgent, OpenCode, Hermes, chat, research, scheduling, approval, cancellation, restart, and reconciliation pass.

### Gate 11: coordinated cutover/deletion

- backup/recovery proven;
- one controlled switch disables old runtime;
- acceptance suite green;
- no mixed canonical transcript;
- no schedule points to old loop;
- rollback is prior compatible release, not indefinite dual runtime;
- after observation, delete legacy implementation, routes/events, tests, dependencies, and configuration.

## 24. Implementation sequencing

### Phase 1: eliminate architectural unknowns

1. Pin upstream versions and prove topology.
2. Spike new Automation run against existing conversation.
3. Spike live delegated-token rotation and broker fallback.
4. Spike ACP–MCP forwarding for OpenCode and Hermes.
5. Spike OpenHands confirmation to Odysseus ApprovalGrant.
6. Spike event reconciliation including unknown persisted events.

### Phase 2: platform boundaries

1. Define archetype contracts and profile metadata.
2. Implement workload identity and delegation authority.
3. Extract shared domain services.
4. Implement thin Odysseus MCP.
5. Implement typed OpenHands integration and projections.
6. Compose `OdysseusAgent`.

### Phase 3: vertical migrations

1. Chat.
2. Notes and documents.
3. Research/Hermes.
4. Mail and calendar.
5. Tasks and scheduling.
6. Memory and skills.
7. Bounded model jobs and remaining direct LLM paths.

### Phase 4: acceptance and cutover

1. Run security, failure, concurrency, and operational tests.
2. Validate complete macOS Docker/Tailscale stack.
3. Switch all generative paths together.
4. Observe and reconcile.
5. Delete legacy runtime and compatibility-only surface.

Vertical phases reduce engineering risk on a test branch; they do not create production backward compatibility.

## 25. Effort

Raw estimate: **32–69 engineer-weeks**, with Phase 1 upstream uncertainty outside ordinary confidence.

| Staffing | Planning range |
|---|---:|
| 1 strong engineer | 9–17 months |
| 2 engineers | 5–9 months |
| 3 engineers | 4–6 months |
| 4 engineers | 3–5 months |

Main multipliers: Automation extension size, live credential rotation, ACP forwarding, business logic trapped in agent/tool code, event reconciliation behavior, legacy SSE/UI coupling, approval integration, Docker Desktop isolation, existing-test relevance, direct-model call coverage, and upstream release churn.

## 26. Freeze state

Frozen:

- ownership;
- archetypes and agent/model-job split;
- MCP/domain boundary;
- identity and delegated-authorization principles;
- two delegation classes;
- confirmation-before-MCP approval model;
- failure, cancellation, retry, and degradation behavior;
- reconciliation-first projection;
- security model;
- coordinated cutover and legacy deletion.

Provisional integration mechanics:

- new Automation execution against existing conversation;
- live delegated-token rotation or thin broker;
- exact ACP MCP propagation;
- OpenHands confirmation evidence to Odysseus ApprovalGrant.

Phase 1 resolves these mechanics before dependent implementation. Failure of a spike selects its documented branch; it does not silently add a second orchestration platform or weaken domain authorization.
