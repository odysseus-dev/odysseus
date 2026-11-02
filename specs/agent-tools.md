# Agent Tools

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers agent/tool behavior in:

- `src/agent_loop.py`, `src/clean_agent_preview.py`, `src/turn_contract.py`, `src/agent_evidence.py`, and `src/agent_trace.py`;
- `src/agent_runtime/` authority, resource binding, action journal, durable effects, completion, and context resolution modules;
- `src/containment.py`, `src/process_ownership.py`, `src/process_lifecycle.py`, and `src/browser_identity.py`;
- `src/llm_core.py`;
- `src/tool_schemas.py`;
- `src/tool_execution.py`;
- `src/tool_policy.py`;
- `src/tool_index.py`;
- `src/tool_parsing.py`;
- `src/tool_security.py`;
- `src/tool_capabilities.py`;
- `src/tool_approval_scopes.py`;
- `src/tool_approvals.py`;
- `src/attachment_refs.py` and shared upload lifecycle helpers in `src/upload_handler.py` / `src/tool_utils.py`;
- `src/tool_implementations.py`;
- `src/tools/*.py`;
- `src/builtin_actions.py`;
- `src/ai_interaction.py`;
- `src/action_intents.py`;
- `src/goal_based_extractor.py`;
- `src/teacher_escalation.py`;
- `src/agent_tools/` modules and compatibility facade;
- `src/mcp_manager.py`;
- `src/builtin_mcp.py`;
- `src/bg_jobs.py` and `src/bg_monitor.py`;
- `routes/chat_routes.py`, `routes/chat_helpers.py`, `routes/model_routes.py`, `routes/skills_routes.py`, canonical `routes/mcp/mcp_routes.py` plus its shim, and `routes/workspace_routes.py`;
- `mcp_servers/*.py`;
- frontend stream/admin/settings files that display tool events, workspaces, and disabled tools;
- `tests/test_agent_loop.py`, `tests/test_tool_*`, and focused MCP/public-policy/schema tests.

## Agent Loop

`src.agent_loop` owns agent prompt assembly, request-local current date/time insertion, tool retrieval, prompted tool-block handling, native tool-call consumption after `llm_core` normalizes provider events, multi-round execution, tool result insertion, final metrics, and fallback responses. It requests context from documents, skills, tool retrieval, and messages; it should not own domain-specific business logic for every tool. Its prompt rules now bias structured/long-form writing toward living documents, route active compose/email drafts back into existing email documents, and prefer first-class `web_search`/`web_fetch` tools over shell/Python/curl for current web lookups when web tools are enabled.

`src.llm_core` owns provider payloads, native tool-schema emission, and provider stream parsing. `agent_loop` consumes normalized tool-call events and decides whether and how to execute them.

The legacy loop and the opt-in compact preview share the execution boundary. Agent mode enters through chat routes, including auto-escalation from intent helpers, detached `agent_runs` streaming, resume/stop behavior, and frontend tool-event rendering.

Guide-only/no-tools turns are runtime policy, not prompt advice. `src.tool_policy` detects strong latest-turn directives such as guide-only mode, no-tools mode, and explicit requests not to use tools; it builds a `ToolPolicy` that hides schemas, disables known native tools, disables MCP for that turn, skips tool retrieval, suppresses local/workspace context injection, blocks document streaming/teacher escalation, and gives `tool_execution` a final execution backstop.

Plan mode is a read-only investigation path inside the same loop. It adds a denylist for known mutating tools, filters write/unknown MCP tools, prepends plan-mode instructions, and uses the `update_plan` tool only after a plan is approved for execution. The backend path still exists for compatibility, but current browser chat forces incoming `plan_mode` off and the old plan-window UI module is gone.

Workspace mode is request-scoped. Admin chat can send a workspace directory selected through `static/js/workspace.js`; `agent_loop` injects that fact early in the prompt and `tool_execution` confines bash, python, read/write/edit-file, and code-navigation tools to that root. `routes.workspace_routes` owns admin-only browse/vet APIs, skips hidden/symlink directory traversal, caps listings, and rejects sensitive/root paths before a workspace reaches chat.

## Tool Registry

Tool registration is split:

