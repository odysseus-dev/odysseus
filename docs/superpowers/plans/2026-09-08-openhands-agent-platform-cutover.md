# OpenHands Agent Platform Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Odysseus legacy agent and unmanaged product-LLM paths with OpenHands native/ACP agents, governed model jobs, Odysseus MCP, delegated authorization, and native product projections.

**Architecture:** OpenHands Agent Server owns conversations/events; Automation owns scheduled/background runs and ad-hoc runs only if Phase-1 contract tests prove new-run/existing-conversation semantics. Odysseus owns archetypes, profile policy, domain services, delegated tokens, approval grants, MCP, schedules, and projections. Production cuts over once; no compatibility runtime remains.

**Tech Stack:** Python 3, Flask, SQLite/PostgreSQL-compatible persistence patterns already used by Odysseus, Docker Compose, OpenHands Agent Server/SDK/Automation/Agent Canvas, ACP, MCP, pytest, JavaScript tests already present in repository.

## Global Constraints

- Execute Tasks 1–5 before Tasks 6–18; record gate evidence in repository.
- Pin exact OpenHands, Automation, Canvas, OpenCode, and Hermes versions before writing production adapters.
- OpenHands Automation 1.11.0 continuation behavior is evidence, not proof of new-run/existing-conversation semantics.
- Keep upstream `AgentProfile` serialization canonical; store Odysseus policy metadata separately.
- Authenticate workload separately from delegated owner authority.
- Agents receive short-lived execution authority and no refresh credential.
- Confirmation happens in OpenHands before MCP invocation; Odysseus signs domain-verifiable approval grants.
- MCP stays thin and calls shared Odysseus domain services.
- Native and ACP agent internals remain distinct.
- Agent Server event history remains canonical; projections recover through full reconciliation and event-ID deduplication.
- Model jobs cannot open agent loops, workspaces, delegation, or arbitrary MCP access.
- No production dual runtime, legacy fallback, copied transcript, arbitrary ACP REST fallback, or browser-tab integration.
- Use existing dependencies and service patterns before adding packages.
- Every non-trivial task follows failing test, minimal implementation, passing test, focused commit.
- Design source: `docs/superpowers/specs/2026-09-08-openhands-agent-platform-cutover-design.md`.

## File Map

New platform code lives under `services/agents/`; domain services remain in existing `services/*` packages.

| Path | Responsibility |
|---|---|
| `services/agents/contracts.py` | Shared IDs, execution references, failures, and projection enums |
| `services/agents/archetypes.py` | Versioned archetype loading and validation |
| `services/agents/profiles.py` | Adjacent Odysseus profile policy and compatibility filtering |
| `services/agents/openhands_client.py` | Typed boundary around pinned Agent Server/Automation clients |
| `services/agents/delegation.py` | Token claims, issuance, verification, narrowing, and revocation checks |
| `services/agents/approvals.py` | ApprovalGrant normalization, signing, verification, and consumption |
| `services/agents/model_jobs.py` | Bounded typed model-job execution |
| `services/agents/projection.py` | Automation/conversation event reconciliation and derived status |
| `services/agents/dispatcher.py` | Archetype/profile resolution and execution dispatch |
| `services/agents/legacy_ledger.py` | Machine-checkable legacy-call disposition manifest |
| `mcp_servers/odysseus_server.py` | Thin agent-facing MCP adapter |
| `routes/agent_routes.py` | Odysseus native agent request/status/approval/cancel endpoints |
| `static/js/agents.js` | Native Odysseus agent event/status UI controller |
| `static/index.html` | Product agent panel markup; no embedded Canvas |
| `config/agents/archetypes/*.yaml` | First-party immutable archetype definitions |
| `config/agents/profile-policy.yaml` | Odysseus metadata keyed to upstream profile revisions |
| `deploy/openhands/*.yaml` | Pinned profiles and runtime configuration |
| `docker-compose.openhands.yml` | Separate control-plane, Automation, Canvas, MCP, and sandbox integration |
| `scripts/openhands_probe.py` | Read-only/isolated Phase-1 contract probe |
| `tests/agents/*` | Unit and contract tests for platform boundaries |
| `tests/integration/openhands/*` | Pinned-stack integration and acceptance tests |

Existing large modules are not expanded. Call sites move incrementally to focused services, then obsolete code is deleted.

---

### Task 1: Pin stack and create executable feasibility harness

**Files:**
- Create: `deploy/openhands/versions.env`
- Create: `docker-compose.openhands.yml`
- Create: `scripts/openhands_probe.py`
- Create: `tests/integration/openhands/test_stack_contract.py`
- Modify: `.env.example`
- Modify: `tests/README.md`

**Interfaces:**
- Consumes: Docker socket and existing Odysseus Compose network.
- Produces: `ProbeResult(name: str, passed: bool, evidence: dict[str, object])`; pinned service endpoints and versions used by Tasks 2–5.

