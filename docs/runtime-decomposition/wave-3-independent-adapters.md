# Wave 3 independent adapters

Continuation base: `8ae6ee43936bdc5fe1da1297f87fb7b56be4a6cc`, directly
above canonical `a80c164dbe3e8bde4fb29b45c5d1c61404f2fede`.
The read-only continuation audit reviewed that checkpoint, its callers and tests,
then used the following design for this slice. The original A–I inventory remains
in `wave-3-resource-identity.md`; this supplement specifies the independent
adapters and the adversarial corrections. Process/browser adapters are deferred.

## A. Re-audit and implicit-resource inventory

| Site | Observation and decision |
| --- | --- |
| `resources.intersect_roots`, `RequestAuthority.intersect`, `bind_request_authority`, `seal_task_authority` | Descendant intersection already checks the parent observation. The equal-root shortcut did not revalidate it. Validate both observations before any intersection result; a fresh descendant never renews a replaced parent. |
| Dispatcher empty-root exact-approval fallback | Proposal roots serve only to re-resolve and compare one captured operation. Never install them into request authority. Test restored versions 1/2, sibling/parent access, replay, aliases and request/owner/session changes. |
| Native read/write/edit/patch, navigation and media workspace paths | Canonical control-path denial omitted hardlinked control objects. Also deny observed device/inode aliases, private configuration/DB/index paths and background control files, including configured paths from loaded producers. Directory grep's ripgrep branch scans descendants without bound checks: use the existing per-file resolver before reading. Filter bound ls/glob results through the same resolver. Media source/destination resolution uses the same control-state denial. This does not introduce a media filesystem adapter. |
| `McpManager.connect_server`, successful connection registration, `call_tool` | Server ID and qualified tool are mutable connection selectors. Seal the actual connection, configured endpoint origin and opaque epoch; revalidate at transport. A bound call cannot reconnect/retry into another producer. No transport redesign. |
| `_MCP_TOOL_MAP`, qualified/bare email dispatch | Availability previously selected backend/fallback. Preserve native filesystem semantics; snapshot other configured backends at trusted admission and pin dispatch. Discovery never creates operation grants. |
| Scoped `AgentExecutionBridge`, TUI bridge, HTTP request bridge | Callback objects or validated endpoint configuration determine execution. Capture object/configuration identity and exact tool, not a local filesystem observation. HTTP bridge factory and admission must produce the same configuration identity. |
| `do_api_call`, registered integrations | Names/IDs resolve through mutable configuration. Resolve aliases uniquely, bind integration ID, origin and configuration epoch; use the ID during execution and compare the loaded configuration before HTTP work. Generic API grants do not authorize the configured integration inventory: explicit trusted backend scope or one exact approval is required. Paths may contain tokens, so serialize origins and opaque epochs, not URL paths. |
| Document handlers / active document | Context/global active ID or most-recent lookup occurred during execution. Resolve server context or owner-scoped latest once; pass exact ID/version/digest and normalized selector. Global active changes cannot select another record. |
| Attachment OCR / upload index | URI resolves through mutable owner/path/hash index. Capture owner-checked row identity and confined file observation; consume the captured path. Keep the upload producer's owner check, without administrator override. |
| Thread management / send / history searches | `current`, line/JSON ID aliases and history target must bind caller owner and invocation thread. Capture exact selected thread row; collection searches bind the owner namespace. Existing owner-filtered search/cache boundaries remain. |
| Notes / native memories | Prefix and title selection can choose the first row later. Resolve uniquely within owner scope and normalize full ID; exact lookup in bound execution. Capture DB revision or opaque private memory revision. |
| Vault configuration / CLI | Global config had no owner producer binding. Legacy unowned config refuses runtime access. Authenticated settings save establishes owner and drops legacy session material; subsequent runtime reads require that owner, endpoint/configuration observation and an item observed by the server search producer. Names/prefixes resolve uniquely in that owner/configuration catalog to an exact UUID. Unknown UUIDs cannot manufacture a record observation. No credential appears in identity. |
| Builtin memory / RAG MCP stores | Memory producer has a fixed configured owner. Bind that owner and reject another caller or an ownerless producer. Legacy builtin RAG has no owner contract and cannot acquire private scope from discovery; refuse its runtime identity. |
| Generic `app_api` loopback | Internal-token calls could bypass migrated record domains. Refuse those namespace paths, including encoded/relative path aliases; callers use dedicated resource-bound operations. This is a migration guard, not an expanded internal API capability. |