- `src.agent_tools` is now a package/facade. `TOOL_HANDLERS` maps native tool names to handler functions across filesystem, subprocess, web, document, interaction, model-interaction, background-job, session, and admin modules, while `TOOL_TAGS` keeps compatibility metadata and the global MCP manager handle;
- `src.tools` owns domain do_* implementations for calendar, contacts, Cookbook, image, notes, research, search, system, and vault tools. `src.tool_implementations` is now a compatibility facade that re-exports those symbols and lazy-loads admin manage_* symbols to avoid circular imports;
- `src.agent_tools.admin_tools` owns admin manage_* tools for endpoints, MCP, webhooks, tokens, and settings, including command validation for `manage_mcp`;
- `src.tool_parsing._TOOL_NAME_MAP` owns aliases and prompted-block parsing;
- `src.tool_schemas.FUNCTION_TOOL_SCHEMAS` and `function_call_to_tool_block()` own native schema and native-call conversion;
- `src.tool_index.BUILTIN_TOOL_DESCRIPTIONS` owns retrieval text;
- `src.tool_execution.execute_tool_block()` owns dispatch and hard execution gates;
- `routes.model_routes.py` and frontend settings/admin surfaces expose global disabled-tool controls.

When adding, removing, or renaming a tool, update the registry chain, execution dispatch, retrieval text, prompt wording, disabled-tool UI, and tests together.

`src.tool_index.ALWAYS_AVAILABLE` is the retrieval catalog for high-frequency tools such as shell/python, web search/fetch, read/write/edit-file, code-nav, `manage_memory`, `ask_user`, `update_plan`, selected Cookbook serve controls, and `app_api`. Compact prompt/schema assembly selects policy-authorized intent-, skill-, and retrieval-relevant tools; the full-schema surface offers all policy-authorized built-ins and MCP schemas. Selection is not authority.

## Tool Retrieval And Execution

`src.tool_index.ToolIndex` owns candidate retrieval using embeddings/keywords and cached index data. Security filtering is not its hard boundary: `agent_loop` hides unavailable schemas, and `tool_execution` blocks disabled, admin-only, and public-restricted calls before dispatch.

`src.tool_execution` owns built-in tool execution, MCP dispatch, path confinement, background markers, output truncation, internal HTTP loopback, owner/admin checks, policy-blocked execution results, and formatting tool results for the model/UI. File tools support exact edit diffs, full-file writes, read line ranges, and workspace confinement. Code-navigation tools (`grep`, `glob`, `ls`) prefer `rg`/structured filesystem traversal over ad hoc shell commands. Uploaded-file context uses stable `attachment_ref` manifests and owner-checked URIs; a compatibility local path is exposed only after upload-root and tool-root confinement. Shared truncation, upload-handler registration, and MCP manager compatibility helpers live in `src.tool_utils`.

Tool retrieval has domain-specific hooks beyond generic similarity: contact queries can surface `resolve_contact`/`manage_contact`; matched skills can add `manage_skills` and their required toolsets to the relevant tool set; explicit admin intents can include admin schemas so prompt text and native schema emission match.

Interaction/session/model helper tools are native first-class tools, not prompt-only conventions. `ask_user` and `update_plan` live in `src.agent_tools.interaction_tools`, model delegation/listing helpers live in `model_interaction_tools`, session creation/list/send/manage helpers live in `session_tools`, and `manage_bg_jobs` lives in `bg_job_tools`.

Prompted-tool parsing includes recovery paths for local/provider text leaks: bare JSON after a web-tool mention, OpenAI-style raw `{"function": ...}` payloads, StepFun/Gemma/DSML markup, Hermes/Qwen JSON bodies nested inside `tool_call` wrappers, and `<function_model><function_call>...</function_call><parameters>...</parameters></function_model>` wrappers from local MLX/Exo models. The Qwen bare end marker requires its pipe delimiter so ordinary text cannot terminate a tool block. Non-dict JSON arguments are rejected back to empty args instead of crashing the turn and common `tex` typos normalize to `text`. Shared finite delimiter scanning lives in `src/text_scanning.py`; supported wrapper recovery and bounded scans are not a general linear-time guarantee for all parsing. Executed raw tool JSON is stripped from assistant text afterward; this is still not a general-purpose JSON-command parser.

Current call sites include:

