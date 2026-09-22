# Search

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers web search, URL fetching, and search-derived context in:

- canonical `routes/search/search_routes.py`, with `routes/search_routes.py` as a compatibility shim;
- reusable outbound transport primitives in `src/outbound_fetch.py`;
- `services/search/*` and exported `services.search.SearchService`;
- `src/search/*` compatibility aliases around canonical service modules;
- search call sites in `src/chat_processor.py`, `src/tool_execution.py`, `src/session_search.py`, `src/research_handler.py`, `src/deep_research.py`, and `services/research/research_handler.py`;
- search settings in `src/settings.py`, `static/js/settings.js`, and compare/research frontend search callers;
- YouTube context paths in `src/youtube_handler.py` and `services/youtube/youtube_handler.py`;
- research visual/report consumers in `src/visual_report.py` and `routes/research/research_routes.py`;
- tests under `tests/test_search_*`, `tests/test_service_search_*`, `tests/test_services_search_*`, `tests/test_security_regressions.py`, `tests/test_agent_loop.py`, `tests/test_deep_research_*`, `tests/test_research_handler_*`, `tests/test_youtube_*`, and `tests/test_og_image_extraction.py`.

`routes/chat_routes.py` also exposes `GET /api/search`, but that route searches chat messages and belongs to chat history behavior, not web search.

## Route Flows

`routes/search/search_routes.py` owns the browser/API web-search routes:

- `GET /api/search/config` returns search configuration with provider key presence, not secret values;
- `POST /api/search` calls `comprehensive_web_search(..., return_sources=True)` and returns `{context, sources, error?}`;
- `GET /api/search/providers` returns provider metadata and availability;
- `POST /api/search/query` calls one provider directly and returns `{results, provider, time, error?}` without ranking, fallback chains, cache formatting, or content fetch.

Compare mode uses both route shapes: shared presearch uses `/api/search`, while provider/search comparison panes use `/api/search/query`. Research panels can pass provider override settings through research routes into the deep-research search path.

Research provider naming is not fully normalized in the UI: some frontend selectors still use `google`, while provider dispatch expects `google_pse`.

## Search Pipeline

`services/search/core.py` owns `comprehensive_web_search()`. It coordinates provider selection, fallback chains, ranking, optional fetch/content extraction, formatted prompt context, cache invalidation, and analytics.

`services/search/service.py` owns `SearchService`, the async facade exported by `services.search` and `services`. It wraps the synchronous comprehensive search path off the event loop and maps route-style output into service result rows.

`services/search/providers.py` owns provider-specific calls for SearXNG, Brave, DuckDuckGo, Google PSE, Tavily, and Serper. `PROVIDER_INFO`, provider availability, missing-key behavior, and provider dispatch live there.

`services/search/query.py` owns query enhancement and sanitization, including stripping markdown/code-fence noise from model- or user-supplied queries before provider calls and extracting Unicode/non-ASCII capitalized entity names. `services/search/ranking.py` owns result ranking, including word-boundary title/snippet/subject matching so short query terms do not match unrelated substrings.

## Provider Settings And Fallback

`src/settings.py` owns default provider settings. The default provider is SearXNG, with DuckDuckGo as the default fallback chain. `static/js/settings.js` owns the admin search settings UI, provider key presence display, provider selection, and fallback ordering. SafeSearch is a backend/provider setting today, not a visible Settings control.

Provider API keys come from settings or environment at call time. Web config routes expose availability/presence only, non-admin settings reads are scrubbed, and chat settings tools cannot set provider credentials.

Runtime behavior:

- disabled search returns disabled/unavailable text in the comprehensive path;
- missing keyed-provider secrets return empty provider results instead of exposing secrets;
- SearXNG retries through JSON variants before HTML fallback, pins English/general-engine defaults where needed, and maps news/recency settings into provider time filters;
- comprehensive search retries providers and then walks the fallback chain;
- `/api/search/query` is a direct provider test/query path and does not use the comprehensive fallback chain. Direct provider result limits can be controlled dynamically by the caller.

## Content Fetching

`src.outbound_fetch.py` owns reusable synchronous public-URL classification, one-resolution-per-hop DNS pinning, redirect handling, and response-body budgets without search/content-extraction dependencies. `services/search/content.py` adapts those primitives and owns webpage extraction/cache/result shaping for the services path:

- public HTTP/HTTPS URL checks;
- DNS fail-closed behavior;
- rejection of localhost, metadata, private, reserved, multicast, and link-local targets;
- redirect revalidation on each hop;
- one-time public DNS resolution per hop plus an `httpcore`/`httpx` pinned
  transport that connects to the validated public IP while preserving the
  original URL, Host header, and TLS SNI, closing DNS-rebinding time-of-check
  drift;
