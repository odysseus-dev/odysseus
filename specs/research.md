# Research

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers deep research behavior in:

- app wiring and timeout policy in `app.py` and `src/app_initializer.py`;
- browser/API routes in `routes/research_routes.py`;
- chat-triggered research in `routes/chat_routes.py`;
- diagnostics in `routes/diagnostics_routes.py`;
- scheduled research in `routes/task_routes.py` and `src/task_scheduler.py`;
- active runtime code in `src/research_handler.py`, `src/deep_research.py`, `src/research_utils.py`, and `src/visual_report.py`;
- search/fetch dependencies in `src.search`, `services.search`, and the `src.search.content` compatibility alias;
- compatibility/public service code in `services/research/research_handler.py` and `services/research/service.py`;
- agent tools in `src/tool_implementations.py`, `src/tool_execution.py`, and `src/tool_index.py`;
- research CLI access in `scripts/odysseus-research`;
- frontend modules `static/js/research/panel.js`, `static/js/research/jobs.js`, `static/js/researchSynapse.js`, `static/js/chat.js`, `static/js/chatRenderer.js`, `static/js/chatStream.js`, `static/js/documentLibrary.js`, `static/js/sessions.js`, and compare stream research UI;
- persisted reports under `data/deep_research/*.json`;
- tests under `tests/test_research_*`, `tests/test_deep_research_*`, `tests/test_visual_report*.py`, `tests/test_services_research_low_quality_sources.py`, research auth regressions, endpoint fallback tests, and research CLI tests.

## Current Call Sites Include

- panel-launched research through `/api/research/start`;
- chat-stream research mode, including clarification, continuation from prior research JSON, progress events, and consumed results;
- non-streaming chat inline research context;
- compare/chat frontend research indicators;
- agent `trigger_research` and `manage_research`;
- scheduled research tasks that write compatible report JSON directly;
- diagnostics `/api/test-research`;
- report library, visual report, hide/unhide image, archive/delete, spinoff, and CLI list/show/report/search/delete flows.

## Job Ownership

`src.research_handler.ResearchHandler` owns panel and chat-stream active research jobs: validation, query synthesis, model probing, endpoint/model selection inputs, task registry state, cancellation, progress, raw findings, result persistence, and owner stamping.

`routes/research_routes.py` owns the browser/API surface: auth and privileges, active/status/cancel/result/result-peek/stream routes, report HTML, hide/unhide images, library/detail/archive/delete, endpoint resolution for panel launch, and spinoff chat creation.

`TaskScheduler` owns scheduled research execution. It uses `DeepResearcher` directly, creates `[Research]` chat sessions, and writes `data/deep_research/*.json` in a compatible library/report shape without going through `ResearchHandler.start_research()`.

Agent tools and the CLI read and mutate persisted research JSON directly. They are separate policy surfaces and must not be assumed to inherit browser route owner gates.

## Research Runtime

`src.deep_research.DeepResearcher` owns multi-round research work:

- date/context setup;
- search provider selection and fallback through `src.search.providers` and `src.search.core`;
- URL/content fetching through `src.search.fetch_webpage_content`;
- source summarization/extraction;
- synthesis into final answers/reports;
- partial/fallback reports when extraction or synthesis fails.

Panel runtime behavior:

- reconnects to active jobs through `/api/research/active`;
- starts jobs through `/api/research/start`;
- streams progress over `/api/research/stream/{id}`;
- falls back to status polling when SSE is unavailable;
- reads non-destructive results through `/api/research/result-peek/{id}`;
- opens visual reports from persisted JSON.

Chat-stream runtime behavior:

- first vague research messages can ask clarifying questions and set `research_pending`;
- later messages synthesize a focused research query;
- prior persisted research can seed continuation;
- progress, sources, raw findings, and `research_done` are emitted as SSE events;
- `/api/research/result/{id}` is destructive for chat consumption and marks/clears consumed in-memory results.

Spinoff creates a new chat session from a saved report. It currently seeds the report text without sources.

## Reports And Persistence

Research persistence uses `data/deep_research/<session_id>.json`. Current JSON can include result/report text, raw report, sources, raw findings, stats, category, archived state, hidden images, owner, timestamps, and consumed state.

`src.visual_report` owns HTML report generation from markdown-like research output, heading/TOC processing, category styling, image injection, allowlist sanitization of untrusted rendered HTML, and client-side controls for hiding images and discussing reports.