- agent mode tool calls from `src.agent_loop`;
- MCP route configuration and built-in MCP registration;
- background job monitoring and auto-continue;
- skill tests, teacher escalation, scheduled tasks, and background follow-up loops;
- UI-control and AI interaction helpers.

## Streaming And Continuations

Agent streaming emits normal content plus tool progress/output, document stream/update, ask-user choices, plan updates, budget, round exhaustion, loop-breaker, intent-nudge exhaustion, metrics, teacher escalation, research anchor, and finish/error events. Frontend chat stream code and detached replay depend on stable event names. If the stream generator closes while awaiting an in-flight tool, the loop cancels and awaits that tool task so subprocess-backed work is not left orphaned.

Long-running bash jobs can be detached with background markers. `src.bg_jobs` owns persistent job state/result files; `src.bg_monitor` owns auto-continuation when jobs finish. Detached chat runs are in-memory and do not survive server restart, while background job state is disk-backed.

Loop-breaker final-answer rounds, explicit repeated-tool/intent-nudge guard events, round-cap continuation signals, optional verifier retries, and teacher escalation are recovery behavior owned by `agent_loop` and `src.teacher_escalation`.

Approval replay injects the sealed first tool result before the resumed model round. If that replay round has neither assistant prose nor reasoning, `_append_tool_results()` omits the empty assistant spacer so Anthropic-compatible payloads do not contain a rejected non-final empty assistant message; reasoning-only carriers remain a documented compatibility edge.

## Security And Policy

- `src.tool_security` owns non-admin blocked-tool decisions.
- Non-admin users must not reach admin tools through agent mode, MCP, retrieval, or loopback calls.
- Agent owner is passed from chat route `get_current_user(request)`. In `AUTH_ENABLED=false` mode this is `None`, not the `""` value returned by route dependencies. `blocked_tools_for_owner()`, schema hiding, and `execute_tool_block()` all use that owner.
- Current dev tool security treats explicit `AUTH_ENABLED=false` as single-user even when an auth store exists, while auth-enabled pre-setup callers remain non-admin.
- Path-based tools must remain confined to allowed roots and reject sensitive paths. Sensitive-path checks are case-insensitive and apply to direct file tools and code-navigation tools; `grep`/`glob`/`ls` must not become existence or content oracles for `.env`, SSH/GPG material, `id_rsa`, and similar denylisted paths.
- Tool output is bounded/truncated where native execution owns the path, including displayed agent-tool output through the shared truncation helper. MCP output must be treated as untrusted; central MCP-output truncation before model re-entry remains a gap.
- Provider-emitted native tool calls are requests, not authorization. `tool_execution` and route-level policy remain the authority.
- `src.tool_capabilities` classifies each tool's effects and result integrity. Once external/workspace-untrusted content becomes model-visible, the request/session security context permits only explicitly low-impact tools without interruption and requires exact approval for high-impact, unknown, and arbitrary MCP calls.
- `src.tool_approvals` seals an opaque, expiring exact first action plus server-only selected tools and continuation query to owner, session, origin run, tool content, workspace, capability snapshot, and—when relevant—document id/version/content digest. Chat choices grant the resumed task or the same chat session; both consume the exact first action, task scope bypasses the gate only during that resumed run, and chat scope is reconstructed only from a resolved card bound to the exact session id. The browser never receives selected tools/query and submits only task/chat/deny. Non-chat callers retain single-action behavior; new normal turns and superseding actions retire unresolved approvals without clearing taint.
- Tool results that expose remote or stored untrusted content arm the gate even when their tool status is failed. Content-free failures and server-generated policy/approval placeholders do not. Native/provider tool messages and fenced results carry model-visible untrusted metadata/wrapping instead of relying on prompt wording alone.
- Attachment-bearing document, note, and calendar tools owner-reserve internal upload references before durable writes and fail without mutation when the referenced upload is unavailable.
- Guide-only/no-tools mode blocks tools before prompt assembly, before execution, and in chat preprocessing paths that would otherwise fetch context or start tool-backed research.
- Plan mode is policy, not prompt advice: mutating native tools are disabled through schema-derived detection plus a static backstop, and write/unknown MCP tools are hidden and runtime-blocked for that turn.

## Internal Loopback