- [ ] **Step 1: Record exact image/package revisions**

Write `deploy/openhands/versions.env` with immutable image digests or release tags verified from official artifacts. Include Agent Server SDK, Automation, Canvas, OpenCode, and Hermes. Do not use `latest`.

- [ ] **Step 2: Write failing pin contract**

```python
def test_all_first_party_components_are_pinned(version_file):
    values = version_file("deploy/openhands/versions.env")
    assert values.keys() >= {
        "OPENHANDS_AGENT_SERVER_IMAGE", "OPENHANDS_AUTOMATION_IMAGE",
        "OPENHANDS_CANVAS_IMAGE", "OPENCODE_VERSION", "HERMES_VERSION",
    }
    assert all(value and ":latest" not in value for value in values.values())
```

- [ ] **Step 3: Run pin contract and verify failure**

Run: `pytest tests/integration/openhands/test_stack_contract.py -v`  
Expected: FAIL because pinned file/harness does not exist.

- [ ] **Step 4: Add Compose overlay and probe**

Compose services must be distinct: `openhands-agent-server`, `openhands-automation`, `openhands-canvas`, and `odysseus-mcp`. Bind internal APIs to Compose network; publish only configured Odysseus/Canvas ports. Probe must emit JSON and return nonzero when version, health, or connectivity differs from pins.

```python
@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    evidence: dict[str, object]
```

- [ ] **Step 5: Prove isolated stack**

Run: `docker compose -f docker-compose.yml -f docker-compose.openhands.yml config --quiet`  
Expected: exit 0.