`clear_result()` marks/clears in-memory state; it does not delete the on-disk report. Library/detail/report/archive/delete routes operate on persisted JSON.

## Frontend Panel

`static/js/research/panel.js` owns the research modal/panel UI, settings, provider controls, job cards, result rendering, destructive actions, progress display, and library counts.

`static/js/research/jobs.js` owns active-job adoption, SSE connection, polling fallback, cancel, and result-peek flow. `researchSynapse.js` owns the compact running-state indicator. Chat and library frontend modules own report buttons, discuss/spinoff entry points, and older library views.

## Degraded Runtime

- `/api/research*` is exempt from the app-level hard request timeout.
- `ResearchHandler.start_research()` applies `research_run_timeout_seconds`; `0` means unlimited and bounded settings protect accidental extremes. User-selected round count is threaded into `DeepResearcher`.
- Deep extraction has separate timeout and concurrency controls.
- Scheduled research currently uses its own fixed max-time behavior.
- Probe failures are formatted before long jobs start.
- Search provider failure records `_last_search_error` and degrades through provider chains or empty results.
- Fetch/extraction failures skip individual sources when possible.
- Synthesis/final-report failures should preserve gathered material where possible.
- Provider, search, fetch, or model offline states should become failed/degraded job state, not app crashes.

Native/Docker endpoint behavior is delegated to model endpoint registration and `src.endpoint_resolver`. Research does not guarantee useful output without a working model plus some usable search/fetch source path.

## Compatibility State

The active FastAPI app path uses `src.research_handler.ResearchHandler`.

`services/research/service.py` is a public wrapper around a duplicate `services.research.research_handler.ResearchHandler`. That services handler is older than the active `src` handler and lacks current owner stamping, raw findings, report helpers, configurable timeout behavior, and route options. Treat it as compatibility/cleanup surface, not canonical runtime truth.

Search compatibility also matters: `src.search.core`, `src.search.providers`, and `src.search.content` alias the service search path so old imports stay live without a second fetch implementation.

## Security Policy

Research routes require an authenticated user, and start routes require research privilege. Persisted report access and mutations should return 404 for cross-owner or null-owner JSON. Archive/delete/hide-image/unhide-image must preserve owner gates.

Endpoint secret policy:

- `/api/research/start` must use owner-scoped enabled endpoints before decrypted API keys/base URLs are passed to the handler;
- spinoff/follow-up endpoint selection still needs owner-scope hardening; current fallback paths can resolve without the route owner;
- token-authenticated behavior must preserve token owner/scope expectations before being treated as an API surface.

Research sources, fetched pages, summaries, generated reports, and saved research context are untrusted data when reused in chat or another model call. Fetched webpage content in `DeepResearcher` is wrapped with `untrusted_context_message("webpage", content)` before extraction; other reuse paths should keep the same user-role/metadata policy.

Visual reports render model/source-influenced Markdown into HTML with inline JavaScript and remote images. Markdown HTML is allowlist-sanitized; category-derived CSS/classes, links, and image URLs need continued policy coverage. Report HTML remains a security-sensitive rendering surface.

## Testing Coverage

Existing useful coverage includes deep-research runtime/degraded tests, handler/service tests, persisted route owner-scope tests, endpoint selection tests, auth regressions, visual report tests, query fallback tests, and CLI preview/store tests.

Coverage is still thin around live job route ownership, `/api/research/start` route behavior, SSE/result-peek/cancel edges, spinoff endpoint ownership, tool/CLI direct JSON access, remote-image policy, and frontend panel/jobs behavior.

## Current Gaps

- Consolidate, retire, or clearly deprecate `services/research/research_handler.py`.
- Decide whether direct JSON access by `manage_research` and `scripts/odysseus-research` must be owner-filtered like browser routes or is local/tool-only.
- Spinoff endpoint fallback needs owner-scoped endpoint regression coverage.
- Spinoff research context should use the shared untrusted-context role/metadata policy.
- Research search/fetch logic does not yet share a single result shape with chat prefetch and agent tools.
- Visual report remote image policy needs stronger regressions.
- Scheduled research persistence needs dedicated route/library/report visibility coverage.
- Frontend research jobs/panel/SSE fallback behavior lacks direct tests.