`do_app_api()` is implemented in `src.tools.system` and re-exported by `src.tool_implementations`. It owns generic app API loopback, OpenAPI discovery, method/path blocklists, and fixed local target behavior. `_internal_headers()` adds the process-secret internal-tool token and optional `X-Odysseus-Owner`; `core.middleware.require_admin()` and auth middleware own the corresponding bypass and owner-stamping rules. Route-specific owner handling must still be audited.

## MCP

`src.mcp_manager` owns configured MCP server lifecycle, discovered tool state, qualified MCP names, OpenAI schema conversion, call routing, generation invalidation, and connect/disconnect status. It supports stdio, SSE, and Streamable HTTP transports; Streamable HTTP can publish a `needs_auth` state and uses `src.mcp_oauth` for OAuth/OIDC-style authorization, token refresh, and encrypted token storage. Arbitrary MCP tools classify fail-high for approvals. `src.builtin_mcp` owns built-in server registration and the native-vs-MCP split. `mcp_servers/` owns server-specific tools for email, image generation, memory, RAG, and optional browser tooling.

Native bash, python, file, web search, and web fetch tools continue through native fallback even when MCP is unavailable. Browser MCP is optional and can be skipped when cached Playwright/NPX packages are missing. Public users get no MCP schemas, and any `mcp__*` execution attempt must be blocked.

MCP prompt/schema rendering includes server-provided input schemas, but names, types, and parameter hint text are sanitized and length-capped before entering the prompt. Per-server disabled tools filter listings, prompt descriptions, and function schemas; execution-time disabled-tool enforcement remains a separate hardening item.

## Intent And Recovery Helpers

`src.action_intents` owns deterministic chat-to-agent promotion hints and returns a category/reason so route logs can explain auto-escalation decisions. Explicit web-search language is category `web`; it can promote the turn into agent mode and narrow tools toward web search/fetch, but route policy requires explicit web-search enablement and honors explicit denial. It must avoid promoting explanatory questions into agent mode. `src.builtin_actions` owns scheduler/background actions outside the normal live agent loop. `src.teacher_escalation` owns recovery/escalation and skill-creation flows. `src.goal_based_extractor` is research-adjacent and should stay cross-referenced from research behavior rather than treated as ordinary tool execution.

When an email reader is active, browser chat passes active email metadata and the agent loop injects it as protected, untrusted context so default reply/draft behavior targets the selected message. Active email compose documents are handled as existing email drafts rather than generic new-document requests.

## Degraded Behavior

- ToolIndex can degrade to keyword selection when embeddings, Chroma, index warmup, or vector retrieval timeouts fail.
- Agent mode can degrade from native function schemas to prompted fenced-block parsing based on provider/tool-support heuristics. Legacy local Ollama `/v1` and native `/api` endpoints normally use text tools unless explicitly supported. Compact native surfaces force native schemas unless a text-recovery path is selected; configured compact/full modes and the external request contract participate in surface selection, so the legacy default is not universal.
- MCP startup failure is non-critical; route/status surfaces expose per-server errors.
- `ODYSSEUS_DISABLE_MCP`, missing `mcp`, uncached browser MCP packages, and per-server disabled tools can remove tools without blocking the app.
- Native browser page execution is unavailable at the common boundary; browser registration and schemas do not override that denial.

## Request Authority, Resource Identity, And Completion

`src.turn_contract` owns immutable request-scoped tool selection and required-read operations. Selection and retrieval never grant permission. `src.agent_runtime.authority` owns server admission independently of offered schemas; `execute_tool_block()` requires an explicit security context and a bound `RequestAuthority`, intersects child authority with its parent, normalizes exact operations, and checks policy before resource resolution or dispatch. Delegated credentials have a separate early denial backstop.

`src.agent_runtime.resources`, `resource_binding`, `owned_resources`, `remote_resources`, and `process_resources` bind operations to server-owned filesystem, owned-record, backend/configuration, process/job, and browser identities. Exact approvals seal these bindings and revalidate them before use; a changed backend, document, path identity, process incarnation, or browser state can refuse replay. Control-plane stores are excluded from native file operations.

`src.agent_runtime.journal` records proposed, normalized, authorized, dispatched, and settled actions. `effects`, `effect_adapters`, and `effect_log` distinguish intent, executor outcome, observed state, and verified effects; a successful receipt alone does not establish resulting state. `completion.with_completion_gate` decorates `stream_agent_loop`, holds answer deltas until the generator unwinds, and checks completion against the server journal/evidence ledger while tool/progress/interaction events remain live. Cancellation cannot emit successful completion. `src.agent_trace` adapts native SSE, typed traces, and imported trajectories into evidence/completion views.

