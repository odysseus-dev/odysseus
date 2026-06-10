# Chat

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers current chat behavior in:

- `routes/chat_routes.py` and `routes/chat_helpers.py`;
- `routes/session_routes.py` and `routes/history_routes.py`;
- `src/chat_helpers.py`;
- `src/agent_runs.py`;
- `src/chat_handler.py` and `src/chat_processor.py`;
- `core/session_manager.py` and `core/models.py`;
- `src/context_budget.py`, `src/context_compactor.py`, and `src/topic_analyzer.py`;
- `routes/workspace_routes.py` for workspace selection support;
- frontend modules `static/js/chat.js`, `static/js/chatStream.js`, `static/js/chatRenderer.js`, `static/js/sessions.js`, `static/js/search-chat.js`, `static/js/compare/stream.js`, `static/js/planWindow.js`, `static/js/workspace.js`, `static/js/streamingSegmenter.js`, `static/js/group.js`, and `static/js/notes.js`;
- integration points with uploads, documents, compare, research, agent tools, memory, RAG, search, and model endpoints.

## Session Ownership

`core.session_manager.SessionManager` owns session persistence and message writes. `routes/session_routes.py` owns session list/create/update/archive/delete/folder/importance behavior for the sidebar. `routes/history_routes.py` owns history/topic surfaces.

`core.models.Session` and `ChatMessage` are pure data containers. They do not own persistence; `Session.add_message()` delegates to the configured session manager when present.

## Streaming

`routes/chat_routes.py` owns `/api/chat`, `/api/chat_stream`, detached stream resume/stop/status, injected context, chat-message search, and rewrite routes. Streaming is the main UI path.

`static/js/chat.js` owns send/abort/continue UI state, the main fetch/read loop, SSE parsing, rendering dispatch, plan-window handoff, workspace form wiring, and background/resumable stream tracking. `static/js/chatStream.js` owns UI-control event handling and stream/research notification helpers. `static/js/sessions.js` polls server stream status after refresh or session switch.

Runtime behavior:

- the `/api/chat*` prefix is exempt from the global request hard timeout;
- browser chat sends `X-Tz-Offset`; route code forwards it into `routes.calendar_routes` request-local state so note/calendar tool parsing can anchor natural-language dates to the user clock;
- browser chat can send a selected workspace path; route code validates it as an existing directory and forwards it so agent file/shell tools are confined by `src.tool_execution`;
- stream callbacks can outlive a deleted session, so persistence must fail closed instead of recreating orphan messages;
- message metadata carries timestamps, metrics, tool events, sources, and related UI state;
- metadata preserves both requested and actual reply models when provider streams or fallbacks report them;
- multimodal content can be a list of content blocks, not just a string.

`src.agent_runs` owns detached in-memory stream runs, replay buffers, replacement cancellation, resume subscribers, explicit stop, and terminal-buffer eviction. Closing the SSE connection does not necessarily stop generation. `static/js/chat.js` can live-resume a still-running detached stream through `/api/chat/resume/{session_id}`; rich responses reload from DB for canonical rendering. Detached runs are process-local and do not survive server restart.

Provider adapters live below chat in `src.llm_core`. Chat consumes normalized SSE output, fallback/error events, reasoning/tool deltas, and metrics. Model fallback only switches before output has started; after partial output, errors are surfaced to the stream instead of silently retrying a new model.

## Context Preface

`routes.chat_helpers.build_chat_context()` owns the shared route pipeline: preset extraction, preprocessing, user-message persistence, incognito/no-memory/RAG/skills flags, prefetched compare search, YouTube transcript context, model normalization, and compaction.

`src.chat_processor.ChatProcessor.build_context_preface()` owns source preface construction. It can add memory, RAG, web search, URL page content, and skills index context before the model call.

Chat preface enhances the model's context. It must not rewrite the user message or force literal-vs-fetch interpretation before the model sees the request. See [context-building.md](context-building.md).

Chat-owned external context must enter the model through `untrusted_context_message()` unless a different treatment is explicitly documented. This includes memory, RAG, web search, URL fetches, prefetched search context, YouTube transcripts, research injection, and manual context injection.

## Modes And Handoffs

