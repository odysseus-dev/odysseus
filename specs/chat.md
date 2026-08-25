# Chat

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers current chat behavior in:

- `routes/chat_routes.py` and `routes/chat_helpers.py`;
- `routes/session_routes.py` and canonical `routes/history/history_routes.py`,
  with `routes/history_routes.py` as a compatibility shim;
- `src/chat_helpers.py`;
- `src/agent_runs.py`;
- `src/chat_handler.py` and `src/chat_processor.py`;
- `core/session_manager.py` and `core/models.py`;
- `src/attachment_refs.py` and `src/upload_handler.py` for durable attachment
  references and write reservations;
- `src/context_budget.py`, `src/context_compactor.py`, and `src/topic_analyzer.py`;
- `src/foreground_model_routing.py`, `src/tool_approval_scopes.py`, `src/tool_approvals.py`, and `src/tool_capabilities.py`;
- `routes/workspace_routes.py` for workspace selection support;
- frontend modules `static/js/chat.js`, `static/js/chatStream.js`, `static/js/chatRenderer.js`, `static/js/sessions.js`, `static/js/search-chat.js`, `static/js/compare/stream.js`, `static/js/workspace.js`, `static/js/composerArrowUpRecall.js`, `static/js/streamingSegmenter.js`, `static/js/group.js`, and `static/js/notes.js`;
- integration points with uploads, documents, compare, research, agent tools, memory, RAG, search, and model endpoints.

## Session Ownership

`core.session_manager.SessionManager` owns session persistence and message writes. `routes/session_routes.py` owns session list/create/update/archive/delete/folder/importance behavior for the sidebar. `routes.history.history_routes` owns history/topic surfaces, with `routes/history_routes.py` kept as a compatibility shim.

`core.models.Session` and `ChatMessage` are pure data containers. They do not own persistence; `Session.add_message()` delegates to the configured session manager when present.

Startup session discovery selects non-archived sessions by the existence of persisted `ChatMessage` rows rather than trusting the denormalized `Session.message_count`. It computes authoritative counts only for the bounded discovery set, then keeps full message hydration lazy.

## Streaming

`routes/chat_routes.py` owns `/api/chat`, `/api/chat_stream`, detached stream resume/stop/status, injected context, chat-message search, and rewrite routes. Streaming is the main UI path.

`static/js/chat.js` owns send/abort/continue UI state, the main fetch/read loop, SSE parsing, rendering dispatch, workspace form wiring, and background/resumable stream tracking. `static/js/chatStream.js` owns UI-control event handling and stream/research notification helpers. `static/js/sessions.js` polls server stream status after refresh or session switch. `static/js/composerArrowUpRecall.js` owns prompt recall from the composer when the caret is at the top of an empty input.

Runtime behavior:

- the `/api/chat*` prefix is exempt from the global request hard timeout;
- browser chat sends `X-Tz-Offset` and an IANA timezone name; request-local helpers prefer a valid IANA zone for DST-aware current-time reasoning, then fall back to the fixed offset;
- browser chat can send a selected workspace path; route code only resolves it for admin/single-user flows, validates it as an existing directory, and forwards it so agent file/shell tools are confined by `src.tool_execution`;
- stream callbacks can outlive a deleted session, so persistence must fail closed instead of recreating orphan messages;
- message metadata carries timestamps, metrics, tool events, sources, hidden
  thinking/reasoning text when providers expose it separately, context-trim
  metrics, structured attachment references, and related UI state;
- metadata preserves requested and actual reply models and endpoints, per-round route transitions, and answering-route cost attribution; stable session ids remain available so prompt/sequence-memory and KV-cache paths can address the same conversation consistently;
- multimodal content can be a list of content blocks for the live provider call,
  while persistence collapses raw media into readable text and stable
  attachment-reference lines;
- agent streams forward explicit round-cap, tool-budget, repeated-tool-loop,
  and intent-without-action guard events so the frontend can distinguish a
  controlled stop from a stalled response.

`src.agent_runs` owns detached in-memory stream runs, replay buffers, replacement cancellation, resume subscribers, explicit stop, and terminal-buffer eviction. Closing the SSE connection does not necessarily stop generation. `static/js/chat.js` can live-resume a still-running detached stream through `/api/chat/resume/{session_id}`; rich responses reload from DB for canonical rendering. Detached runs are process-local and do not survive server restart.