`src.agent_runtime.runtime_selection` is the shared compact-preview eligibility rule. `src.clean_agent_preview` runs the opt-in clean v3 loop when its contract has `clean_compact_v3_preview` selection mode; regular/full-schema and compatibility surfaces retain the legacy loop. Preview schema validation, bounded retry/search/tool budgets, and evidence-based finish checks supplement the common executor rather than replacing its authority.

Current native tools also include `apply_patch`, `get_workspace`, `host_shell`, `private_browser`, `get_weather`, `youtube_tool`, `inspect_media`, `extract_text`, `transcribe_media`, and `search_chats`. Filesystem, subprocess, web, weather, and media handlers own these implementations; `src.media_ingress` owns bounded local attachment ingestion, and OCR/transcription can depend on optional backends.

## Compact Selection And Evidence Recovery

`src.turn_contract` shares broad-web briefing classification with preview. Named private stores, active editor work, concrete local media and explicit operations take precedence over incidental public lookup words. Standalone social turns and full-match supplied-text proofreading/translation (including correction-only colon payloads) offer no tools; compound save/search requests remain separate. A subjectless lookup can request clarification only without conversation referents or supplied workspace/media/editor context. These lexical guards neither resolve arbitrary language nor grant authority. A core tool floor applies only without recognized families, exact selected tools or a sealed read, and is still intersected with inventory/policy; browser is not a core tool.

Explicit discovery-and-page retrieval can select both web tools; workspace artifact requirements can retain bounded file/Python tools alongside web selection. Memory-use prohibitions suppress sealed memory prerequisites. Concrete media routes prefer OCR for exact text, transcription for captions, otherwise inspection; explicit downstream artifacts or personal actions remain distinct. Tool-family selection and multilingual heuristics do not guarantee execution order or semantic understanding.

Preview rejects blank search queries rather than copying conversational text. It preserves requested temporal/news constraints and adds a PDF constraint only when asked. Discovery is bounded separately from source retrieval: no-evidence/repeated cosmetic searches suppress further search while discovered URLs can remain inspectable. Broad briefing recovery permits one breadth expansion after one successful search and fewer than two attempts, within the remaining round budget; an empty second search cannot restart that expansion. One shallow-answer repair uses an 80-word-or-missing-source-URL heuristic, not substantive grounding proof. Breadth work precedes presentation-only citation/content repair.

Retrieval qualification uses bounded body/access/missing-page/repetition heuristics, including embedded article text and a 100-word/12-shingle duplicate detector. A successful retrieval prompts assessment of missing, stale or contradictory evidence rather than forcing completion. Browser fallback proposals cannot override the common page-execution denial. Failed transports can lead to alternative offered search/source retrieval, not an authorized automatic browser session. A rejected search may synthesize only from earlier usable retrieval. Terminal budget fallback preserves up to five deduplicated source strings with explicit preliminary/insufficient-evidence wording, or reports no usable evidence. Synthesized citations are not automatically filled from arbitrary discovered URLs; deterministic link-only answers require a positive whole-request grammar and enough extracted links. Unknown official-source identity is not inferred from PDF extensions or product words.

Native tool choice normalization can make a uniquely offered named search required, narrow schemas before omitting an unsupported provider choice, or use named/auto compatibility for specific DeepSeek/Kimi/Qwen/Ajax families. It is serialization compatibility, not provider acceptance. Wire messages strip server-only provenance and empty assistant placeholders while preserving legitimate tool-call carriers. Sibling tool receipts remain contiguous before bounded untrusted visual blocks; still-image repeat identity includes path, casefolded query and size. Visual defaults retain one current record/three frames with a bounded 1–8 frame control and retire prior tool pixels separately from user uploads.

Artifact completion is target-specific: off-target/scratch writes and generic successful shell receipts cannot satisfy a required artifact or force early synthesis. Empty runner declarations fall back to prompt-inferred artifacts. Directory requests require meaningful files; source-code writes can unwrap leading/trailing Markdown fences for allowlisted suffixes without certifying syntax. Successful workspace paths can display as `/workspace`, without implying universal path redaction. A reserved final synthesis and tightly bounded malformed-call recovery do not create extra execution authority. `turn_contract` and `agent_step` expose required artifacts/calls-used/pending/write-phase fields; these are progress telemetry, not completion proof. Execution timing metrics record attempted canonical tools even on failure.