Other owner domains (calendar/contact/research/task/dynamic-tool stores), opaque
native script semantics and unrelated internal API paths remain separate adapter
work. Their existing permission gates are not described as typed enforcement.
This slice does not make a whole-runtime containment or private-data claim.

## B. Typed model

`resources.py` owns the additive immutable contracts:

* `NativeBackendResource`: fixed native namespace and exact tool. Availability
  cannot replace it with an MCP filesystem.
* `ExternalResource`: backend namespace, configured server ID, credential-free
  endpoint origin, exact tool ID, connection/configuration epoch and optional
  producer owner. Always `external=true`, `contained=false`.
* `OwnedScope`: namespace, owner, invocation thread and either an explicit record
  ID set or a server-granted owner collection. The collection is a typed scope,
  not a wildcard model selector or a capability floor.
* `OwnedResource`: namespace/collection, owner, invocation thread, exact record
  ID, observed revision and storage-thread linkage where applicable.

Attachment bindings additionally carry the existing typed filesystem observation
under the owner's private upload root. Context adapters are in
`remote_resources.py` and `owned_resources.py`; they grant no operation names.

## C. Normalized operation/resource binding

`ExactOperation` retains the original normalized proposal. Backend bindings
capture that exact input, caller and request alongside the backend identity.
Owned bindings carry original operation plus server-normalized execution input,
record observations and document execution context. Approval serialization seals
normalized input digests without copying credential-bearing arguments into the
identity. Existing approval content/digest and one-use claim remain mandatory.

Collection creation/search/list operations bind owner collection identity;
specific reads/mutations bind exact records. A restricted record set cannot admit
a collection operation. Native filesystem bindings keep all existing source and
destination rules; patch moves remain unsupported and fail before execution.

## D. Validation flow

1. Server semantic admission grants operations independently of the tool inventory.
2. Trusted authority construction snapshots backend resources for those grants
   and admits relevant owner/thread scopes. Restored snapshots never run this
   constructor's implicit sealing path.
3. Request binding, parent intersection, policy and TurnContract gates run first.
4. Resolve backend and record selectors centrally, or consume the proposal's
   exact sealed identities. Compare ownership, request/thread and resource scope.
5. Revalidate observations before consuming the existing one-use approval and
   again at dispatch/producer entry. Bind contexts with `finally` reset.
6. Execute normalized input on the pinned backend/record. MCP and integration
   producers compare their actual connection/configuration at the call boundary.

Filesystem checks remain pathname observations, not descriptor-relative atomic
execution. Inode reuse, concurrent path replacement after validation and DB
changes between observation and mutation remain limitations. Record revisions
identify selected state; they are not new Wave 4 evidence or effect claims.

## E. Alias, rename and ownership rules

Backend aliases must resolve uniquely to the approved server/configuration. A
changed endpoint, connection or alias fails before claim/effect. Document
active/latest and thread current selectors resolve once on the server; an
approval consumes the captured ID even when the current UI alias changes. Missing,
stale, conflicting or ambiguous records fail closed. Notes/memory prefixes cannot
fall through to another title/record during bound execution.

Child scopes intersect exact backend identities and owned record sets. Session
continuations may rebind the invocation namespace under the existing trusted
continuation rules, retaining owner, record limits and backend observations;
they do not synthesize a record from copied history. Exact approvals may admit
only their captured operation for a non-inherited legacy authority; they never
install a general resource scope or widen a parent's record/backend scope.
Inherited proposals themselves must fit their originating operation, backend,
filesystem and record scopes. A later approval resumption that resets the existing
inherited marker cannot reconstruct an identity excluded at proposal time.
Private read identity grants no additional send/egress operation.

