# Wave 3: server-owned resource identity

Audit base: `a80c164dbe3e8bde4fb29b45c5d1c61404f2fede` on
`feature/runtime-resource-authority`. The read-only audit and this design precede
production edits. Wave 3-S is frozen. This document distinguishes the contract
from the initial enforcement slice; it does not claim all resource adapters are
migrated.

## A. Current implicit-resource inventory

| Boundary / locator | Existing authority | Resource still interpreted later |
| --- | --- | --- |
| `src/agent_runtime/authority.py`: `ExactOperation`, `OperationGrant`, `RequestAuthority` | Immutable request, owner/session/workspace, action/input limits, policy denials | Workspace is a string; no root incarnation, object, destination or backend binding. |
| `src/turn_contract.py`: `TurnContract`, `canonical_tool` | Inventory narrows operations; email aliases share policy identity | Inventory/selection does not resolve resources. Bare/qualified email names can address one server. Transcription/OCR/tasks remain narrow. |
| `src/tool_execution.py`: `_tool_path_roots`, `_resolve_tool_path`, `_resolve_search_root` | Operation admission and deployment/public/admin policy | Data, system temp and configured extra roots are an access allowlist; relative paths may use process cwd; empty search path uses mutable defaults. An allowlist is not a request resource grant. |
| Same: `_resolve_tool_path_in_workspace`, `vet_workspace`, `_display_tool_path` | Trusted workspace string, sensitive-path deny policy | `/workspace`, relative/host paths and symlinks resolve later; root/object replacement is not represented. Display/evidence aliases do not confer access. |
| `src/path_confinement.py`: `canonical_root`, `confine` | Canonical inside-root check | Non-strict realpath intentionally supports missing destinations; it does not identify an existing object or grant a root. |
| `src/agent_tools/filesystem_tools.py`: read/write/edit, `ApplyPatchTool`, ls/glob/grep | Dispatcher gate and shared resolver | Handlers reparse paths; writes create parent directories; patches resolve each target and stage/backup by pathname. Different selectors may identify the same target. Patch moves are explicitly unsupported. Search binds a directory but derives descendants later. |
| `src/agent_runtime/identity.py`: `artifact_identity`, `artifact_version` | Evidence bookkeeping only | Workspace/absolute string identities and content hashes are completion evidence, not execution identities or authority. |
| `src/agent_tools/subprocess_tools.py`: `_owned_spec`, `_run_owned_command`, Bash/Python/host shell | Request operation grant then Wave 3-S containment | Cwd, environment, mount recipe and workspace aliases are interpreted at execution. Opaque scripts cannot be treated as an enumerated file operation. Host-shell endpoint/jobs belong to an external executor. |
| `src/containment.py`: `ContainmentSpec`, `ContainmentGrant`, `agent_spec`, `declare_external_bridge` | Frozen enforcement requirements | Receipt ID, owner label, PID/namespace PID and endpoint attest boundaries. They do not supply user permission or a request resource grant. |
| `src/process_ownership.py`: `capture`, `verify`, `start_token` | PID plus OS start token, Linux boot identity | A numeric PID alone is a reused slot. Tokens are inspection identities, not permissions. No new teardown/lifecycle algorithm belongs in Wave 3. |
| `src/bg_jobs.py`: `launch`, `get`, `kill`; `src/agent_tools/bg_job_tools.py` | Session check; verified process teardown | Job ID resolves through a mutable store. Supervisor PID/token, containment ID and namespace identity are separate. Session ownership is implicit rather than typed. |
| `src/agent_runtime/authority.py`: task/job snapshots; `src/bg_monitor.py`; `src/task_scheduler.py` | Parent intersection, sealed task input, continuation owner/session checks | Persisted workspace string can resolve to a replacement root. Missing snapshots fail closed. Session rebinding must not create resources. |
| `src/agent_tools/web_tools.py`: `_scoped_browser_session`, private-browser execution; `src/browser_lifecycle.py`: `BrowserSession`, `session_for`, `receipt` | Browser action class; server session hashing; producer locks | Namespace/session hash identifies a producer name, not its incarnation. Navigation generation, current URL, failed navigation and element references are mutable page state. URL/element selectors are not page identity. Receipts are not semantic verification. |
| `src/builtin_mcp.py`, `src/mcp_manager.py`: `call_tool`, reconnect, builtin browser | Qualified tool and policy gates | Server ID maps to a mutable connection/configuration; reconnect replaces producer. Builtin Playwright has a shared global browser. Stdio locally launches a third-party server but does not prove containment of its operations. |
| `src/tool_execution.py`: `AgentExecutionBridge`, `_client_bridge`, `_route_tool_via_bridge`, `_apply_patch_via_tui_host_bridge`, `_call_mcp_tool` | Explicit bridge routing after authority; exact approvals | Bridge callback/name, endpoint and context are resolved later; MCP-to-native fallback changes backend. Transport selection and availability must not authorize a backend/resource. External paths need the remote owner's contract, not local realpath or invented remote containment. |
| `src/agent_tools/document_tools.py`: `_get_owned_document`, `_most_recent_owned_document`, update/edit/suggest/manage | Owner-filtered DB lookup; approved ID/version/digest | Context target, process-global active document, model ID aliases and most-recent selection can choose targets late. Ownership alone does not establish that the request selected a document. |
| `src/agent_tools/media_tools.py`: `_resolve_workspace_path`, media/OCR/transcription implementations | Narrow operation class and local/upload checks | Workspace URI, local paths, confined host aliases, attachment URI and export/output aliases are separate resolution paths. Exports require source plus destinations; attachment IDs require owner-checked index identity. |
| `src/upload_handler.py`: `reserve_upload`, `resolve_upload`; `src/document_processor.py` | Ownership/index consistency and path confinement | Upload ID/hash/index aliases map to files; row/path/owner binding must be captured before consumption. Owner migration and cleanup can mutate mappings. |
| `src/agent_tools/session_tools.py`, `src/session_actions.py`, `src/session_search.py`, `src/tools/search.py` | Owner-filtered thread/history lookup | `current`, IDs, list/search result sets, fork targets and DB rows are reconstructed during execution. Null-owner handling differs by API and must remain explicit. A child thread never inherits authority by copying history. |
| `src/agent_tools/coding_tools.py`: `TodoWriteTool` | Tool/session context | Session text is sanitized into a filename and can fall back to model input/`current`; different strings may collide. This is private storage, not an ordinary workspace file. |
| `src/tools/notes.py`, `calendar.py`, `contacts.py`, `vault.py`, `research.py`, `image.py`, `system.py`, `cookbook.py`; admin tools and `app_api` | Owner/admin filters, operation gates, scheduled-task snapshots | Record ID/title/query/default account, task/action, model/server ID, preset, endpoint and API path select resources later. User collections and service credentials are private namespaces; installed tools/endpoints do not grant access. Broad app API and opaque host/script calls require dedicated backend contracts. |
| `src/tool_approvals.py`: pending digest, `matches`, `claim`; nested invocation tests | Exact one-use input, owner/session/workspace/document and original authority | File path is exact text but its alias/object can change between proposal and claim. Children may only intersect operation and resource scopes. No approval grants a later operation implicitly. |