## Admission And Bounded Execution Details

Chat-scope bearer admission resolves the credential owner and intersects delegated tool grants with server policy; client runtime metadata, retrieval results and prompted tool names cannot widen it. Interactive approval/session options are refused for delegated credentials. Persistent chat grants are server-signed; a normal new turn retires stale pending approvals, while exact first-action replay is one-use. For a non-inherited ungranted launch lacking a backend launch scope, `ToolApprovalStore.create()` may seal exactly one request resource root (or its sealed workspace fallback); inherited children cannot gain this scope. Local reads through delegated credentials, including local `web_fetch`, file-scheme/workspace reads and absolute-path PDF extraction, have an early denial backstop even when the automatic action gate is disabled or bypassed.

`ODYSSEUS_TOOL_APPROVAL_GATE` defaults on; only normalized `0`, `false`, `no` and `off` disable it. The gate does not replace authority/resource checks. Request authority binds owner, request/thread, workspace and admitted operations; child scopes intersect the parent. Owned record revisions, backend/configuration epochs, filesystem identities and process/job generations are revalidated. Captured server producer observations supply evidence; arbitrary tool output or registry membership is not a trusted producer. Expected admission/resource failures become typed denials; unexpected programming errors are not silently converted into authorization success.

Native file tools use the canonical agent workspace and explicit allowed roots, deny control-plane paths and all hardlinked regular files, and validate path/resource identities. `ls`, `glob` and `grep` share one control-plane snapshot per scan; root and bound-resource checks still observe fresh state. Grep normalizes relative-or-basename glob filters before bounded ripgrep batches, validates regex even with no targets, closes stdin and retains a bounded Python fallback with identity revalidation. Empty file writes require explicit clear intent for nonempty existing files; missing/null content is refused, already-empty files can be no-ops, and missing files use exclusive/staged creation without overwriting a competing creator. These checks do not make filesystem operations universally atomic or race-free.

Bounded required-safe reads use an exact sealed operation from the admitted family; a presentation `max_items` limit is not an owner/database filter. Only one eligible active family is warmed and a failed read does not authorize probing alternate families. Bounded model context keeps actual visible records rather than hidden private overflow. Native call identifiers survive only when arguments match exactly; structured turn replacement preserves tool/result pairing. Supplied documents, email, Notes/Todos, skills and workspace instructions remain untrusted reference data.

Compact preview keeps connections alive without replaying executed tools. It permits at most two qualifying context-size retries per turn with reconstructed paired messages and one pre-response transport retry; yielded stream content is not retried. Pre-execution schema hints are freshly generated from offered schemas/arguments (bounded required/type/enum/additional-property/path guidance). Caught execution exceptions instead use fixed public messages and error categories; raw exception details can remain in server logs. Terminal provider errors use fixed public failure events and do not emit a successful done event.

Repeated successful exact calls are suppressed while changed arguments or pagination can proceed; repeated failed exact calls and ignored corrective nudges have separate bounded stop rules. Artifact workflows reserve bounded calls and require actual usable artifacts, not merely directory existence or a tool receipt. Compact interactive rounds clamp to 1–200 with an eight-round fallback, and nonpositive interactive tool budgets use a large sentinel. Validated native workspaces instead clamp to 1–64 rounds with an eight-round fallback and a fixed 32-call budget. Legacy adaptive handling and UI limits are separate. Budgets are checked between rounds and do not interrupt an already-running call. Optional verifier retries and teacher escalation are separate recovery controls; teacher work starts after the parent stream contexts close.

Durable effects distinguish intended mutations, execution outcome and observed state. Claims are persisted before execution; failure to establish a durable claim prevents execution. The newest eligible authorized observation for a resource wins; partial/undecidable evidence cannot borrow an older success, unknown impacts overlap conservatively, and later mutations/new incarnations stale earlier reads. Whole-file postconditions are all-or-none when derivation is unsupported; partial reads are not whole-file digests. Failed reads establish absence only after confined repeated ENOENT checks. Verifier cancellation remains unsettled; changed undeclared targets contradict completion. Only owned running processes survive as running effects; background settlement does not by itself verify output. External execution may finish with disclosed unverified effects, rather than claiming local containment or verified remote state. Scheduled built-ins and arbitrary remote systems are not all covered by this journal.

