# Research

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers deep research behavior in:

- app wiring and timeout policy in `app.py` and `src/app_initializer.py`;
- canonical browser/API routes in `routes/research/research_routes.py`, with `routes/research_routes.py` as a compatibility shim;
- chat-triggered research in `routes/chat_routes.py`;
- diagnostics in `routes/diagnostics_routes.py`;
- scheduled research in canonical `routes/task/task_routes.py`, its top-level compatibility shim, and `src/task_scheduler.py`;
- active runtime code in `src/research_handler.py`, `src/deep_research.py`, `src/research_utils.py`, and `src/visual_report.py`;
- search/fetch dependencies in `src.search`, `services.search`, and the `src.search.content` compatibility alias;
- compatibility/public service code in `services/research/research_handler.py` and `services/research/service.py`;
- agent tools in `src/tool_implementations.py`, `src/tool_execution.py`, and `src/tool_index.py`;
- research CLI access in `scripts/odysseus-research`;
- frontend modules `static/js/research/panel.js`, `static/js/research/jobs.js`, `static/js/researchSynapse.js`, `static/js/chat.js`, `static/js/chatRenderer.js`, `static/js/chatStream.js`, `static/js/documentLibrary.js`, `static/js/sessions.js`, and compare stream research UI;
- persisted reports under `data/deep_research/*.json`;
- tests under `tests/test_research_*`, `tests/test_deep_research_*`, `tests/test_visual_report*.py`, `tests/test_services_research_low_quality_sources.py`, `tests/test_svc_research_sources_nondict.py`, research auth regressions, endpoint fallback tests, and research CLI tests.

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

`src.research_handler.ResearchHandler` owns panel and chat-stream active research jobs: validation, query synthesis, model probing, endpoint/model selection inputs, task registry state, cancellation, progress, raw findings, result persistence, average-duration caching, owner stamping, and owner rename for active/disk-backed task state.

`routes.research.research_routes` owns the browser/API surface: auth and privileges, active/status/cancel/result/result-peek/stream routes, report HTML, hide/unhide images, library/detail/archive/delete, endpoint resolution for panel launch, and spinoff chat creation. Top-level `routes.research_routes` is a `sys.modules` compatibility shim.

Internal-tool owner forwarding rejects only request sentinel identities. The reserved Default/Local storage owner is allowed to own research state in explicit no-login storage flows, while named-user lookups and route gates remain authoritative in configured auth mode.

`TaskScheduler` owns scheduled research execution. It uses `DeepResearcher` directly, creates `[Research]` chat sessions, and writes `data/deep_research/*.json` in a compatible library/report shape without going through `ResearchHandler.start_research()`.

The built-in `tidy_research` action removes only empty or unparseable report JSON. Because those broken files have no readable owner stamp, `src.builtin_actions` refuses the sweep unless the stored task owner is an admin or the app is in explicit auth-disabled single-user mode; refusal happens before file enumeration.

Agent tools and the CLI read and mutate persisted research JSON directly. They are separate policy surfaces and must not be assumed to inherit browser route owner gates.

## Research Runtime

`src.deep_research.DeepResearcher` owns multi-round research work:

- date/context setup;
- search provider selection and fallback through `src.search.providers` and `src.search.core`;
- URL/content fetching through `src.search.fetch_webpage_content`;
- separate tracking of analyzed URLs, last search errors, and empty-round limits;
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

Spinoff/Discuss creates a new chat session from a saved report. It seeds the report text as a system primer with `research_spinoff_from` metadata, uses the source session owner/endpoint context where available, disables RAG by default for the new session, and keeps source details out of the chat context to avoid fabricated citations.

## Reports And Persistence

Research persistence uses `data/deep_research/<session_id>.json`. Current JSON can include result/report text, raw report, sources, raw findings, stats, category, archived state, hidden images, owner, timestamps, and consumed state.

Route access to persisted report files is path-confined. Browser routes validate
session ids against `^[a-zA-Z0-9-]{1,128}$`, enumerate trusted `*.json` files
under the resolved research storage root, match by exact filename, reject
symlink/path escapes after `resolve().relative_to(root)`, and then perform owner
checks before detail/archive/delete/result-peek/spinoff reads or mutations.
Invalid ids return 400; missing or cross-owner reports return 404.

