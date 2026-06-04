# Agent Tools

Last updated: dev@a3cb15d | 2026-06-06

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
- `src/tool_implementations.py`;
- `src/builtin_actions.py`;
- `src/ai_interaction.py`;
- `src/action_intents.py`;
- `src/goal_based_extractor.py`;
- `src/teacher_escalation.py`;
- `src/agent_tools.py`;
- `src/mcp_manager.py`;
- `src/builtin_mcp.py`;
- `src/bg_jobs.py` and `src/bg_monitor.py`;
- `routes/chat_routes.py`, `routes/chat_helpers.py`, `routes/model_routes.py`, `routes/skills_routes.py`, `routes/mcp_routes.py`, and `routes/workspace_routes.py`;
- `mcp_servers/*.py`;
- frontend stream/admin/settings files that display tool events, active plans, workspaces, and disabled tools;
- `tests/test_agent_loop.py`, `tests/test_tool_*`, and focused MCP/public-policy/schema tests.

## Agent Loop

`src.agent_loop` owns agent prompt assembly, request-local current date/time insertion, tool retrieval, prompted tool-block handling, native tool-call consumption after `llm_core` normalizes provider events, multi-round execution, tool result insertion, final metrics, and fallback responses. It requests context from documents, skills, tool retrieval, and messages; it should not own domain-specific business logic for every tool.

`src.llm_core` owns provider payloads, native tool-schema emission, and provider stream parsing. `agent_loop` consumes normalized tool-call events and decides whether and how to execute them.

Agent mode enters through chat routes, including auto-escalation from intent helpers, detached `agent_runs` streaming, resume/stop behavior, and frontend tool-event rendering.

Guide-only/no-tools turns are runtime policy, not prompt advice. `src.tool_policy` detects strong latest-turn directives such as guide-only mode, no-tools mode, and explicit requests not to use tools; it builds a `ToolPolicy` that hides schemas, disables known native tools, disables MCP for that turn, skips tool retrieval, suppresses local/workspace context injection, blocks document streaming/teacher escalation, and gives `tool_execution` a final execution backstop.

Plan mode is a read-only investigation path inside the same loop. It adds a denylist for known mutating tools, filters write/unknown MCP tools, prepends plan-mode instructions, and uses the `update_plan` tool only after a plan is approved for execution.

Workspace mode is request-scoped. Chat can send a workspace directory selected through `static/js/workspace.js`; `agent_loop` injects that fact early in the prompt and `tool_execution` confines bash, python, read/write/edit-file, and code-navigation tools to that root.

## Tool Registry

Tool registration is split:

- `src.agent_tools.TOOL_TAGS` owns executable fenced tags and the global MCP manager handle;
- `src.tool_parsing._TOOL_NAME_MAP` owns aliases and prompted-block parsing;
- `src.tool_schemas.FUNCTION_TOOL_SCHEMAS` and `function_call_to_tool_block()` own native schema and native-call conversion;
- `src.tool_index.BUILTIN_TOOL_DESCRIPTIONS` owns retrieval text;
- `src.tool_execution.execute_tool_block()` owns dispatch and hard execution gates;
- `routes.model_routes.py` and frontend settings/admin surfaces expose global disabled-tool controls.

When adding, removing, or renaming a tool, update the registry chain, execution dispatch, retrieval text, prompt wording, disabled-tool UI, and tests together.

`src.tool_index.ALWAYS_AVAILABLE` is the ambient backstop for high-frequency tools such as shell/python, web search/fetch, read/write/edit-file, code-nav, `manage_memory`, `ask_user`, `update_plan`, selected Cookbook serve controls, and `app_api`. Retrieval can add contextual tools, but these should not disappear from ordinary agent turns.

## Tool Retrieval And Execution

`src.tool_index.ToolIndex` owns candidate retrieval using embeddings/keywords and cached index data. Security filtering is not its hard boundary: `agent_loop` hides unavailable schemas, and `tool_execution` blocks disabled, admin-only, and public-restricted calls before dispatch.

`src.tool_execution` owns built-in tool execution, MCP dispatch, path confinement, background markers, output truncation, internal HTTP loopback, owner/admin checks, policy-blocked execution results, and formatting tool results for the model/UI. File tools support exact edit diffs, full-file writes, read line ranges, and workspace confinement. Code-navigation tools (`grep`, `glob`, `ls`) prefer `rg`/structured filesystem traversal over ad hoc shell commands.

Current call sites include:

- agent mode tool calls from `src.agent_loop`;
- MCP route configuration and built-in MCP registration;
- background job monitoring and auto-continue;
- skill tests, teacher escalation, scheduled tasks, and background follow-up loops;
- UI-control and AI interaction helpers.

## Streaming And Continuations

Agent streaming emits normal content plus tool progress/output, document stream/update, ask-user choices, plan updates, budget, metrics, teacher escalation, research anchor, and finish/error events. Frontend chat stream code and detached replay depend on stable event names.