## Artifact Recovery And Completion Claims

Artifact delivery contracts narrow offered writer schemas on copied schema objects and bind declared targets without treating directory descendants or filename patterns as filesystem confinement. Binary suffixes refuse text-writer delivery, including relevant bridge paths, and prefer an offered capable writer. Empty, malformed, off-contract, or non-materialized output uses bounded shared recovery attempts; pending sibling calls and late results do not justify claiming an artifact exists. Python literal-output analysis is a planning aid, not execution evidence.

Compact tool-call parsing accepts bounded concatenated complete JSON objects with ordered call identities and normal admission checks. Limited trailing tool markup after a complete supported call is inert; arbitrary prose is not an executable call. Artifact research reserves bounded call/time budget for delivery rather than guaranteeing interruption of an already-running tool.

Completion assessment in `src.agent_runtime` distinguishes a usable artifact, a satisfied obligation, and verified execution. File evidence uses exact normalized identity and bounded regular-file reads, rejecting symlink/nonregular aliases rather than trusting basename matches. Partial reads do not prove whole-file verification, late observations do not retroactively verify earlier claims, and executable verifier results take precedence over optimistic prose.

Final presentation can remove unsupported execution, test-count, coverage, or all-fixed assertions while preserving quoted/explanatory text. It does not invent new obligations from fabricated assistant claims. Provider errors, approval waits, and budget exhaustion remain distinct terminal states; a polished answer is not an execution receipt. Child journals remain separate, teacher review follows closed child execution, and incomplete children do not become successful distillation evidence. Explicit no-web filtering cannot be undone by an unrelated memory/toolset early return.

## Parser And Diagnostic Ownership

`src.text_scanning` supplies forward-only shared scans for the existing staged tool grammars, preserving delimiter priority, mandatory terminal-dot captures and legacy newline behavior. The parser does not become a universal linear-time or arbitrary-language recognizer. Binding catches expected input/resource errors as curated denials; programming defects such as AttributeError are allowed to propagate rather than being relabeled as policy refusal.

Filesystem postconditions derive exact requested content from the producer transforms, including edit newline behavior and universal-newline patch prestate, with bounded identity-checked UTF-8 reads. Unsupported derivation clears the complete obligation set instead of verifying a convenient subset. Only a process-bound launch or detachment can attest RUNNING; unrelated returned lifecycle keys, arbitrary containment dictionaries and external acknowledgements cannot manufacture native cleanup or job observations.

## Trusted MCP Attribution

`src/tool_execution.py` strips caller-provided `_odysseus_*` fields from qualified/legacy MCP JSON and parsed tool arguments. Dispatch then injects trusted owner/session context for email and trusted owner for image MCP tools; missing trusted context does not preserve a forged claim. `_call_mcp_tool()` forwards owner through native/unavailable/disconnected direct fallback paths. Image results from qualified dispatch receive promoted image fields. The image MCP server persists the trimmed owner and registers fresh files; this transport attribution does not replace route authorization.

## Current Gaps

- Tool descriptions are duplicated across `FUNCTION_TOOL_SCHEMAS`, agent prompt sections, and `BUILTIN_TOOL_DESCRIPTIONS`.
- Agent prompts remain heavy for small local context windows.
- Some AI-control helpers are still globally wired from app startup rather than a narrower service layer.
- Tool registry consistency is manual across handler maps, tags, aliases, schemas, retrieval descriptions, execution dispatch, settings/model routes, and frontend toggles.
- Registry and retrieval consistency still requires coordinated updates across native and MCP surfaces.
- External MCP output still needs a single central size cap before model re-entry; untrusted-result metadata and the post-external-context action gate now cover the prompt-injection/authorization boundary.
- Auth-disabled/no-login owner propagation is inconsistent between route dependencies and chat/agent execution, so tool-security and native tool storage behavior need dedicated regression coverage.
- Authority, resources, effects, containment, preview, and turn contracts have focused regression suites; live provider and platform behavior still requires separate validation.