- metadata, Open Graph image, list, table, code block, PDF, and text extraction;
- readable text extraction for `text/*`, Markdown, `.txt`, `.json`, `.jsonl`, and JSON content types;
- central User-Agent behavior through `WEB_FETCH_USER_AGENT`;
- soft and hard download byte caps through `WEB_FETCH_SOFT_MAX_BYTES` and `WEB_FETCH_HARD_MAX_BYTES`, with declared-length and streaming-budget checks; requests prefer identity transfer encoding so compressed bodies cannot bypass the effective body cap;
- JS-heavy empty result hints;
- cache writes;
- empty/error result shape, including explicit HTTP-status failures instead of raising through callers.

`src/search/content.py` is now a compatibility alias to `services.search.content`; chat URL auto-fetch, agent `web_fetch`, and deep research keep the `src.search` import path but share the services implementation.

Agent `web_fetch` raises the per-call budget only within the global hard cap, leads tool output with a partial-content notice when the download budget truncated the page, and then applies normal tool-output truncation so the notice survives.

Content failures are caller-shaped:

- comprehensive search drops failed page fetches and keeps usable search context;
- `web_fetch` returns tool errors, including bot-protection and HTTP-status failures;
- direct URL chat prefetch turns failures into compact untrusted unavailable-page context without exposing raw URL/exception/response diagnostics;
- deep research records search/provider failures separately from extraction failures.

## Result Shapes

Search does not have one canonical result shape yet. Current shapes include:

- `/api/search`: `{context, sources, error?}`;
- `/api/search/query`: `{results, provider, time, error?}`;
- `comprehensive_web_search(return_sources=True)`: formatted context plus `{url, title}` sources;
- `SearchService.search()`: service result rows;
- agent `web_search`: tool output text plus a hidden sources marker stripped by the agent loop;
- agent `web_fetch`: fetched page text or tool error;
- deep research: findings, cited sources, optional source images, and `_last_search_error` state.

Chat/session transcript search is separate from web search but now uses `chat_messages_fts` when available, sanitizes FTS queries, and batches message lookup after FTS hits to avoid per-hit database reads.

Search owns Open Graph image extraction for fetched pages. Research owns promotion of those images into research sources and visual reports. This is not a standalone web image-search provider or gallery image proxy.

## YouTube

`services/youtube/youtube_handler.py` owns YouTube URL detection, id extraction, transcript, comment, and formatting behavior. `src/youtube_handler.py` is a compatibility alias to the canonical services module so startup `init_youtube()` state and chat imports share one implementation.

YouTube transcript and comment content is search-like external context. URL parsing covers common watch, mobile/music, embed, `/v/`, shorts, live, and `youtu.be` forms and must tolerate non-string input.

## Compatibility State

`src/search/core.py`, `src/search/providers.py`, `src/search/ranking.py`, `src/search/cache.py`, `src/search/content.py`, `src/search/query.py`, and `src/search/analytics.py` are compatibility shims or module aliases around `services.search`. Ranking helpers exposed through `src.search.ranking` include recency scoring, result ranking, naive-UTC handling, `_SPORTS_HINT_RE`, and age formats.

`src.youtube_handler` remains a compatibility import path, but it should resolve to the same module object as `services.youtube.youtube_handler`.

## Context Policy

Search results, fetched pages, Open Graph metadata, and YouTube transcript/comment content are untrusted context.

Chat search, chat URL prefetch, compare presearch, and YouTube context wrap inserted content through the shared untrusted-context message helpers. Agent `web_search`/`web_fetch` results are read-only tool outputs and must not be treated as instructions.

Deep research wraps fetched webpage content through `untrusted_context_message("webpage", content)` before extractor calls, though search result/failure shapes still differ from chat and agent tools.

## Optional And Platform Behavior

`ddgs` is optional; provider code has an HTML fallback. Search cache and analytics state live under the shared data dir and mkdir failures in read-only image layers are tolerated where possible. PDF extraction uses `pdfminer.six` only when installed. Native SearXNG defaults to `http://localhost:8080`; Docker uses the compose `searxng` service URL and pins the SearXNG image with a healthcheck.

Compose preserves retained SearXNG settings but runs `scripts/migrate_searxng_settings.py` before startup to add missing `use_default_settings: true` inheritance. The migration accepts only a regular single-document YAML mapping, preserves BOM/newline/style/ownership/mode, writes and directory-fsyncs atomically, and no-ops when the key exists. Compose treats migration failure as non-fatal so SearXNG health reports the retained-file problem instead of the wrapper command preventing startup.

`httpx` and BeautifulSoup are required runtime dependencies for the active search/fetch path.

## Current Gaps

- Search route handlers need direct tests for request body formats, provider validation, provider availability, and route error/empty-result shapes.
- Agent search, chat search prefetch, and research search do not yet share a single result/failure shape.
- `src/search` and `services/search` are mostly consolidated through shims, but import-path parity tests remain important.
- Deep-research webpage-content extraction uses the shared untrusted wrapper, but synthesis/reuse boundaries still need route/tool tests.
- Search-sourced `og_image` URLs need an explicit privacy/security decision: documented direct browser loads, public-URL validation, or a same-origin proxy.
- Route and integration tests do not fully pin chat/compare/YouTube untrusted-context insertion.