The inventory is of execution/resource-resolution seams. Internal renderer and
temporary implementation files are not independent user authority targets. Their
identity derives from the admitted operation's bounded root/backend contract.

## B. Typed resource identity model

Identity is inert, immutable server data. Model arguments remain selectors.
There is no model-facing deserializer that mints grants.

* Filesystem: a root with scope (`workspace`, `scratch`, `external`, `private`),
  canonical location and observed device/inode/type. An object has that root,
  canonical path, target observation (or explicit absence) and existing ancestor
  observations. Missing destinations retain their existing parent identity;
  they are not imaginary inodes. Private roots additionally bind an owner.
  Server execution-control stores and background authority sidecars cannot be
  addressed as user filesystem resources, even beneath an admitted root.
* Process: backend/ownership namespace, producer incarnation, PID/start token,
  optional namespace PID/start token, background job ID and containment receipt
  linkage. A receipt reference is attribution only. New process execution first
  binds its execution root/backend; PID identity only exists after spawn.
* Browser producer: backend namespace, owner/thread, producer session and
  incarnation. Page observation: that producer plus navigation generation,
  observed page ID/URL and producer reference. Lifecycle state is distinct from
  page semantics, and neither establishes semantic correctness.
* External execution: backend namespace, endpoint identity, server/tool and
  connection incarnation. Always explicitly external. Endpoint identities must
  be sanitized identifiers, never credentials. No containment is inferred.
* Owned records: ownership namespace, exact owner, thread, collection and
  record/document ID; revision when the producer supplies it. Collections used
  for list/search are explicit owner-bound resources, not unknown record IDs.

The initial implementation provides types for each domain. Only filesystem
resolution/admission is migrated; unused domain types do not attest existing
producers or silently supply missing incarnations.

