# Context Building

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers model-context construction in:

- `src/chat_processor.py`;
- `src/chat_handler.py` and `src/youtube_handler.py`;
- `routes/chat_helpers.py` and context injection in `routes/chat_routes.py`;
- `src/agent_loop.py`;
- `src/tool_execution.py`;
- `src/attachment_refs.py` and uploaded-file manifest construction in
  `routes/chat_helpers.py`;
- `src/tool_policy.py`;
- `src/prompt_security.py`;
- `src/tool_capabilities.py`, `src/tool_approval_scopes.py`, and `src/tool_approvals.py`;
- transport primitives in `src/outbound_fetch.py` plus fetch/extraction adapters in `src/search/content.py` and `services/search/content.py`;
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
- `untrusted_context_message(label, content)` wraps source content as user-role data with `metadata.trusted = False`, provenance origin, and an `arm_tool_gate`/`tool_gate_untrusted` signal that defaults to arming the server-owned tool gate.

Current untrusted context sources include:

- fetched URLs and web search results;
- webpage content passed into deep-research extraction;
- YouTube transcripts/comments;
- RAG/personal document chunks;
- memories and skills;
- notes and active editor documents;
- emails and attachments;
- tool output from external/user-controlled data.

Live multimodal provider blocks can contain data URLs, but persisted and
tool-facing context uses stable attachment references. Tool manifests carry an
`odysseus://attachment/<id>` URI and owner-checked read policy; local paths are
compatibility data added only after owner and root-confinement checks. Persisted
chat context keeps readable text/reference lines rather than reinserting raw
media bytes into later turns or search state.

## URL, Search, And Tool-Derived Context

Chat URL prefetch and agent `web_fetch` are different paths. Chat prefetch happens before the model call; `web_fetch` is a tool the model may choose later. Both should converge on the same intent: enrich context when content is available, represent unavailable content when it is not, and let the model interpret the user request.

Search results and fetched pages are evidence. `web_search` should not force a page fetch unless its explicit contract says it does. Failed fetches should not crash chat or silently imply content was read. Canonical search content fetchers can extract readable text from HTML, `text/*`, Markdown, `.txt`, `.json`, and `.jsonl` responses and should return shaped error results for HTTP status failures. URL fetches validate every redirect hop and pin the outbound connection to a public IP resolved during validation, so context-building callers do not need a second DNS-rebinding guard.

Current behavior is not yet unified:

- successful chat URL prefetch is wrapped as untrusted context; failed prefetch now adds a compact untrusted statement that the page was not read, recognizes only transport-owned HTTP/size/rate-limit categories, and suppresses raw exception/response text;
- agent `web_fetch` returns explicit URL-specific tool errors for timeout, unsupported scheme, fetch failure, or no readable text;
- comprehensive search reports provider-chain failures, but individual page-fetch failures can be logged and omitted;
- YouTube fetching is owned by `ChatHandler`/`youtube_handler`, while `routes.chat_helpers` only wraps the resulting transcript/comment strings.

`src.outbound_fetch` owns reusable synchronous public-URL classification, per-hop DNS resolution/pinning, redirect handling, and body budgets. `services/search/core.py` owns `comprehensive_web_search()` orchestration. `services.search.content` owns content extraction and adapts the shared transport; `src/search/core.py` and `src/search/content.py` preserve compatibility imports without a second implementation.

## Tool Result Envelope

`src.tool_execution` executes and formats tools. Tool output caps live in `src.constants` and are re-exported through older facades; shared native-tool truncation lives in `src.tool_utils`. `src.agent_loop._append_tool_results()` owns model re-entry: native tool calls return as provider-style `role: "tool"` messages with untrusted metadata, while fenced-tool results use the untrusted wrapper. Classification considers both the requested tool and the result payload, so remote or stored model-visible content can arm the session gate even on a failed tool status.

Taint is server-owned continuation state, not a model instruction. After untrusted external/workspace context, low-impact reads can continue, but high-impact, unknown, and arbitrary MCP actions become proposals that produce an exact approval card. The server seals the exact first action plus private continuation tool/query state; document actions also bind the current document version and digest. A chat decision can allow the resumed task or persist a grant for later turns in that exact chat, while non-chat callers remain single-action. Blocked/approval placeholders and content-free failures do not recursively arm the gate.

Context budgeting uses known model context windows when available. `src.context_budget` treats the default 6000-token value as an automatic sentinel, scales to a capped fraction of known context length for non-explicit budgets, and leaves unknown windows on conservative defaults.

Side-effect enforcement lives outside context building. Chat route disabled-tool policy, `src.tool_security`, `src.tool_execution`, and `do_app_api()` block unsafe tool execution; prompt wording alone is not the authority.

Guide-only/no-tools policy can suppress context acquisition before the model call. `src.tool_policy` feeds chat route preprocessing and agent-loop assembly so tool-backed search/research/memory/RAG/skills/local-context paths are skipped when the latest user turn explicitly forbids tools.

## Degraded And Optional Dependencies

- ChromaDB, HTTP embeddings, and FastEmbed are installed/expected in normal setups but must degrade cleanly when a service, package, or embedding backend is unavailable.
- `src.rag_singleton.get_rag_manager()` owns RAG startup retry throttling; `src.rag_vector.VectorRAG` is the live owner-filtered path; `src.rag_manager.RAGManager` is compatibility/backward-compat behavior.
- Memory-vector and tool-index retrieval can fall back to keyword/text behavior when vector stores or embeddings fail.
- Docker compose and native installs use different Chroma host defaults; model endpoint loopback rewriting is owned by model/runtime specs.

## Current Call Sites Include

- `ChatProcessor.build_context_preface()` for memory, RAG, web search, URL content, and skills index;
- `ChatHandler.preprocess_message()` and the canonical `services.youtube.youtube_handler` import path for YouTube fetch/format, then `routes/chat_helpers.py` for wrapping prefetched search/Youtube context;
- `routes/chat_routes.py` research context injection;
- `src.agent_loop` for active editor document, skill context, and tool-result reinsertion;
- uploaded-file manifest/reference context for agent tools and later chat turns;
- `src.tool_execution` for `web_search`, `web_fetch`, file, shell, MCP, and other tool outputs;
- `src.deep_research` and research handlers for search/fetch/extract flows used by research jobs, with fetched webpage text wrapped before extraction and analyzed URLs tracked separately from source snippets.

## Current Gaps

- URL/search context result shape is not unified across chat prefetch, agent tools, and research.
- Failed fetch representation remains inconsistent outside direct chat URL prefetch, especially in comprehensive search and research aggregation.
- Tool/context wording is spread across schema, prompt, and retrieval surfaces.
- Source-specific wrapping and unavailable-state behavior still needs broader focused coverage for literal URL intent, research, RAG/memory/skills, and YouTube; external tool results and approval continuation now have dedicated gate/taint regressions.
- Compare pre-search context is computed but may not be submitted through the current compare stream form.