## F. Integration points

Authority construction/persistence/intersection; central dispatch; approval
proposal/digest; HTTP request bridge admission; MCP successful connection/call
boundary; integration alias/configuration lookup; document dispatch context;
attachment OCR; notes/native memory exact lookup; authenticated vault settings
and owner-bound vault search producers. `agent_loop` changes only forward existing runtime context to proposal
capture. No loop decomposition, containment redesign or lifecycle change.

## G. Migration

Authority snapshots become version 3. Versions 1/2 restore empty backend/owned
scope fields. Fixed local dispatch compatibility retains existing operation gates;
no legacy snapshot reconstructs an external backend or owned collection. Exact
proposal snapshots can admit one operation without renewing general authority.

Remote connection identities expire on reconnect/restart; private configuration
epochs use an in-process keyed opaque identifier. Restored stale epochs refuse
execution and require fresh trusted admission. Legacy unowned vault/RAG and
unresolved MCP connections fail closed. No remote owner, resource containment or
semantic page claim is inferred from successful transport.
Vault record observations describe the last server search response. Configuration
changes or refreshed record observations invalidate sealed operations; this is
not fresh remote semantic verification or a CLI process/account lifecycle claim.

## H. Required verification

New regressions cover equal/subtree stale parent intersection through direct,
context and task callers; restored empty-root exact approvals; control-state
direct/relative/symlink/hardlink reads/writes/search; backend availability, exact
tool/selectors, reconnect/endpoint/alias changes, legacy restoration, child
intersection, credentials and external flags; owned record aliases, revisions,
owner/thread changes, narrow scopes, attachments, vault/native memory identities,
generic loopback bypasses and context cleanup on success/error/cancel/nesting.
Focused existing suites cover RequestAuthority, TurnContract transcription/OCR/
tasks, approvals, nested invocation, filesystem confinement, MCP/bridge routing,
documents/uploads/history and owner-scoped stores. Validation results are recorded
below; no full repository suite is run.

Final validation on the checkpoint tree: **2,435 passed, 2 skipped, 4 warnings**
across the 88 focused files below (56.60 seconds). The skips are the existing
`/tmp`-symlink platform case and a containment shortfall case when `RLIMIT_AS`
can be lowered. The full repository suite was not run.

Tests used `/tmp/odysseus-wave3-validation/bin/python`, an isolated venv with
system site packages plus `bcrypt`, `pyotp`, `mcp<2` and `pypdfium2`. The command
was that interpreter followed by `-m pytest -q -rs --disable-warnings
--maxfail=10` and the exact file arguments below. Earlier overlapping targeted
runs are not added to the final count.

Static gates passed with empty output:

```sh
python3 -m compileall -q app.py core routes services src tests scripts
git diff --check
git grep -n -E '^(<<<<<<< |=======$|>>>>>>> )' || true
git ls-files -u
```

<details>
<summary>Exact focused test file arguments</summary>

