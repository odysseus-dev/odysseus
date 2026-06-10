# Search

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers web search, URL fetching, and search-derived context in:

- `routes/search_routes.py`;
- `services/search/*` and exported `services.search.SearchService`;
- `src/search/*` compatibility and live duplicate modules;
- search call sites in `src/chat_processor.py`, `src/tool_execution.py`, `src/session_search.py`, `src/research_handler.py`, `src/deep_research.py`, and `services/research/research_handler.py`;
- search settings in `src/settings.py`, `static/js/settings.js`, and compare/research frontend search callers;
- YouTube context paths in `src/youtube_handler.py` and `services/youtube/youtube_handler.py`;
- research visual/report consumers in `src/visual_report.py` and `routes/research_routes.py`;
- tests under `tests/test_search_*`, `tests/test_service_search_*`, `tests/test_services_search_*`, `tests/test_security_regressions.py`, `tests/test_agent_loop.py`, `tests/test_deep_research_*`, `tests/test_research_handler_*`, `tests/test_youtube_*`, and `tests/test_og_image_extraction.py`.

`routes/chat_routes.py` also exposes `GET /api/search`, but that route searches chat messages and belongs to chat history behavior, not web search.

## Route Flows

`routes/search_routes.py` owns the browser/API web-search routes:

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

`services/search/query.py` owns query enhancement. `services/search/ranking.py` owns result ranking, including word-boundary title/snippet/subject matching so short query terms do not match unrelated substrings.

## Provider Settings And Fallback

`src/settings.py` owns default provider settings. The default provider is SearXNG, with DuckDuckGo as the default fallback chain. `static/js/settings.js` owns the admin search settings UI, provider key presence display, provider selection, and fallback ordering. SafeSearch is a backend/provider setting today, not a visible Settings control.

Provider API keys come from settings or environment at call time. Web config routes expose availability/presence only, non-admin settings reads are scrubbed, and chat settings tools cannot set provider credentials.

Runtime behavior:

- disabled search returns disabled/unavailable text in the comprehensive path;
- missing keyed-provider secrets return empty provider results instead of exposing secrets;
- SearXNG retries through JSON variants before HTML fallback;
- comprehensive search retries providers and then walks the fallback chain;
- `/api/search/query` is a direct provider test/query path and does not use the comprehensive fallback chain.

## Content Fetching

`services/search/content.py` owns webpage fetch/extract behavior for the services path:

- public HTTP/HTTPS URL checks;
- DNS fail-closed behavior;
- rejection of localhost, metadata, private, reserved, multicast, and link-local targets;
- redirect revalidation on each hop;
- metadata, Open Graph image, list, table, code block, PDF, and text extraction;
- JS-heavy empty result hints;
- cache writes;
- empty/error result shape.

`src/search/content.py` is now a compatibility alias to `services.search.content`; chat URL auto-fetch, agent `web_fetch`, and deep research keep the `src.search` import path but share the services implementation.

Content failures are caller-shaped:

- comprehensive search drops failed page fetches and keeps usable search context;
- `web_fetch` returns tool errors, including bot-protection and HTTP-status failures;
- direct URL chat prefetch expects unavailable context rather than fabricated content;
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

Search owns Open Graph image extraction for fetched pages. Research owns promotion of those images into research sources and visual reports. This is not a standalone web image-search provider or gallery image proxy.

## YouTube

`src/youtube_handler.py` owns chat YouTube context. `services/youtube/youtube_handler.py` is still used by diagnostics/tests and is not fully consolidated with the `src` copy.

YouTube transcript and comment content is search-like external context. Fixes to parsing, guards, unavailable states, or formatting need parity checks across both handlers until one path is removed.

## Compatibility State

`src/search/core.py`, `src/search/providers.py`, `src/search/ranking.py`, `src/search/cache.py`, `src/search/content.py`, `src/search/query.py`, and `src/search/analytics.py` are compatibility shims or module aliases around `services.search`. Ranking helpers exposed through `src.search.ranking` include recency scoring, result ranking, naive-UTC handling, `_SPORTS_HINT_RE`, and age formats.

Still-separate compatibility-sensitive copies include:

- `src/youtube_handler.py` and `services/youtube/youtube_handler.py`.

Tests intentionally cover selected behavior through both import paths, but coverage is not complete parity.

## Context Policy

Search results, fetched pages, Open Graph metadata, and YouTube transcript/comment content are untrusted context.

Chat search, chat URL prefetch, compare presearch, and YouTube context wrap inserted content through the shared untrusted-context message helpers. Agent `web_search`/`web_fetch` results are read-only tool outputs and must not be treated as instructions.

Deep research wraps fetched webpage content through `untrusted_context_message("webpage", content)` before extractor calls, though search result/failure shapes still differ from chat and agent tools.

## Optional And Platform Behavior

`duckduckgo-search` is optional; provider code has an HTML fallback. PDF extraction uses `pdfminer.six` only when installed. Native SearXNG defaults to `http://localhost:8080`; Docker uses the compose `searxng` service URL and pins the SearXNG image with a healthcheck.

`httpx` and BeautifulSoup are required runtime dependencies for the active search/fetch path.

## Current Gaps

- Search route handlers need direct tests for request body formats, provider validation, provider availability, and route error/empty-result shapes.
- Agent search, chat search prefetch, and research search do not yet share a single result/failure shape.
- `src/search` and `services/search` are mostly consolidated through shims, but import-path parity tests remain important.
- Deep-research webpage-content extraction uses the shared untrusted wrapper, but synthesis/reuse boundaries still need route/tool tests.
- Search-sourced `og_image` URLs need an explicit privacy/security decision: documented direct browser loads, public-URL validation, or a same-origin proxy.
- Route and integration tests do not fully pin chat/compare/YouTube untrusted-context insertion.