## C. Normalized operation/resource binding

Retain the original `ExactOperation` for policy and approval matching. Add an
immutable bound operation containing request identity, canonical executor input
and role-tagged resources (`source`, `target`, `destination`, `search_root`).
Patch operations enumerate all targets before dispatch and reject canonical
path and observed object collisions (including hardlinks). Rename/move bindings require both source and destination; the
current native patch parser continues refusing moves. No shell text parsing is
used to pretend an opaque script has enumerated filesystem semantics.

## D. Authority-to-resource validation flow

1. Normalize the original tool/input; check RequestAuthority binding, parent
   intersection, policy denials and exact operation grant/approval eligibility.
2. Apply the unchanged TurnContract and existing security/public/admin gates.
3. Resolve native filesystem selectors against roots sealed by the server,
   apply existing confinement and sensitive-path policy, and observe identities.
   Neither configured allowlists nor schema/bridge availability adds a root.
4. Compare approved resource snapshots before claiming the exact one-use action.
   Revalidate root/object/ancestors; unresolved or changed identities refuse.
5. Dispatch canonical executor input under a context-local binding. Shared
   resolvers consume that binding and reject undeclared paths; search traversal
   remains bounded by the declared search resource and sensitive-path policy.
6. Existing effect/evidence/completion handling continues unchanged.

Path observations and immediate revalidation detect replacement before
dispatch. They are not kernel-held file descriptors and cannot eliminate all
concurrent pathname races inside existing handlers. Closing those races requires
descriptor-relative I/O integration; this slice must not claim atomic identity
enforcement or change the frozen process containment mechanism.
Device/inode observations also cannot distinguish every possible inode reuse;
they are scoped local filesystem observations rather than globally permanent IDs.

## E. Alias, rename and ownership rules

`/workspace`, relative paths, host paths and symlinks resolve only on the server.
Executor input uses the resolved path; original input remains exact for approval.
Retargeting an approved alias changes its bound identity and refuses execution.
Both sides of any future move must resolve under admitted scopes before an
effect. A missing destination binds absence plus its existing ancestors.
Owner/thread mismatches fail; an ownership query proves attribution, not intent.
Children intersect roots by identical root observation and owner/scope, and may
narrow to descendant scopes. Empty intersections stay empty. Continuations and
persisted snapshots retain observations instead of re-sealing a changed root.

## F. Integration points / chosen slice

Add `src/agent_runtime/resources.py`, extend RequestAuthority with sealed
filesystem roots, and add the central native filesystem binder in
`src/agent_runtime/resource_binding.py`. Integrate read/write/edit/patch/ls/glob/
grep with `execute_tool_block`, shared path resolvers and exact approval sealing.
Bridge-routed operations remain outside this native adapter; a local root must
not be used to invent a remote resource identity. Existing native search handlers
retain their descendant checks. No agent-loop decomposition or browser/process
lifecycle refactor is needed.

Bare native filesystem operations now dispatch directly to their native handlers
with canonical input. A connected filesystem MCP server cannot redirect these
resources or supply an implicit fallback backend. Explicit qualified MCP calls
remain on the external path pending its producer/resource adapter.

## G. Migration plan

1. Initial slice: seal a vetted workspace at server authority construction;
   permit explicit server-supplied scratch/external/private roots; serialize the
   observations and intersect them. No implicit data/tmp/extra-root grant.
2. Version authority snapshots. Legacy snapshots retain operation restrictions
   but receive no reconstructed filesystem roots. Missing roots refuse migrated
   native tools. A new trusted request may seal new resources.
3. Integrate canonical native filesystem input and approved resource snapshots.
   Existing fixtures requiring unscoped native files must explicitly grant a
   test root; they cannot rely on broad production allowlists.
4. Follow-up adapters: media/attachment/export, document/thread/private stores,
   job controls and native opaque execution root/recipe, then bridge/MCP and
   browser producers. Each requires its own server-owned resolution seam and
   must fail closed on absent producer identity. Do not fill gaps with string
   hashes described as incarnations or generic capability floors.

The narrow slice does not remove every implicit-resource site listed in A.
Its coverage and remaining adapters must be reported explicitly.
The server-control-store denial applies to this native filesystem adapter;
opaque scripts and other unmigrated adapters still need their own resource
boundaries. This slice does not attest those paths as enforcing the new contract.

## H. Exact tests required

* Root/target canonicalization: relative, host, `/workspace`, symlink aliases;
  sibling/traversal/symlink escapes; sensitive files; malformed path/JSON/type.
