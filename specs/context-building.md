# Context Building

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers model-context construction in:

- `src/chat_processor.py`;
- `src/chat_handler.py` and `src/youtube_handler.py`;
- `routes/chat_helpers.py` and context injection in `routes/chat_routes.py`;
- `src/agent_loop.py`;
- `src/tool_execution.py`;
- `src/tool_policy.py`;
- `src/prompt_security.py`;
- URL fetchers in `src/search/content.py` and `services/search/content.py`;
- search orchestration in `services/search/core.py` and the compatibility wrapper in `src/search/core.py`;
- RAG and personal docs in `src/rag_singleton.py`, `src/rag_vector.py`, `src/rag_manager.py`, and `src/personal_docs.py`;
- research flows in `src/deep_research.py`, `src/research_handler.py`, and `services/research/research_handler.py`;
- memory and skills in `src/memory.py` and `services/memory/*`;
- related policy in `THREAT_MODEL.md`.

## Contract

Context-building tools gather evidence. They do not own user-intent routing.

Runtime rules:

- if external context is available, add it as compact untrusted source data;
- if an attempted source is unavailable and relevant, represent the unavailable state explicitly with source and reason when known;
- preserve the user's original message for the model;
- do not use regex preprocessing to force literal-vs-fetch intent;
- do not disable tools or force a reply style solely because preprocessing found a URL.

## Untrusted Data

`src.prompt_security` owns the untrusted wrapper:

- `UNTRUSTED_CONTEXT_POLICY` states global model policy;
- `untrusted_context_message(label, content)` wraps source content as user-role data with `metadata.trusted = False`.

Current untrusted context sources include:

- fetched URLs and web search results;
- webpage content passed into deep-research extraction;
- YouTube transcripts/comments;
- RAG/personal document chunks;
- memories and skills;
- notes and active editor documents;
- emails and attachments;
- tool output from external/user-controlled data.

## URL, Search, And Tool-Derived Context

Chat URL prefetch and agent `web_fetch` are different paths. Chat prefetch happens before the model call; `web_fetch` is a tool the model may choose later. Both should converge on the same intent: enrich context when content is available, represent unavailable content when it is not, and let the model interpret the user request.

Search results and fetched pages are evidence. `web_search` should not force a page fetch unless its explicit contract says it does. Failed fetches should not crash chat or silently imply content was read.

Current behavior is not yet unified:

- successful chat URL prefetch is wrapped as untrusted context, but failed chat URL prefetch can be dropped;
- agent `web_fetch` returns explicit URL-specific tool errors for timeout, unsupported scheme, fetch failure, or no readable text;
- comprehensive search reports provider-chain failures, but individual page-fetch failures can be logged and omitted;
- YouTube fetching is owned by `ChatHandler`/`youtube_handler`, while `routes.chat_helpers` only wraps the resulting transcript/comment strings.

`services/search/core.py` owns `comprehensive_web_search()` orchestration. `src/search/core.py` is a compatibility wrapper. `src/search/content.py` now aliases the canonical `services.search.content` module so old imports do not create a second fetch/extract implementation.

## Tool Result Envelope

`src.tool_execution` executes and formats tools. Tool output caps live in `src.constants` and are re-exported through older facades. `src.agent_loop._append_tool_results()` owns model re-entry: native tool calls return as provider-style `role: "tool"` messages, while fenced-tool results can become a bracketed user message. These results are untrusted, but they do not all currently use `untrusted_context_message()` or `metadata.trusted = False`.

Side-effect enforcement lives outside context building. Chat route disabled-tool policy, `src.tool_security`, `src.tool_execution`, and `do_app_api()` block unsafe tool execution; prompt wording alone is not the authority.

Guide-only/no-tools policy can suppress context acquisition before the model call. `src.tool_policy` feeds chat route preprocessing and agent-loop assembly so tool-backed search/research/memory/RAG/skills/local-context paths are skipped when the latest user turn explicitly forbids tools.

## Degraded And Optional Dependencies

- ChromaDB, HTTP embeddings, and FastEmbed are installed/expected in normal setups but must degrade cleanly when a service, package, or embedding backend is unavailable.
- `src.rag_singleton.get_rag_manager()` owns RAG startup retry throttling; `src.rag_vector.VectorRAG` is the live owner-filtered path; `src.rag_manager.RAGManager` is compatibility/backward-compat behavior.
- Memory-vector and tool-index retrieval can fall back to keyword/text behavior when vector stores or embeddings fail.
- Docker compose and native installs use different Chroma host defaults; model endpoint loopback rewriting is owned by model/runtime specs.

## Current Call Sites Include

- `ChatProcessor.build_context_preface()` for memory, RAG, web search, URL content, and skills index;
- `ChatHandler.preprocess_message()` and `youtube_handler` for YouTube fetch/format, then `routes/chat_helpers.py` for wrapping prefetched search/Youtube context;
- `routes/chat_routes.py` research context injection;
- `src.agent_loop` for active editor document, skill context, and tool-result reinsertion;
- `src.tool_execution` for `web_search`, `web_fetch`, file, shell, MCP, and other tool outputs;
- `src.deep_research` and research handlers for search/fetch/extract flows used by research jobs, with fetched webpage text wrapped before extraction.

## Current Gaps

- URL/search context result shape is not unified across chat prefetch, agent tools, and research.
- Some failed fetch states are still easier for code to drop than to represent explicitly.
- Tool/context wording is spread across schema, prompt, and retrieval surfaces.
- Agent tool-result reinjection lacks a unified untrusted wrapper/metadata envelope across native, fenced, MCP, and app API outputs.
- Source-specific wrapping and unavailable-state behavior need focused tests for chat URL prefetch, literal URL intent, search context, deep-research extraction, RAG/memory/skills, YouTube, and tool results.
- Compare pre-search context is computed but may not be submitted through the current compare stream form.
