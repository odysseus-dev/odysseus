# Agent Tools

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers agent/tool behavior in:

- `src/agent_loop.py`;
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
- `src/attachment_refs.py` and shared upload lifecycle helpers in
  `src/upload_handler.py` / `src/tool_utils.py`;
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

Agent mode enters through chat routes, including auto-escalation from intent helpers, detached `agent_runs` streaming, resume/stop behavior, and frontend tool-event rendering.

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

`src.tool_index.ALWAYS_AVAILABLE` is the retrieval catalog for high-frequency tools such as shell/python, web search/fetch, read/write/edit-file, code-nav, `manage_memory`, `ask_user`, `update_plan`, selected Cookbook serve controls, and `app_api`. Current prompt/schema assembly preserves only selected base tools unconditionally, then adds intent-, skill-, and retrieval-relevant tools so unrelated schemas do not flood small contexts.

## Tool Retrieval And Execution

`src.tool_index.ToolIndex` owns candidate retrieval using embeddings/keywords and cached index data. Security filtering is not its hard boundary: `agent_loop` hides unavailable schemas, and `tool_execution` blocks disabled, admin-only, and public-restricted calls before dispatch.

`src.tool_execution` owns built-in tool execution, MCP dispatch, path confinement, background markers, output truncation, internal HTTP loopback, owner/admin checks, policy-blocked execution results, and formatting tool results for the model/UI. File tools support exact edit diffs, full-file writes, read line ranges, and workspace confinement. Code-navigation tools (`grep`, `glob`, `ls`) prefer `rg`/structured filesystem traversal over ad hoc shell commands. Uploaded-file context uses stable `attachment_ref` manifests and owner-checked URIs; a compatibility local path is exposed only after upload-root and tool-root confinement. Shared truncation, upload-handler registration, and MCP manager compatibility helpers live in `src.tool_utils`.

Tool retrieval has domain-specific hooks beyond generic similarity: contact queries can surface `resolve_contact`/`manage_contact`; matched skills can add `manage_skills` and their required toolsets to the relevant tool set; explicit admin intents can include admin schemas so prompt text and native schema emission match.

Interaction/session/model helper tools are native first-class tools, not prompt-only conventions. `ask_user` and `update_plan` live in `src.agent_tools.interaction_tools`, model delegation/listing helpers live in `model_interaction_tools`, session creation/list/send/manage helpers live in `session_tools`, and `manage_bg_jobs` lives in `bg_job_tools`.

Prompted-tool parsing includes recovery paths for local/provider text leaks: bare JSON after a web-tool mention, OpenAI-style raw `{"function": ...}` payloads, StepFun/Gemma/DSML markup, Hermes/Qwen JSON bodies nested inside `tool_call` wrappers, and `<function_model><function_call>...</function_call><parameters>...</parameters></function_model>` wrappers from local MLX/Exo models. The Qwen bare end marker requires its pipe delimiter so ordinary text cannot terminate a tool block. Non-dict JSON arguments are rejected back to empty args instead of crashing the turn, common `tex` typos normalize to `text`, and delimiter scans are forward-only so unterminated tool markup cannot drive quadratic rescans. Executed raw tool JSON is stripped from assistant text afterward; this is still not a general-purpose JSON-command parser.

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
- Attachment-bearing document, note, and calendar tools owner-reserve internal
  upload references before durable writes and fail without mutation when the
  referenced upload is unavailable.
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

- ToolIndex can degrade to keyword selection when embeddings, Chroma, index
  warmup, or vector retrieval timeouts fail.
- Agent mode can degrade from native function schemas to prompted fenced-block parsing based on provider/tool-support heuristics. Local Ollama `/v1` and native `/api` endpoints default to text tools unless the endpoint explicitly advertises `supports_tools`; `gpt-oss` remains text-tool by default unless the endpoint opts in.
- MCP startup failure is non-critical; route/status surfaces expose per-server errors.
- `ODYSSEUS_DISABLE_MCP`, missing `mcp`, uncached browser MCP packages, and per-server disabled tools can remove tools without blocking the app.
- Global `builtin_browser` disable behavior may not currently match qualified `mcp__builtin_browser__*` tool names.

## Current Gaps

- Tool descriptions are duplicated across `FUNCTION_TOOL_SCHEMAS`, agent prompt sections, and `BUILTIN_TOOL_DESCRIPTIONS`.
- Agent prompts remain heavy for small local context windows.
- Some AI-control helpers are still globally wired from app startup rather than a narrower service layer.
- Tool registry consistency is manual across handler maps, tags, aliases, schemas, retrieval descriptions, execution dispatch, settings/model routes, and frontend toggles.
- MCP disabled-tool changes can stale-cache tool retrieval because disabled maps are not always an index generation input.
- External MCP output still needs a single central size cap before model re-entry; untrusted-result metadata and the post-external-context action gate now cover the prompt-injection/authorization boundary.
- Auth-disabled/no-login owner propagation is inconsistent between route dependencies and chat/agent execution, so tool-security and native tool storage behavior need dedicated regression coverage.
- Agent tests mostly cover helpers and targeted regressions, including round-cap
  and disconnect cancellation paths, but not an end-to-end fake-LLM
  `stream_agent_loop` path with retrieval, native schemas, prompted blocks,
  disabled/admin hiding, MCP tools, plan/workspace state, user-time context, and
  tool-result SSE.
