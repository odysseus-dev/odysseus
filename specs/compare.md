# Compare

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers model A/B comparison behavior in:

- `routes/compare_routes.py`;
- `routes/session_routes.py`;
- `routes/chat_routes.py` and `routes/chat_helpers.py`;
- `routes/model_routes.py`;
- `routes/search_routes.py`;
- `core/database.py` model `Comparison`;
- `src/llm_core.py` and `src/endpoint_resolver.py`;
- frontend modules under `static/js/compare/`;
- `static/js/chat.js`, `static/js/sessions.js`, `static/js/models.js`, and `static/js/slashCommands.js`;
- `tests/test_compare_*` and focused blind-compare redaction tests.

## Runtime Behavior

The active text compare UI creates ordinary `[CMP]` sessions through `/api/session`, then streams each pane through `/api/chat_stream` with `compare_mode=true`. Search compare is a separate branch: it can query `/api/search/query` directly and its synthesis sessions use ordinary chat streaming without `compare_mode=true`. `static/js/compare/index.js` owns compare orchestration, session creation, execution order, search-mode branching, and export actions. `static/js/compare/panes.js` owns pane add/remove/swap/reroll lifecycle. `static/js/compare/stream.js` owns pane streaming and event rendering.

`routes/compare_routes.py` owns the `/api/compare` HTTP surface for alternate/legacy start/vote/history/delete behavior and the active `/api/compare/record` vote-summary endpoint. Legacy `/api/compare/start` now uses neutral helper-session names and withholds model identities/mapping from the start response while blind mode is active. It does not own provider-specific payload behavior.

Current call sites include:

- `/api/session` compare session creation and cleanup in compare frontend modules;
- `/api/chat_stream` pane execution through chat routes and detached stream infrastructure;
- `/api/models` and probe routes for model/endpoint selection;
- search-provider compare mode through `routes/search_routes.py`;
- `/api/compare/record` as a fire-and-forget backend vote summary, while active scoreboard state is localStorage-backed.

`Comparison` rows currently persist vote/history metadata: prompt, first model identifiers, winner, blind flag, optional N-model JSON in `blind_mapping`, vote timestamp, and owner. Response and metric columns exist in the schema but are not populated by the active compare UI flow. Compare history must be owner-scoped.

Frontend compare behavior is split by responsibility:

- `state.js` owns local compare state;
- `selector.js`, `models.js`, and `probe.js` own endpoint/model selection and probe UI;
- `panes.js` and `stream.js` own paired response rendering;
- `vote.js` and `scoreboard.js` own voting and history display.

## Ownership Boundaries

Compare owns paired evaluation flow and pane state. Chat routes own the actual stream execution path for compare panes. LLM provider code owns model-call mechanics. Session/model routes own endpoint-id resolution, owner-filtered endpoint/model visibility, header copying, and deleted-endpoint failures.

`compare_mode` in chat strips compare-breaking tools, disables document tools for `[CMP]` sessions, skips some research clarification, and suppresses memory, skill, and webhook side effects after pane responses.

Compare frontend code is part of the app DOM security surface. Current stream/search rendering sanitizes probe labels and tool labels, constrains search-result links to HTTP(S), uses safe generated-image display sources, and opens compare export/image popups with opener isolation.

## Policy Notes

- Current blind compare is UI/API masking until vote/reveal, not a full confidentiality boundary. `[CMP]` session names and session-list model fields are redacted for helper sessions, and legacy `/api/compare/start` withholds model identity/mapping while blind. Client-side selected model state and privileged/local inspection can still expose identity.
- Compare endpoint lists use owner filtering so users see only shared or owned endpoints.
- Non-admin compare session creation must use registered owner-visible endpoints; compare must not allow arbitrary raw endpoint URLs to bypass session-route endpoint policy.
- Prefetched search, URL, RAG, and research context entering compare panes must use the untrusted-context wrapper.
- If endpoint fallback behavior changes, verify each pane still goes to the intended model and that fallback notices/errors are visible in compare panes.

## Degraded And Compatibility Behavior

- Missing/offline endpoints are surfaced by model/session routes; chat can clear orphaned endpoint references and recover empty models when possible.
- Compare streams inherit chat's pre-output-only model fallback and provider-normalized SSE events, but compare frontend handling for `event: error` and `type: "fallback"` is thinner than chat's stream path.
- Shared legacy `ModelEndpoint.owner == NULL` rows remain visible through owner filters. Legacy `Comparison.owner == NULL` rows are not treated as shared for authenticated vote/delete/history flows.
- `/api/compare/start` and `/{comp_id}/vote` remain implemented but are not the active frontend path.

## Current Gaps

- Blind mode is not a confidentiality boundary; client/local state can still expose model identity before vote.
- `/api/compare/start` accepts raw endpoint URLs and can diverge from `/api/session` endpoint-owner/raw-endpoint policy.
- `src/agent_loop.py` advertises stale compare app API endpoints.
- Compare streaming and chat streaming are separate frontend paths but share model/provider infrastructure; regressions can happen when provider event shape changes.
- Compare frontend needs explicit fallback/error event handling parity with chat streaming.
- Compare tests cover endpoint owner helper behavior, blind compare redaction, and portable JS helpers, but not full active `/api/session` pane creation, frontend pane lifecycle, or SSE fallback/error handling.