Chat can dispatch to normal LLM calls, agent mode, research mode, or compare-related flows. Session mode is stored on `sessions.mode`.

Current call sites include:

- chat/research dispatch in `routes/chat_routes.py`;
- agent execution in `src/agent_loop.py`;
- deep research orchestration in `src/research_handler.py`;
- compare entry points in `routes/compare_routes.py` and frontend compare modules.

Agent-mode tool access is gated in layers. Chat route toggles and privileges build a disabled-tool set; incognito and compare mode remove persistence-heavy or UI-breaking tools; `src.action_intents.message_needs_tools()` provides conservative regex auto-escalation hints; `src.agent_loop`, `src.tool_security`, `src.tool_execution`, and internal loopback validation remain server-side enforcement owners.

Guide-only/no-tools requests build an effective tool policy before preprocessing and agent dispatch. That policy suppresses tool-backed preprocessing/background extraction/research, disables schemas and MCP for the turn, and is still enforced by `src.tool_execution` if a model emits a tool call anyway.

## Attachments

`src.chat_handler.ChatHandler.preprocess_message()` owns owner-scoped upload-id resolution, attachment metadata, YouTube transcript/comment preprocessing, image/VL behavior, and enhanced text used by chat. `src.document_processor.build_user_content()` owns conversion of uploaded/chat-attached files into model-ready text or multimodal blocks. `static/js/fileHandler.js` owns frontend pending-file state.

Attachment-only sends are valid. Missing or unauthorized upload ids are skipped, upload failures keep pending files for retry, unsupported media can degrade to text markers, optional Office/PDF/VL dependencies can emit extraction banners, and fillable-PDF auto-document failures fall back to normal PDF extraction. Chat does not own durable document storage; it requests document/upload behavior from those subsystems.

## Security And Provenance

`/api/chat` and `/api/chat_stream` verify session ownership before loading the session. Chat privilege gates enforce allowed models and daily message caps before LLM work. Active document injection, session auth/header recovery, endpoint repair, upload-id resolution, memory/RAG retrieval, and post-response work must stay owner-scoped.

The scoped API-token chat surface is `/api/v1/chat`. Browser chat routes can receive bearer-auth state from middleware, but route code must not assume `"api"` is a durable owner; API-token support requires explicit scope checks and token-owner attribution.

Incognito disables memory, skill, and chat-history tools and skips assistant DB persistence, but current user-message persistence and later cleanup are not a strict no-write guarantee. Treat incognito changes as security-sensitive until that contract is clarified.

## Search Boundary

`GET /api/search` in `routes/chat_routes.py` is chat-message search for the UI and slash commands. Web search routes are owned by `routes/search_routes.py`; chat and agent web context call through `src.search`, compatibility shims, and search content fetchers. Do not confuse chat-history search with external web retrieval.

## Degraded And Compatibility Behavior

- Missing ChromaDB, embeddings, memory vectors, RAG managers, or skills indexes should remove injected context or fall back to keyword/text behavior without failing chat.
- Sessions hydrate legacy string headers and multimodal JSON-array content, export text/HTML/Markdown after flattening non-string blocks, can lazy-load from DB when cached state is empty, and preserve old history/index delete behavior where needed.
- Chat repairs empty selected models and orphaned endpoint references before provider calls when possible.
- Deleted-session stream writes fail closed.
- Docker/native endpoint differences are owned by runtime/model setup, but chat sessions depend on the saved endpoint URLs and headers.

## Current Gaps

- Chat, agent, research, and compare orchestration still meet in a large route file.
- Context preface behavior is spread across `routes/chat_helpers.py`, `src/chat_processor.py`, route injections, and agent/tool paths.
- Detached stream lifecycle spans `routes/chat_routes.py`, `src/agent_runs.py`, `static/js/chat.js`, `static/js/sessions.js`, and non-chat callers.
- Some frontend stream state is still global/module-level in `static/js/chat.js` and needs careful session isolation when adding background or resumable flows.
- Chat lacks route-level SSE regression tests for `/api/chat_stream`, live resume/stop/status, mode handoff, persistence metadata, partial-save behavior, attachment/doc-update events, browser timezone offset/workspace handling, and literal URL context intent.
- Bearer-token behavior on browser chat routes and incognito persistence need explicit contract decisions and regression coverage.