Long-running bash jobs can be detached with background markers. `src.bg_jobs` owns persistent job state/result files; `src.bg_monitor` owns auto-continuation when jobs finish. Detached chat runs are in-memory and do not survive server restart, while background job state is disk-backed.

Loop-breaker final-answer rounds, optional verifier retries, and teacher escalation are recovery behavior owned by `agent_loop` and `src.teacher_escalation`.

## Security And Policy

- `src.tool_security` owns non-admin blocked-tool decisions.
- Non-admin users must not reach admin tools through agent mode, MCP, retrieval, or loopback calls.
- Path-based tools must remain confined to allowed roots and reject sensitive paths.
- Tool output is bounded/truncated where native execution owns the path. MCP output must be treated as untrusted; central MCP-output truncation before model re-entry remains a gap.
- Provider-emitted native tool calls are requests, not authorization. `tool_execution` and route-level policy remain the authority.
- Guide-only/no-tools mode blocks tools before prompt assembly, before execution, and in chat preprocessing paths that would otherwise fetch context or start tool-backed research.
- Plan mode is policy, not prompt advice: mutating native tools are disabled and write/unknown MCP tools are hidden and runtime-blocked for that turn.

## Internal Loopback

`src.tool_implementations.do_app_api()` owns generic app API loopback, OpenAPI discovery, method/path blocklists, and fixed local target behavior. `_internal_headers()` adds the process-secret internal-tool token and optional `X-Odysseus-Owner`; `core.middleware.require_admin()` and auth middleware own the corresponding bypass and owner-stamping rules. Route-specific owner handling must still be audited.

## MCP

`src.mcp_manager` owns configured MCP server lifecycle, discovered tool state, qualified MCP names, OpenAI schema conversion, call routing, generation invalidation, and connect/disconnect status. It supports stdio, SSE, and Streamable HTTP transports; Streamable HTTP can publish a `needs_auth` state and uses `src.mcp_oauth` for OAuth/OIDC-style authorization, token refresh, and encrypted token storage. `src.builtin_mcp` owns built-in server registration and the native-vs-MCP split. `mcp_servers/` owns server-specific tools for email, image generation, memory, RAG, and optional browser tooling.

Native bash, python, file, web search, and web fetch tools continue through native fallback even when MCP is unavailable. Browser MCP is optional and can be skipped when cached Playwright/NPX packages are missing. Public users get no MCP schemas, and any `mcp__*` execution attempt must be blocked.

MCP prompt/schema rendering includes server-provided input schemas, but names, types, and parameter hint text are sanitized and length-capped before entering the prompt. Per-server disabled tools filter listings, prompt descriptions, and function schemas; execution-time disabled-tool enforcement remains a separate hardening item.

## Intent And Recovery Helpers

`src.action_intents` owns deterministic chat-to-agent promotion hints and returns a category/reason so route logs can explain auto-escalation decisions. It must avoid promoting explanatory questions into agent mode. `src.builtin_actions` owns scheduler/background actions outside the normal live agent loop. `src.teacher_escalation` owns recovery/escalation and skill-creation flows. `src.goal_based_extractor` is research-adjacent and should stay cross-referenced from research behavior rather than treated as ordinary tool execution.

## Degraded Behavior

- ToolIndex can degrade to keyword selection when embeddings, Chroma, or index warmup fail.
- Agent mode can degrade from native function schemas to prompted fenced-block parsing based on provider/tool-support heuristics. Local Ollama `/v1` defaults to fenced tools unless the endpoint explicitly advertises `supports_tools`.
- MCP startup failure is non-critical; route/status surfaces expose per-server errors.
- `ODYSSEUS_DISABLE_MCP`, missing `mcp`, uncached browser MCP packages, and per-server disabled tools can remove tools without blocking the app.
- Global `builtin_browser` disable behavior may not currently match qualified `mcp__builtin_browser__*` tool names.

## Current Gaps

- Tool descriptions are duplicated across `FUNCTION_TOOL_SCHEMAS`, agent prompt sections, and `BUILTIN_TOOL_DESCRIPTIONS`.
- Agent prompts remain heavy for small local context windows.
- Some AI-control helpers are still globally wired from app startup rather than a narrower service layer.
- Tool registry consistency is manual across tags, aliases, schemas, retrieval descriptions, execution dispatch, settings/model routes, and frontend toggles.
- MCP disabled-tool changes can stale-cache tool retrieval because disabled maps are not always an index generation input.
- External MCP output truncation and tool-result prompt-injection wrapping need stronger guarantees.
- Agent tests mostly cover helpers and targeted regressions, not an end-to-end fake-LLM `stream_agent_loop` path with retrieval, native schemas, prompted blocks, disabled/admin hiding, MCP tools, plan/workspace state, user-time context, and tool-result SSE.