`src.visual_report` owns HTML report generation from markdown-like research output, heading/TOC processing, category styling, image injection, allowlist sanitization of untrusted rendered HTML, and client-side controls for hiding images and discussing reports.

Research library thumbnails prefer visible source/report images and Open Graph images, while avoiding obvious logos/icons and blocked/hidden images.

`clear_result()` marks/clears in-memory state; it does not delete the on-disk report. Library/detail/report/archive/delete routes operate on persisted JSON.

## Frontend Panel

`static/js/research/panel.js` owns the research modal/panel UI, settings, provider controls, job cards, result rendering, destructive actions, progress display, and library counts.

`static/js/research/jobs.js` owns active-job adoption, SSE connection, polling fallback, cancel, and result-peek flow. `researchSynapse.js` owns the compact running-state indicator. Chat and library frontend modules own report buttons, discuss/spinoff entry points, and older library views.

## Degraded Runtime

- `/api/research*` is exempt from the app-level hard request timeout.
- `ResearchHandler.start_research()` applies `research_run_timeout_seconds`; `0` means unlimited and bounded settings protect accidental extremes. User-selected round count is threaded into `DeepResearcher`; `max_rounds=0` means automatic mode capped by the route/handler rather than unbounded research.
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

`services/research/service.py` is a public wrapper around a duplicate `services.research.research_handler.ResearchHandler`. That services handler remains compatibility/cleanup surface rather than canonical runtime truth; check parity before assuming it has every active-route field or policy behavior.

Its source extraction skips non-dict finding rows so one malformed cached or
generated entry does not discard later valid URL/title/summary sources.

Search compatibility also matters: `src.search.core`, `src.search.providers`, and `src.search.content` alias the service search path so old imports stay live without a second fetch implementation.

## Security Policy

Research routes require an authenticated user, and start routes require research privilege. Persisted report access and mutations should return 404 for cross-owner or null-owner JSON. Archive/delete/hide-image/unhide-image must preserve owner gates.

Endpoint secret policy:

- `/api/research/start` must use owner-scoped enabled endpoints before decrypted API keys/base URLs are passed to the handler;
- endpoint/model selectors should resolve `ProviderAuthSession`-backed endpoints for the acting owner and filter non-chat/image-only models out of research model lists;
- spinoff/follow-up endpoint selection should keep using owner-scoped endpoint context when present;
- token-authenticated behavior must preserve token owner/scope expectations before being treated as an API surface.

Research sources, fetched pages, summaries, generated reports, and saved research context are untrusted data when reused in chat or another model call. Fetched webpage content in `DeepResearcher` is wrapped with `untrusted_context_message("webpage", content)` before extraction; other reuse paths should keep the same user-role/metadata policy.

Visual reports render model/source-influenced Markdown into HTML with inline JavaScript and remote images. Markdown HTML is allowlist-sanitized; category-derived CSS/classes, links, and image URLs need continued policy coverage. Report HTML remains a security-sensitive rendering surface.

## Testing Coverage

Existing useful coverage includes deep-research runtime/degraded tests, handler/service tests, persisted route owner-scope tests, endpoint selection tests, auth regressions, visual report tests, query fallback tests, and CLI preview/store tests.

Coverage is still thin around live job route ownership, `/api/research/start` route behavior, SSE/result-peek/cancel edges, spinoff endpoint ownership, tool/CLI direct JSON access, remote-image policy, and frontend panel/jobs behavior.

## Current Gaps

- Consolidate, retire, or clearly deprecate `services/research/research_handler.py`.
- Decide whether direct JSON access by `manage_research` and `scripts/odysseus-research` must be owner-filtered like browser routes or is local/tool-only.
- Spinoff endpoint fallback needs continued owner-scoped endpoint regression coverage.
- Spinoff research context is preserved during trimming through metadata, but the system-message primer still needs an explicit policy decision versus the shared untrusted-context role/metadata wrapper.
- Research search/fetch logic does not yet share a single result shape with chat prefetch and agent tools.
- Visual report remote image policy needs stronger regressions.
- Scheduled research persistence needs dedicated route/library/report visibility coverage.
- Frontend research jobs/panel/SSE fallback behavior lacks direct tests.