Run: `python scripts/openhands_probe.py stack --json`  
Expected: JSON entries for all four services with `passed: true` and exact reported versions.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/integration/openhands/test_stack_contract.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add deploy/openhands/versions.env docker-compose.openhands.yml scripts/openhands_probe.py tests/integration/openhands/test_stack_contract.py .env.example tests/README.md
git commit -m "build: pin OpenHands integration stack"
```

### Task 2: Resolve Automation execution semantics gate

**Files:**
- Modify: `scripts/openhands_probe.py`
- Create: `tests/integration/openhands/test_automation_existing_conversation.py`
- Create: `docs/architecture/evidence/automation-existing-conversation.md`

**Interfaces:**
- Consumes: pinned Automation and Agent Server endpoints from Task 1.
- Produces: `probe_automation_existing_conversation() -> ProbeResult`; one explicit result: `distinct_run`, `continued_run`, or `unsupported`.

- [ ] **Step 1: Write contract test for exact identity semantics**

```python
def test_new_automation_execution_targets_existing_conversation(stack):
    first = stack.start_adhoc(message="first")
    stack.wait_idle(first.conversation_id)
    second = stack.start_adhoc(message="second", conversation_id=first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert second.execution_id != first.execution_id
    assert stack.execution(second.execution_id).request_key == second.request_key
```

Add tests proving Canvas-created conversation targeting, stable idempotency key, exact-run cancellation, and no disposable Automation definition.

- [ ] **Step 2: Run contract against unextended pinned stack**

Run: `pytest tests/integration/openhands/test_automation_existing_conversation.py -v`  
Expected: PASS only if pinned upstream exposes distinct-run behavior; otherwise failure output records actual continuation behavior.

- [ ] **Step 3: Select smallest supported branch**

Implement probe classification:

```python
class AutomationConversationMode(StrEnum):
    DISTINCT_RUN = "distinct_run"
    CONTINUED_RUN = "continued_run"
    UNSUPPORTED = "unsupported"
```

If `DISTINCT_RUN`, production dispatcher may use Automation for interactive turns. If `CONTINUED_RUN` or `UNSUPPORTED`, record interactive Agent Server execution as explicit exception unless a narrow upstream patch makes contract pass without creating Odysseus orchestration.

- [ ] **Step 4: Write evidence record**

Evidence must contain pins, API calls, returned IDs, idempotency result, cancellation result, conversation origin, and selected branch. No credentials or raw sensitive payloads.

- [ ] **Step 5: Re-run and commit**

Run: `python scripts/openhands_probe.py automation-existing-conversation --json`  
Expected: one classified result with evidence.

```bash
git add scripts/openhands_probe.py tests/integration/openhands/test_automation_existing_conversation.py docs/architecture/evidence/automation-existing-conversation.md
git commit -m "test: resolve Automation conversation execution semantics"
```

### Task 3: Resolve live credential rotation gate

**Files:**
- Modify: `scripts/openhands_probe.py`
- Create: `tests/integration/openhands/test_runtime_credential_rotation.py`
- Create: `docs/architecture/evidence/runtime-credential-rotation.md`
- Create only if direct rotation fails: `services/agents/mcp_broker.py`
- Create only if direct rotation fails: `tests/agents/test_mcp_broker.py`

**Interfaces:**
- Produces: `CredentialDeliveryMode.DIRECT_ROTATION` or `CredentialDeliveryMode.BROKER`; broker exposes stable local MCP connection while selecting current server-side delegation.

- [ ] **Step 1: Write failing live-rotation contract**

```python
@pytest.mark.parametrize("profile", ["openhands", "opencode", "hermes"])
def test_live_runtime_rotates_without_restart(stack, profile):
    run = stack.start_token_probe(profile, token="T1")
    before = stack.runtime_identity(run)
    stack.rotate_probe_token(run, old="T1", new="T2")
    assert stack.call_mcp(run, "T2").status == 200
    assert stack.call_mcp(run, "T1").status == 401
    assert stack.runtime_identity(run) == before
```

- [ ] **Step 2: Run against direct injection path**

Run: `pytest tests/integration/openhands/test_runtime_credential_rotation.py -v`  
Expected: PASS for every profile or evidence-backed failure selecting broker branch.

- [ ] **Step 3: Implement broker only when required**

Broker API stays transport-only:

```python
class DelegationResolver(Protocol):
    def current_token(self, *, execution_id: str, workload_id: str) -> str: ...

class McpBroker:
    def forward(self, request: McpRequest, context: WorkloadContext) -> McpResponse: ...
```

Tests must prove token never appears in agent-visible environment, broker logs, Agent Server events, or ACP profile snapshots; old token fails immediately after rotation.

- [ ] **Step 4: Record selected delivery mode and rerun**

Run: `python scripts/openhands_probe.py credential-rotation --json`  
Expected: mode, runtime identities, old-token rejection, and redaction checks all present.

- [ ] **Step 5: Commit**

```bash
git add scripts/openhands_probe.py tests/integration/openhands/test_runtime_credential_rotation.py docs/architecture/evidence/runtime-credential-rotation.md services/agents/mcp_broker.py tests/agents/test_mcp_broker.py
git commit -m "test: resolve live MCP credential delivery"
```

Omit broker paths from `git add` when direct rotation passes and those files do not exist.

### Task 4: Resolve ACP–MCP forwarding gate

**Files:**
- Modify: `scripts/openhands_probe.py`
- Create: `tests/integration/openhands/test_acp_mcp_contract.py`
- Create: `docs/architecture/evidence/acp-mcp-contract.md`
- Create only if needed: `services/agents/acp_mcp_proxy.py`
- Create only if needed: `tests/agents/test_acp_mcp_proxy.py`

**Interfaces:**
- Consumes: credential delivery mode from Task 3.
- Produces: per-profile capability record for forwarding, reconnect, resume, cancellation, and secret handling.

- [ ] **Step 1: Write parameterized ACP contract**

```python
@pytest.mark.parametrize("profile", ["opencode", "hermes"])
def test_acp_receives_scoped_mcp_without_secret_leak(stack, profile):
    run = stack.start_acp_probe(profile, scopes={"notes.read"})
    assert stack.mcp_call(run, "notes.read").allowed
    assert not stack.mcp_call(run, "mail.send").allowed
    assert not stack.find_secret(run.token_id, sources=("logs", "events", "profile", "workspace"))
```

Add reconnect and cancellation tests. Record unsupported behavior as capability metadata, not native-agent behavior.

- [ ] **Step 2: Run direct forwarding test**

Run: `pytest tests/integration/openhands/test_acp_mcp_contract.py -v`  
Expected: PASS or exact failing profile/transport behavior.

- [ ] **Step 3: Add narrow proxy only for demonstrated transport gap**

Proxy may translate configuration/transport and attach broker context. It may not call Odysseus REST, store durable tokens, or implement domain rules.

- [ ] **Step 4: Write evidence and commit**

Run: `python scripts/openhands_probe.py acp-mcp --json`  
Expected: OpenCode and Hermes capability matrices with all security assertions passing.

```bash
git add scripts/openhands_probe.py tests/integration/openhands/test_acp_mcp_contract.py docs/architecture/evidence/acp-mcp-contract.md services/agents/acp_mcp_proxy.py tests/agents/test_acp_mcp_proxy.py
git commit -m "test: prove ACP MCP capability contract"
```

### Task 5: Resolve confirmation-to-ApprovalGrant gate

**Files:**
- Modify: `scripts/openhands_probe.py`
- Create: `tests/integration/openhands/test_confirmation_approval_grant.py`
- Create: `docs/architecture/evidence/approval-grant-plumbing.md`

**Interfaces:**
- Produces: stable `PendingActionEvidence` captured from canonical Agent Server event and a delivery path for `ApprovalGrant` before MCP invocation.

- [ ] **Step 1: Write failing lifecycle test**

```python
def test_mcp_side_effect_waits_for_odysseus_grant(stack):
    run = stack.propose("mail.send", {"to": ["a@example.test"], "body": "x"})
    pending = stack.wait_for_confirmation(run)
    assert stack.domain_calls("mail.send") == []
    stack.confirm(pending.event_id)
    grant = stack.issue_grant(pending.event_id)
    stack.resume_with_grant(run, grant)
    assert len(stack.domain_calls("mail.send")) == 1
```

Add altered-argument, expired, replay, cross-execution, and rejected-confirmation tests.

- [ ] **Step 2: Run against pinned confirmation lifecycle**

Run: `pytest tests/integration/openhands/test_confirmation_approval_grant.py -v`  
Expected: lifecycle evidence identifies stable pending event and injection point; MCP sees no invocation before confirmation.

- [ ] **Step 3: Record narrow adapter contract**

Evidence specifies fields available from ActionEvent, confirmation identity, normalized argument source, grant delivery mechanism, and whether upstream patch is required. OpenHands never holds Odysseus signing key.

- [ ] **Step 4: Commit**

```bash
git add scripts/openhands_probe.py tests/integration/openhands/test_confirmation_approval_grant.py docs/architecture/evidence/approval-grant-plumbing.md
git commit -m "test: prove OpenHands approval grant plumbing"
```

### Task 6: Define platform contracts, archetypes, and profile policy

**Files:**
- Create: `services/agents/__init__.py`
- Create: `services/agents/contracts.py`
- Create: `services/agents/archetypes.py`
- Create: `services/agents/profiles.py`
- Create: `config/agents/archetypes/chat-v1.yaml`
- Create: `config/agents/archetypes/deep-research-v1.yaml`
- Create: `config/agents/archetypes/model-job-v1.yaml`
- Create: `config/agents/profile-policy.yaml`
- Create: `tests/agents/test_contracts.py`
- Create: `tests/agents/test_archetypes.py`
- Create: `tests/agents/test_profiles.py`

**Interfaces:**
- Produces: `TaskArchetype`, `ExecutionKind`, `AutomationExecutionRef`, `ModelJobRef`, `OdysseusProfilePolicy`, `select_compatible_profiles()`.

- [ ] **Step 1: Write failing immutable-contract tests**

```python
def test_agent_and_model_job_ids_cannot_be_confused():
    assert AutomationExecutionRef("r1", "c1") != ModelJobRef("m1")

def test_profile_requires_exact_upstream_revision():
    with pytest.raises(ValueError):
        OdysseusProfilePolicy(agent_profile_id="hermes", agent_profile_revision=None)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/agents/test_contracts.py tests/agents/test_archetypes.py tests/agents/test_profiles.py -v`  
Expected: import failures.

- [ ] **Step 3: Implement minimum frozen dataclasses/enums and strict loaders**

```python
class ExecutionKind(StrEnum):
    AGENT = "agent"
    MODEL_JOB = "model-job"

@dataclass(frozen=True)
class AutomationExecutionRef:
    automation_execution_id: str
    conversation_id: str

@dataclass(frozen=True)
class ModelJobRef:
    model_job_id: str
```

Reject unknown keys, duplicate `(id, version)`, missing result schemas, agent-only fields on model jobs, model-job-only fields on agents, and profile/archetype incompatibility.

- [ ] **Step 4: Test and commit**

Run: `pytest tests/agents/test_contracts.py tests/agents/test_archetypes.py tests/agents/test_profiles.py -v`  
Expected: PASS.

```bash
git add services/agents config/agents tests/agents/test_contracts.py tests/agents/test_archetypes.py tests/agents/test_profiles.py
git commit -m "feat: define governed execution contracts"
```

### Task 7: Implement delegated authority

**Files:**
- Create: `services/agents/delegation.py`
- Create: `tests/agents/test_delegation.py`
- Modify: `src/secret_storage.py`

**Interfaces:**
- Produces: `DelegationClaims`, `DelegationAuthority.issue()`, `.verify()`, `.issue_child()`, `.reissue()`.

- [ ] **Step 1: Write failing security tests**

```python
def test_child_cannot_expand_parent(authority, parent):
    with pytest.raises(DelegationDenied):
        authority.issue_child(parent, scopes={"notes.read", "mail.send"})

def test_agent_cannot_reissue(authority, token):
    with pytest.raises(WorkloadAuthenticationRequired):
        authority.reissue(token, workload=None)
```

Cover audience, expiry, execution state, profile/archetype revisions, resource subset, budget, depth, cancellation, token ID, and log-safe errors.

- [ ] **Step 2: Verify failures**

Run: `pytest tests/agents/test_delegation.py -v`  
Expected: import failures.

- [ ] **Step 3: Implement minimal signed claims using existing secret/key facilities**

Do not add JWT dependency if existing signing primitives or standard-library HMAC satisfy local deployment requirements. Use constant-time signature comparison. Persist only revocation/execution-state data needed by policy.

- [ ] **Step 4: Test and commit**

Run: `pytest tests/agents/test_delegation.py tests/test_api_key_file_permissions.py -v`  
Expected: PASS.

```bash
git add services/agents/delegation.py tests/agents/test_delegation.py src/secret_storage.py
git commit -m "feat: add execution-scoped delegation authority"
```

### Task 8: Implement Approval Authority

**Files:**
- Create: `services/agents/approvals.py`
- Create: `tests/agents/test_approvals.py`
- Modify: `src/tool_approvals.py`

**Interfaces:**
- Produces: `normalize_action()`, `ApprovalAuthority.issue_from_confirmation()`, `.verify_and_consume()`.

- [ ] **Step 1: Write failing exact-action tests**

```python
def test_changed_recipient_invalidates_grant(authority, confirmed_action):
    grant = authority.issue_from_confirmation(confirmed_action)
    changed = confirmed_action.with_args(to=["b@example.test"])
    with pytest.raises(ApprovalDenied):
        authority.verify_and_consume(grant, changed)
```

Cover canonical JSON normalization, nonce, expiry, replay, execution/profile/tool/resource binding, rejection, and idempotent duplicate result lookup.

- [ ] **Step 2: Verify failure, implement, and run**

Run before implementation: `pytest tests/agents/test_approvals.py -v`  
Expected: import failures.

Use deterministic UTF-8 JSON with sorted keys and compact separators before hashing. Reuse signing/key storage from Task 7.

Run after implementation: `pytest tests/agents/test_approvals.py tests/test_approved_replay_message_shape.py -v`  
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add services/agents/approvals.py tests/agents/test_approvals.py src/tool_approvals.py
git commit -m "feat: issue domain approval grants"
```

### Task 9: Extract shared domain services and expose thin MCP

**Files:**
- Create: `mcp_servers/odysseus_server.py`
- Create: `tests/agents/test_odysseus_mcp.py`
- Modify: `services/docs/service.py`
- Modify: `services/research/service.py`
- Modify: `services/memory/service.py`
- Modify: `routes/calendar_routes.py`
- Modify: `routes/email_routes.py`
- Modify: `routes/note/note_routes.py`
- Modify: `routes/task/task_routes.py`
- Modify: existing service modules selected by disposition ledger for mail/calendar/notes/tasks/sessions/gallery/notifications

**Interfaces:**
- Consumes: verified workload, delegation, optional ApprovalGrant.
- Produces: semantic MCP resources/actions that call shared domain services.

- [ ] **Step 1: Build operation inventory test**

```python
@pytest.mark.parametrize("operation", REQUIRED_OPERATIONS)
def test_mcp_operation_calls_domain_service_not_route(operation, registry):
    assert registry[operation].target_layer == "service"
```

Inventory includes notes, documents, mail, calendar, memory, research, tasks, sessions, gallery, notifications, and retained skills.

- [ ] **Step 2: Move business logic one domain at a time**

For each operation: capture current behavior in service test, move shared rule to existing/new focused service function, make GUI/worker route call it, then expose MCP adapter. Mutating tools require scope, constraints, idempotency key, and ApprovalGrant where policy requires it.

- [ ] **Step 3: Prove deny/default behavior**

Run: `pytest tests/agents/test_odysseus_mcp.py tests/test_*owner_scope*.py -v`  
Expected: missing workload, token, scope, resource grant, or approval always fails before service mutation.

- [ ] **Step 4: Commit by domain**

Use one focused commit per domain, for example:

```bash
git commit -m "refactor: share notes domain operations with MCP"
git commit -m "refactor: share mail domain operations with MCP"
```

### Task 10: Implement OpenHands boundary and dispatcher

**Files:**
- Create: `services/agents/openhands_client.py`
- Create: `services/agents/dispatcher.py`
- Create: `tests/agents/test_openhands_client.py`
- Create: `tests/agents/test_dispatcher.py`
- Modify: `src/app_initializer.py`

**Interfaces:**
- Produces: `OpenHandsClient.create_or_resume()`, `.cancel_execution()`, `.get_execution()`, `.conversation_events()`; `AgentDispatcher.dispatch(request) -> AutomationExecutionRef`.

- [ ] **Step 1: Write boundary tests against fake typed client**

```python
def test_dispatch_is_idempotent(dispatcher):
    first = dispatcher.dispatch(request_id="o1", archetype="chat", payload={"text": "hi"})
    second = dispatcher.dispatch(request_id="o1", archetype="chat", payload={"text": "hi"})
    assert second == first
```

Test selected Task-2 branch explicitly. No raw HTTP calls outside `openhands_client.py`.

- [ ] **Step 2: Implement minimum adapter using pinned generated/typed client**

Map upstream failures to normalized failure envelope without erasing source owner. Inject exact profile revision, archetype version, request ID, workspace grants, and credential-delivery mode.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/agents/test_openhands_client.py tests/agents/test_dispatcher.py -v`  
Expected: PASS.

```bash
git add services/agents/openhands_client.py services/agents/dispatcher.py tests/agents/test_openhands_client.py tests/agents/test_dispatcher.py src/app_initializer.py
git commit -m "feat: dispatch governed OpenHands executions"
```

### Task 11: Implement reconciliation-first projection

**Files:**
- Create: `services/agents/projection.py`
- Create: `tests/agents/test_projection.py`
- Modify: `src/database.py`

**Interfaces:**
- Produces: `AgentRunProjection`, `ProjectionStore.apply()`, `ProjectionReconciler.reconcile()`.

- [ ] **Step 1: Write event-race tests**

```python
def test_reconcile_is_idempotent(reconciler, rest_events, websocket_overlap):
    reconciler.ingest(websocket_overlap)
    reconciler.reconcile(rest_events)
    first = reconciler.snapshot()
    reconciler.reconcile(rest_events)
    assert reconciler.snapshot() == first
```

Cover duplicates, REST/WebSocket inversion, parent IDs, late terminal events, projector restart, unknown event kind, partially unreadable history, and composed Automation/conversation/local status.

- [ ] **Step 2: Implement transactional projection**

Persist unique `(source_system, source_event_id)`, raw safe metadata, parent/source order, arrival sequence, reconciliation position, derived status, and stale/degraded flag. Unknown events are quarantined; conversation is not deleted.

- [ ] **Step 3: Test and commit**

Run: `pytest tests/agents/test_projection.py -v`  
Expected: PASS.

```bash
git add services/agents/projection.py tests/agents/test_projection.py src/database.py
git commit -m "feat: reconcile OpenHands run projections"
```

### Task 12: Implement native Odysseus endpoints and UI

**Files:**
- Create: `routes/agent_routes.py`
- Create: `tests/test_agent_routes_openhands.py`
- Create: `static/js/agents.js`
- Create: `tests/test_agent_ui_js.py`
- Modify: `app.py`
- Modify: `static/index.html`
- Modify: `static/js/init.js`

**Interfaces:**
- Produces: create/message/status/events/approve/cancel/resume endpoints over dispatcher/projection; native textual UI; “Open in Agent Canvas” URL.

- [ ] **Step 1: Write route authorization and idempotency tests**

```python
def test_cancel_targets_execution_not_conversation(client, run):
    response = client.post(f"/api/agents/executions/{run.execution_id}/cancel")
    assert response.status_code == 202
    assert run.conversation_id not in response.get_json().get("cancelled_conversations", [])
```

- [ ] **Step 2: Implement routes over services only**

Routes must not call provider APIs, MCP domain tools, or database business logic directly. Expose derived stale/reconnecting/synchronizing/degraded states.

- [ ] **Step 3: Implement minimal native UI**

Support launch, message, status, progress, approval, artifacts, cancel/resume, and Canvas link. Do not embed Canvas or recreate its advanced diagnostics.

- [ ] **Step 4: Run route and JS tests, then commit**

Run: `pytest tests/test_agent_routes_openhands.py tests/test_agent_ui_js.py -v`  
Expected: PASS.

```bash
git add routes/agent_routes.py tests/test_agent_routes_openhands.py static/js/agents.js static/index.html static/js/init.js tests/test_agent_ui_js.py app.py
git commit -m "feat: add native OpenHands agent experience"
```

### Task 13: Implement bounded model jobs

**Files:**
- Create: `services/agents/model_jobs.py`
- Create: `tests/agents/test_model_jobs.py`
- Modify: `src/ai_interaction.py`

**Interfaces:**
- Produces: `ModelJobExecutor.execute(archetype, input, owner) -> ModelJobResult`.

- [ ] **Step 1: Write boundary tests**

```python
def test_model_job_rejects_agent_capabilities(executor, model_job_archetype):
    model_job_archetype.tools = ["odysseus.mail.send"]
    with pytest.raises(InvalidArchetype):
        executor.execute(model_job_archetype, {}, owner="u1")
```

Test typed output, budget, timeout, audit, schema failure, bounded retry, no conversation, no workspace, no delegation, and no arbitrary tools.

- [ ] **Step 2: Implement over existing model invocation primitive**

Keep one structured request boundary. Preserve infrastructure exemptions for embeddings, STT/TTS, image generation, moderation, and capability probes.

- [ ] **Step 3: Run and commit**

Run: `pytest tests/agents/test_model_jobs.py tests/test_ai_interaction_owner_scope.py -v`  
Expected: PASS.

```bash
git add services/agents/model_jobs.py tests/agents/test_model_jobs.py src/ai_interaction.py
git commit -m "feat: govern bounded model jobs"
```

### Task 14: Package first-party agents and delegation

**Files:**
- Create: `deploy/openhands/profiles/odysseus.yaml`
- Create: `deploy/openhands/profiles/opencode.yaml`
- Create: `deploy/openhands/profiles/hermes.yaml`
- Create: `deploy/openhands/agents/odysseus-system.md`
- Create: `tests/integration/openhands/test_first_party_profiles.py`
- Create: `tests/integration/openhands/test_delegation_classes.py`

**Interfaces:**
- Produces: resolvable native OdysseusAgent composition, OpenCode ACP, Hermes ACP, local native subagent, and managed child execution.

- [ ] **Step 1: Write profile resolution/launch tests**

```python
@pytest.mark.parametrize("profile", ["odysseus", "opencode", "hermes"])
def test_profile_revision_is_snapshotted(stack, profile):
    launched = stack.launch_profile(profile)
    assert launched.profile_id == profile
    assert launched.profile_revision
    assert launched.snapshot_rejects_unknown_fields
```

- [ ] **Step 2: Compose OdysseusAgent without subclass**

Use CodeActAgent, system suffix, approved skills, MCP configuration, and archetype context. Test absence of direct database/domain/provider access.

- [ ] **Step 3: Prove both delegation classes**

Local subagent shares parent execution and narrows capability through `delegation_id`. Hermes managed child gets new execution, profile revision, budget, workspace policy, and subset token.

- [ ] **Step 4: Run and commit**

Run: `pytest tests/integration/openhands/test_first_party_profiles.py tests/integration/openhands/test_delegation_classes.py -v`  
Expected: PASS.

```bash
git add deploy/openhands/profiles deploy/openhands/agents tests/integration/openhands/test_first_party_profiles.py tests/integration/openhands/test_delegation_classes.py
git commit -m "feat: package first-party OpenHands agents"
```

### Task 15: Migrate product vertical slices

**Files:**
- Modify: `routes/chat_routes.py`
- Modify: `routes/skills_routes.py`
- Modify: `src/teacher_escalation.py`
- Modify: `src/bg_monitor.py`
- Modify: `src/task_scheduler.py`
- Modify: direct-LLM callers listed in design §22
- Create: `config/agents/legacy-disposition.yaml`
- Create: `tests/test_agent_migration_manifest.py`
- Modify: relevant existing behavior tests per migrated domain

**Interfaces:**
- Consumes: dispatcher, model-job executor, shared domain services.
- Produces: no production entry point to legacy loop or unmanaged product LLM.

- [ ] **Step 1: Make disposition ledger exhaustive**

Each entry contains old symbol/location, purpose, target archetype or deterministic service, execution kind, owner, replacement test, and deletion status. AST/text scan test fails for uncovered callers.

- [ ] **Step 2: Migrate slices in fixed order**

Order: chat; notes/documents; research; mail/calendar; tasks/scheduling; memory/skills; remaining bounded model jobs. For each slice, first preserve desired behavior in boundary tests, then switch caller, then mark ledger entry replaced.

- [ ] **Step 3: Prove five loop callers are gone**

Run:

```bash
ast-grep --pattern 'stream_agent_loop($$$ARGS)' --lang python routes src
```

Expected: no production matches after all slice commits.

- [ ] **Step 4: Prove unmanaged model calls are gone**

Run ledger scanner in `tests/test_agent_migration_manifest.py`; allow only enumerated infrastructure exemptions. Expected: PASS with zero unknown invocation sites.

- [ ] **Step 5: Commit each vertical slice**

```bash
git commit -m "feat: move chat to governed agents"
git commit -m "feat: move research to governed agents"
```

Repeat focused naming for remaining slices.

### Task 16: Migrate scheduling to Automation

**Files:**
- Modify: `src/task_scheduler.py`
- Modify: `routes/assistant_routes.py`
- Modify: `src/bg_monitor.py`
- Create: `tests/agents/test_automation_scheduling.py`

**Interfaces:**
- Produces: fresh Automation execution/profile revision/token per schedule fire; no stored capability credential.

- [ ] **Step 1: Write fresh-authority test**

```python
def test_each_schedule_fire_resolves_current_policy(scheduler):
    first = scheduler.fire("schedule-1")
    scheduler.set_policy_revision(8)
    second = scheduler.fire("schedule-1")
    assert first.execution_id != second.execution_id
    assert first.token_id != second.token_id
    assert second.policy_revision == 8
```

- [ ] **Step 2: Replace legacy dispatch**

Persist schedule intent only. Let Automation own dispatch/run history/sandbox lifecycle. Keep Odysseus schedule ID as correlation metadata.

- [ ] **Step 3: Test and commit**

Run: `pytest tests/agents/test_automation_scheduling.py tests/test_compute_next_run_monthly_clamp.py -v`  
Expected: PASS.

```bash
git add src/task_scheduler.py routes/assistant_routes.py src/bg_monitor.py tests/agents/test_automation_scheduling.py
git commit -m "feat: dispatch schedules through OpenHands Automation"
```

### Task 17: Run platform acceptance and security gates

**Files:**
- Create: `tests/integration/openhands/test_platform_acceptance.py`
- Create: `tests/integration/openhands/test_platform_security.py`
- Create: `docs/operations/openhands-stack.md`
- Modify: `specs/testing-devops.md`

**Interfaces:**
- Produces: one repeatable macOS Docker/Tailscale acceptance command and evidence for cutover decision.

- [ ] **Step 1: Encode end-to-end acceptance**

Test stack startup, sandbox creation, native chat, OpenCode task, Hermes research, scheduling, approval, altered-grant rejection, cancellation, token rotation/broker, projection restart, unknown event degradation, Canvas/Odysseus shared conversation, notes/documents routes, and intended Tailscale endpoints.

- [ ] **Step 2: Encode adversarial checks**

Test scope escalation, cross-owner token, workload/token mismatch, child expansion, replay, duplicate dispatch, duplicate side effect, workspace mount escape, secret search across logs/events/profile/workspace, and Canvas/Odysseus write conflict.

- [ ] **Step 3: Run focused and full suites**

Run: `pytest tests/agents tests/integration/openhands -v`  
Expected: PASS.

Run: `pytest -m 'not slow' -q`  
Expected: PASS.

Run: `docker compose -f docker-compose.yml -f docker-compose.openhands.yml up -d --wait`  
Expected: all required services healthy.

Run: `python scripts/openhands_probe.py acceptance --json`  
Expected: every gate reports `passed: true`.

- [ ] **Step 4: Document operations and commit**

Document startup, health, endpoint exposure, token key rotation, failure diagnosis, projector reconciliation, sandbox cleanup, backup, rollback, and Canvas access.

```bash
git add tests/integration/openhands/test_platform_acceptance.py tests/integration/openhands/test_platform_security.py docs/operations/openhands-stack.md specs/testing-devops.md
git commit -m "test: verify OpenHands platform cutover"
```

### Task 18: Coordinated switch and legacy deletion

**Files:**
- Delete: `src/agent_loop.py`
- Delete or reduce: legacy-only executor/schema/background/event modules identified by ledger
- Delete: compatibility-only tests identified by ledger
- Modify: `app.py`
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `specs/agent-tools.md`
- Modify: `specs/chat.md`
- Modify: `website/agent-migration.md`
- Modify: `README.md`

**Interfaces:**
- Produces: one production OpenHands platform; prior release remains rollback unit.

- [ ] **Step 1: Prove backup and rollback compatibility**

Create database backup using existing supported mechanism, restore into isolated stack, and run read-only smoke checks. Record release/image/database compatibility in operations guide.

- [ ] **Step 2: Disable legacy runtime with one controlled configuration change**

No request, schedule, webhook, assistant, or skill path may reference old loop. Do not retain automatic fallback.

- [ ] **Step 3: Run acceptance before deletion**

Run commands from Task 17. Expected: all PASS while old runtime is disabled.

- [ ] **Step 4: Delete ledger-confirmed legacy code and tests**

Remove old loop, provider streaming/tool loop, obsolete SSE/detached machinery, unused dependencies/configuration, and tests that assert only removed implementation. Preserve domain behavior tests.

- [ ] **Step 5: Verify no legacy symbols remain**

Run:

```bash
rg -n "stream_agent_loop|execute_tool_block" src routes services app.py
```

Expected: no production matches.

Run: `pytest -m 'not slow' -q`  
Expected: PASS.

Run: `python scripts/openhands_probe.py acceptance --json`  
Expected: all gates pass.

- [ ] **Step 6: Commit deletion**

```bash
git add -A
git commit -m "refactor: complete OpenHands agent platform cutover"
```

## Execution Control

Stop after Tasks 1–5 for architecture-gate review. Record selected branches for:

- interactive execution owner;
- direct rotation versus broker;
- per-profile ACP MCP transport;
- ApprovalGrant delivery.

Only then execute Tasks 6–18. Stop before Task 18 for destructive-cutover review using Task-17 evidence. Task 18 deletion is authorized by approved coordinated-cutover design, but target files must be resolved from clean disposition ledger and passing acceptance stack immediately before deletion.

## Final Definition of Done

- Every frozen requirement in design has implementation or test evidence.
- All four provisional mechanics have evidence-backed selected branches.
- Agent Server owns canonical conversation history; Automation owns applicable run history.
- Odysseus projections rebuild from canonical state and tolerate unreadable events.
- Native and ACP profiles operate through governed archetypes and MCP.
- Side effects require delegation, exact approval when applicable, and domain idempotency.
- All product reasoning/generation uses governed agent or model-job execution.
- macOS Docker/Tailscale acceptance passes.
- Legacy agent runtime and compatibility surface are deleted.
- Worktree contains focused commits and updated operations/architecture documentation.