Provider adapters live below chat in `src.llm_core`. Chat consumes normalized SSE output, fallback/error events, reasoning/tool deltas, and metrics. Foreground chat is strict to the selected route by default. Only the selected owner can opt in through `foreground_fallback_enabled` plus ordered `foreground_model_fallbacks`; the retired `default_model_fallbacks` key is ignored. Eligible pre-content availability failures can advance through at most ten owner-visible exact model candidates, while missing configuration/endpoints, provider/schema errors, clean empty completions, and post-content failures remain on the selected route and surface an error. Once a route produces substantive text/reasoning or a tool call it is pinned as the answering route.

Fallback candidates receive route-neutral context shaping. Only compaction performed for the answering route is persisted. Chat and agent metadata record requested/actual model and endpoint identity, round-by-round route transitions, and costs against the route that actually answered; the browser renders same-model endpoint changes as well as model changes.

## Context Preface

`routes.chat_helpers.build_chat_context()` owns the shared route pipeline: preset extraction, preprocessing, user-message persistence, incognito/no-memory/RAG/skills flags, prefetched compare search, YouTube transcript context, research-spinoff grounding, model normalization, and compaction.

`src.chat_processor.ChatProcessor.build_context_preface()` owns source preface construction. It can add memory, RAG, web search, URL page content, and skills index context before the model call.

Chat preface enhances the model's context. It must not rewrite the user message or force literal-vs-fetch interpretation before the model sees the request. See [context-building.md](context-building.md).

Chat-owned external context must enter the model through `untrusted_context_message()` unless a different treatment is explicitly documented. This includes memory, RAG, web search, URL fetches, prefetched search context, YouTube transcripts, research injection, and manual context injection.

## Modes And Handoffs

Chat can dispatch to normal LLM calls, agent mode, research mode, or compare-related flows. Session mode is stored on `sessions.mode`.

Legacy plan-mode backend plumbing still exists below chat, but `routes/chat_routes.py` currently forces browser/form `plan_mode` input off and the old visible plan window frontend module is not part of the current SPA. Treat plan-mode changes as compatibility work unless the UI contract is intentionally reintroduced.

Current call sites include:

- chat/research dispatch in `routes/chat_routes.py`;
- agent execution in `src/agent_loop.py`;
- deep research orchestration in `src/research_handler.py`;
- compare entry points in canonical `routes/compare/compare_routes.py` and frontend compare modules.

Agent-mode tool access is gated in layers. Chat route toggles and privileges
build a disabled-tool set; incognito and compare mode remove persistence-heavy
or UI-breaking tools; `src.action_intents.message_needs_tools()` provides
conservative regex auto-escalation hints; `src.agent_loop`,
`src.tool_security`, `src.tool_execution`, and internal loopback validation
remain server-side enforcement owners.

`allow_bash` and `allow_web_search` can be read from the JSON request body for browser chat posts that do not submit traditional form fields.

Web search tools are per-turn explicit opt-in. Either `allow_web_search=true`
or `use_web=true` can enable `web_search`/`web_fetch`, but an explicit
`allow_web_search=false` wins over `use_web=true` and keeps those tools
disabled. Explicit latest-turn web-search intent can still auto-escalate into
agent mode and narrows the available tool set toward `web_search`/`web_fetch`,
but it no longer re-enables web tools after an explicit denial or global
disable.

Guide-only/no-tools requests build an effective tool policy before preprocessing and agent dispatch. That policy suppresses tool-backed preprocessing/background extraction/research, disables schemas and MCP for the turn, and is still enforced by `src.tool_execution` if a model emits a tool call anyway.

When route context is trimmed without full compaction, chat emits a
`context_trimmed` SSE event and carries before/after message/token counts into
metrics. Provider reasoning/thinking deltas are streamed for live UI handling
but kept out of the visible saved assistant content and stored in metadata when
available.

## Attachments

`src.chat_handler.ChatHandler.preprocess_message()` owns owner-scoped upload-id resolution, attachment metadata, YouTube transcript/comment preprocessing, image/VL behavior, and enhanced text used by chat. `src.document_processor.build_user_content()` owns conversion of uploaded/chat-attached files into model-ready text or multimodal blocks. `src.attachment_refs` owns persisted text/reference normalization, and `SessionManager` owner-reserves attachment ids before appending or replacing durable message rows. `static/js/fileHandler.js` owns frontend pending-file state.