* Existing files and directories; absent destination plus parent identity;
  replacement of root, target or existing ancestor invalidates the binding.
* No roots means no migrated native execution, even with an offered handler,
  configured allowlist, selected tool, valid operation grant or result receipt.
* Every patch target binds before dispatch; canonical target collisions and
  unsupported moves refuse before partial writes. Dual-resource move contract.
* Canonical input reaches the handler; shared resolvers reject undeclared
  targets; directory searches allow only bounded descendants.
* Parent/child root intersection, mismatch of owners/sessions, context cleanup,
  concurrent calls, task/background persistence, malformed/legacy snapshots.
* Approval alias/target/parent replacement, immutable digest, missing resource
  snapshot, exact original input, one-use replay and nested restriction.
* Regression suites: request authority, approvals, nested ownership, workspace
  confinement, path policy, filesystem tools, execution bridges, TurnContract
  (including transcription/OCR/tasks), frozen containment/native/background.
* Future adapters require job PID reuse/receipt mismatches, browser incarnation/
  page generation distinction, MCP reconnect/endpoint changes, cross-owner
  attachment/record/thread rejection and exact dual-resource exports/moves.

## I. Collision analysis with Wave 4 and Wave 5B

Wave 3 binds what an admitted operation addresses. Device/inode observations
identify objects, not content versions or proof that an effect occurred. It adds
no durable claim, effects ledger, egress/provenance, evidence freshness rule or
truthful-completion mechanism (Wave 4). It adds no supervisor, restart/reaper,
cleanup state machine, generic lifecycle namespace allocator or process teardown
algorithm (Wave 5B). Process/browser producer incarnations must come from their
owners; this contract does not fabricate them. Frozen containment receipts and
browser lifecycle receipts remain evidence of their stated producer boundaries,
never authority or semantic verification.

## Implementation validation

Executed locally with `/usr/bin/python3` on 2026-10-02:

* Integrated focused run: **1,649 passed, 2 skipped, 1 warning**. This includes
  request identity linkage and approval matching, before the final hardlink
  collision and resource-context unwind additions.
* Final follow-up after those additions: **109 passed, 1 warning** across
  `test_resource_identity.py`, `test_apply_patch_transaction.py`,
  `test_workspace_confine.py` and `test_tool_approvals.py`.
* `compileall -q` on the five changed/new production Python modules and the two
  changed/new test modules passed. `git diff --check` passed.

Counts overlap and must not be added. No full Python suite was executed. The
earlier focused runs exposed error-message expectation changes; the three
unscoped dispatcher denial assertions now check missing sealed roots. The
separate legacy resolver/sensitive-path tests remain intact. The new tests use
the raw dispatcher with explicit server authority, not a permissive fixture.

Integrated command:

```sh
/usr/bin/python3 -m pytest \
  tests/test_resource_identity.py tests/test_request_authority.py \
  tests/test_tool_approvals.py tests/test_tool_approval_single_action_scope.py \
  tests/test_tool_approval_task_scope.py tests/test_workspace_confine.py \
  tests/test_tool_path_confinement.py tests/test_path_confinement_boundary.py \
  tests/test_filesystem_tool_argument_validation.py tests/test_code_nav_tools.py \
  tests/test_apply_patch_transaction.py tests/test_execution_bridge.py \
  tests/test_production_external_bridge.py tests/test_turn_contract.py \
  tests/test_turn_contract_read_operations.py tests/test_turn_contract_integration.py \
  tests/test_agent_turn_contract_boundaries.py tests/test_explicit_personal_turn_contract.py \
  tests/test_nested_invocation_ownership.py tests/test_containment_contract.py \
  tests/test_containment_enforcement.py tests/test_containment_process_tree.py \
  tests/test_native_execution_containment.py tests/test_background_containment.py \
  tests/test_process_ownership.py tests/test_bg_jobs_store.py \
  tests/test_bg_job_tools.py tests/test_execution_filesystem_boundary.py \
  -q --disable-warnings --maxfail=8
```

Final follow-up command:

```sh
/usr/bin/python3 -m pytest tests/test_resource_identity.py \
  tests/test_apply_patch_transaction.py tests/test_workspace_confine.py \
  tests/test_tool_approvals.py -q --disable-warnings
```

Frozen containment, browser lifecycle producers, process ownership and
`agent_loop` were not edited. The resource types for the remaining domains are
inert contracts; their presence does not mean those execution adapters enforce
Wave 3 yet. Pathname races and inode reuse remain the limitations stated in D.