```text
tests/test_resource_identity.py
tests/test_owned_resource_identity.py
tests/test_remote_resource_identity.py
tests/test_request_authority.py
tests/test_tool_approvals.py
tests/test_tool_approval_single_action_scope.py
tests/test_tool_approval_task_scope.py
tests/test_workspace_confine.py
tests/test_tool_path_confinement.py
tests/test_path_confinement_boundary.py
tests/test_filesystem_tool_argument_validation.py
tests/test_code_nav_tools.py
tests/test_apply_patch_transaction.py
tests/test_execution_bridge.py
tests/test_production_external_bridge.py
tests/test_turn_contract.py
tests/test_turn_contract_read_operations.py
tests/test_turn_contract_integration.py
tests/test_agent_turn_contract_boundaries.py
tests/test_explicit_personal_turn_contract.py
tests/test_nested_invocation_ownership.py
tests/test_containment_contract.py
tests/test_containment_enforcement.py
tests/test_containment_process_tree.py
tests/test_native_execution_containment.py
tests/test_background_containment.py
tests/test_process_ownership.py
tests/test_bg_jobs_store.py
tests/test_bg_job_tools.py
tests/test_execution_filesystem_boundary.py
tests/test_mcp_manager.py
tests/test_mcp_reconnect_args.py
tests/test_mcp_text_error_normalization.py
tests/test_mcp_param_hint_hardening.py
tests/test_mcp_tool_params_in_prompt.py
tests/test_mcp_memory_owner_scope.py
tests/test_mcp_cache_invalidation.py
tests/test_multiple_mcp_servers_timeout.py
tests/test_mcp_dependency_compatibility.py
tests/test_builtin_mcp_bg_tasks.py
tests/test_builtin_mcp_pythonpath.py
tests/test_builtin_mcp_npx_cache.py
tests/test_mcp_add_server_args_validation.py
tests/test_manage_mcp_command_allowlist.py
tests/test_document_tool_owner_scope.py
tests/test_owned_document_query.py
tests/test_document_session_owner_scope.py
tests/test_active_document_mutation_guard.py
tests/test_native_document_stream.py
tests/test_document_followup_integrity.py
tests/test_document_active_restore.py
tests/test_attachment_refs.py
tests/test_upload_handler_atomicity.py
tests/test_upload_handler_cleanup.py
tests/test_upload_handler_rename_owner.py
tests/test_upload_routes_owner_scope.py
tests/test_resolve_upload_path_nondict.py
tests/test_personal_upload_isolation.py
tests/test_personal_upload_privilege.py
tests/test_extract_text_tool.py
tests/test_media_ingress.py
tests/test_session_tools_registry.py
tests/test_session_owner_attribution.py
tests/test_session_list_owner_scope.py
tests/test_session_endpoint_owner_scope.py
tests/test_session_search.py
tests/test_session_search_batch_fetch.py
tests/test_history_topics_owner_scope.py
tests/test_history_order_by_timestamp_regression.py
tests/test_history_db_fallback_hidden.py
tests/test_memory_owner_isolation.py
tests/test_memory_routes_session_owner.py
tests/test_manage_memory_json_contract.py
tests/test_manage_memory_list.py
tests/test_memory_store_unreadable_no_wipe.py
tests/test_manage_notes_search_contract.py
tests/test_notes_fail_closed_auth.py
tests/test_notes_checklist_state.py
tests/test_vault_password_not_in_argv.py
tests/test_vault_routes_shim.py
tests/test_external_context_tool_gate.py
tests/test_chat_route_tool_policy.py
tests/test_product_turn_contract_route.py
tests/test_native_tool_result_threading.py
tests/test_host_shell_polling.py
tests/test_integrations_url_join.py
tests/test_integration_api_call_ssrf.py
tests/test_integrations_api_call_truncation.py
```

</details>

## I. Wave 4 / Wave 5B collision boundaries

Wave 4 retains durable claim, effects, evidence freshness, provenance and egress
policy. No private content is licensed for transfer by a resource identity.
Existing containment/browser receipts are not authority or semantic verification.

Wave 5B must freeze the shared `ProcessIdentity` and lifecycle API before these
seams are implemented:

* Native `_run_owned_command` and process ownership checks: consume the producer's
  verified process identity and lifecycle namespace/incarnation, linking the
  admitted execution backend/root and containment receipt without granting scope.
* `bg_jobs.launch/get/kill`, monitor continuations and authority sidecars: link
  the durable owner/thread/job identity to that same verified lifecycle identity
  and receipt. A model job ID or restored PID never reconstructs it.
* Browser lifecycle `session_for`/receipt and private/MCP browser producers:
  consume the frozen producer/process lifecycle identity, then bind owner/thread,
  browser session incarnation and page/navigation observations separately.
  Producer liveness is not verification of remote page meaning.

This continuation implements none of those adapters and creates no parallel
`ProcessIdentity`. Existing inert process/browser types are unchanged.