Attachment-only sends are valid. Missing or unauthorized ids are skipped during preprocessing, while a missing/wrong-owner durable reference aborts a message/history replacement before existing transcript rows are removed. Upload failures keep pending files for retry, unsupported media can degrade to text markers, optional Office/PDF/VL dependencies can emit extraction banners, Office attachments can create markdown documents when extracted server-side, and fillable-PDF auto-document failures fall back to normal PDF extraction. `chat_messages.content` and FTS do not retain provider data URLs; structured references stay in metadata for reloads. Chat does not own upload bytes or durable document storage; it requests document/upload behavior from those subsystems.

Frontend chat distinguishes normal resend from regenerate-from-here: normal resend appends a fresh user copy and carries upload IDs where available, while regeneration truncates from the selected point. AI-message delete prompts before removing the AI response plus preceding user turn. Desktop Enter submits; mobile Enter inserts a newline unless another platform-specific send control is used.

Native document tool outputs can open or refresh the document editor from
tool-result metadata, so the UI can recover if a later `doc_update` stream event
is missed. The chat renderer also hides raw/incomplete leaked tool JSON and
document fences from normal transcript text.

When untrusted external/workspace content has entered the agent context, high-impact tool calls pause as exact approval cards instead of executing. The browser can allow the rest of the interrupted task, allow this chat session, or deny; it submits only the opaque id/decision with an empty control-plane message and does not mutate the composer. The server restores the sealed first action plus private selected tools/query, revalidates policy and document freshness, consumes the first action, and resumes without persisting a synthetic user message. Task scope ends with that resumed run. Chat scope persists the resolved card and marks later context only for that exact session; forks do not inherit it. A normal message retires an unresolved card while preserving taint.

## Security And Provenance

`/api/chat` and `/api/chat_stream` verify session ownership before loading the session. Chat privilege gates enforce allowed models and daily message caps before LLM work. Active document injection, session auth/header recovery, endpoint repair, upload-id resolution and reservation, memory/RAG retrieval, and post-response work must stay owner-scoped.

The scoped API-token chat surface is `/api/v1/chat`. Browser chat routes can receive bearer-auth state from middleware, but route code must not assume `"api"` is a durable owner; API-token support requires explicit scope checks and token-owner attribution.

Incognito disables memory, skill, and chat-history tools and skips assistant DB persistence, but current user-message persistence and later cleanup are not a strict no-write guarantee. Treat incognito changes as security-sensitive until that contract is clarified.

## Search Boundary

`GET /api/search` in `routes/chat_routes.py` is chat-message search for the UI and slash commands. Web search routes are owned by canonical `routes/search/search_routes.py`; chat and agent web context call through `src.search`, compatibility shims, and search content fetchers. Do not confuse chat-history search with external web retrieval.

## Degraded And Compatibility Behavior

- Missing ChromaDB, embeddings, memory vectors, RAG managers, or skills indexes should remove injected context or fall back to keyword/text behavior without failing chat.
- Direct URL prefetch failures become compact untrusted context stating that the page was not read, with only transport-owned HTTP/size/rate-limit status where recognized; raw URLs, exception text, and response-controlled diagnostics are not echoed into logs or model context.
- Sessions hydrate legacy string headers and multimodal JSON-array content, export text/HTML/Markdown after flattening non-string blocks, can lazy-load from DB when cached state is empty, and preserve old history/index delete behavior where needed.
- Initial shell/session loading is non-blocking: the sidebar can render before a selected transcript is hydrated, and full transcript hydration is deferred until display or a model send requires it.
- Chat repairs empty selected models and orphaned endpoint references before provider calls when possible.
- Deleted-session stream writes fail closed.
- Docker/native endpoint differences are owned by runtime/model setup, but chat sessions depend on the saved endpoint URLs and headers.
- Copying a response from the UI copies the displayed answer text and omits hidden reasoning/thinking segments.

## Current Gaps

- Chat, agent, research, and compare orchestration still meet in a large route file.
- Context preface behavior is spread across `routes/chat_helpers.py`, `src/chat_processor.py`, route injections, and agent/tool paths.
- Detached stream lifecycle spans `routes/chat_routes.py`, `src/agent_runs.py`, `static/js/chat.js`, `static/js/sessions.js`, and non-chat callers.
- Some frontend stream state is still global/module-level in `static/js/chat.js` and needs careful session isolation when adding background or resumable flows.
- Chat lacks route-level SSE regression tests for `/api/chat_stream`, live resume/stop/status, mode handoff, persistence metadata, partial-save behavior, attachment/doc-update events, browser timezone offset/workspace handling, and literal URL context intent.
- Bearer-token behavior on browser chat routes and incognito persistence need explicit contract decisions and regression coverage.
