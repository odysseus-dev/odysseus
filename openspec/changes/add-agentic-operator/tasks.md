# Agentic Operator — Tasks

> Status legend: [x] done · [~] done in working tree but glue left UNCOMMITTED
> per owner decision (shared files carried pre-existing WIP) · [ ] not started.
> Committed: 6 phase commits (2696268 → 561af67), operator files + tests only.
> The registrations/models/wiring below marked [~] are live and tested in the
> working tree but sit in shared files (app.py, core/database.py, src/tool_*,
> tools/clicky_worker_api.py) that were not committed.

## 1. Operator core (service + routes + health)

- [x] 1.1 Create `services/operator/__init__.py` with `OperatorService` facade and shared result envelope (`ok`, `capability`, `data`, `degraded`, `hint`)
- [x] 1.2 Implement per-sidecar health probes with 30 s cache (Screenpipe :3030, PixelRAG :30001, unified memory :40001, Clicky worker, CDP :9222)
- [x] 1.3 Create `routes/operator_routes.py` with `GET /api/operator/status` behind `require_authenticated_request`; [~] wire router in `app.py`
- [~] 1.4 Add `operator_audit` SQLite table (timestamp, action, target, session id) in `core/database.py`
- [x] 1.5 Tests: status route auth, health cache, degraded envelope shape (mocked sidecars)

## 2. Perception + recall (read-only tools)

- [x] 2.1 Implement `services/operator/perception.py` — Screenpipe query (latest frames, text query, `minutes` lookback, 8,000-char budget with truncation sentinel)
- [x] 2.2 Implement `services/operator/recall.py` — unified memory `:40001/query` client (reuse `clicky_integration/clicky_client.py` pattern), 120 s timeout → structured `timeout`/`no_index` results, direct PixelRAG fallback
- [~] 2.3 Register `screen_look` and `screen_recall` in `src/tool_schemas.py`, `src/tool_implementations.py`, `src/tool_execution.py`, `src/tool_index.py`
- [x] 2.4 Tests: OCR truncation at frame boundary, recall degraded states, tool retrieval from "what's on my screen" intent

## 3. SpecTracer integration

- [x] 3.1 Implement trace store (SQLite, 24 h / 50-trace retention purge) in `services/operator/tracer.py`
- [x] 3.2 Add `POST /api/operator/spec-trace` (API-token auth, 256 KB cap → 413, returns `trace_id`, accepts `bundle_version`)
- [~] 3.3 Register `spec_trace` tool (`latest`, `list`, `get` actions)
- [ ] 3.4 Spec_Tracer repo: add "Send to Odysseus" export (settings for URL + API token, POST bundle, clipboard fallback on failure) — DEFERRED (separate repo + Chrome Web Store release channel)
- [x] 3.5 Tests: ingest happy path, oversized rejection, retention purge, `latest` retrieval

## 4. Desktop action (Clicky bridge)

- [~] 4.1 Audit Clicky worker IPC; add pointer command channel (move/click/double_click/drag) — resolved D-Q1: worker had no pointer channel, added `POST /pointer` (user32) in `tools/clicky_worker_api.py`
- [x] 4.2 Implement `services/operator/desktop.py` — coordinate actions + `target_text` resolution from Screenpipe OCR boxes (unique match required; return candidates on ambiguity)
- [x] 4.3 Add audio actions (`speak`/`listen`) with mic-lease acquire/release; `mic_busy` when lease held (host-side recording reports `unsupported_action` until WPF integration lands)
- [x] 4.4 Implement per-session consent gate (held action → `ask_user` approval → execute) + audit-log writes
- [~] 4.5 Register `desktop_act` tool; `clicky_offline` degraded result with launch hint
- [x] 4.6 Tests: consent flow, ambiguity refusal, mic-lease busy, offline degradation

## 5. Browser action (CDP harness)

- [x] 5.1 Implement `services/operator/browser.py` + `services/operator/cdp.py` — dependency-free CDP client (`/json/list` targets + per-target websocket): `tabs`, `navigate`, `snapshot`, `click`, `type`, `evaluate`
- [x] 5.2 Apply consent gate to mutating actions only; read-only `tabs`/`snapshot` ungated; audit-log all executed actions
- [~] 5.3 Register `browser_act` tool; `cdp_unreachable` degraded result with `--remote-debugging-port=9222` hint
- [x] 5.4 Tests: CDP round-trips (real loopback websocket), consent gating split (read vs mutate), unreachable degradation

## 6. Research fan-out

- [~] 6.1 Add `perplexity` to `PROVIDER_INFO` + `perplexity_search` in `services/search/providers.py` + `_call_provider` dispatch in `services/search/core.py`; `PERPLEXITY_API_KEY` already in `.env.example`
- [x] 6.2 Implement `services/operator/research.py` — parallel fan-out (TinyFish + Perplexity + Firecrawl), skip unkeyed providers, per-provider errors isolated, URL-dedupe + rank merge, 20 s overall deadline with partial results
- [~] 6.3 Register `operator_research` tool with description steering to explicit research intents
- [x] 6.4 Tests: merge/dedupe, missing-key skip, single-provider timeout isolation, deadline partials

## 7. UI + config + docs

- [ ] 7.1 Operator status panel in `static/js/` (capability health, consent state, launch hints) — DEFERRED (frontend; backend `/api/operator/status` ready)
- [x] 7.2 `memory_stack.env` additions (operator block: `SCREENPIPE_PORT`, `OPERATOR_CDP_PORT`, char budget, recall timeout, trace retention); provider keys already present in `.env.example`
- [ ] 7.3 Update `Graphy.md` / docs with the operator architecture and sidecar matrix — DEFERRED
- [x] 7.4 Smoke: all-import alignment check (6 tools × schemas/tags/index/impls); 85 operator tests green; all-sidecars-down path returns degraded envelopes (covered by unit tests)

## Follow-ups (not blocking merge)

- [ ] Commit the working-tree glue (owner decision: currently left UNCOMMITTED — shared files hold unrelated pre-existing WIP): `app.py`, `core/database.py`, `src/tool_schemas.py`, `src/tool_execution.py`, `src/tool_index.py`, `src/tool_implementations.py`, `src/agent_tools/__init__.py`, `src/tool_parsing.py`, `tools/clicky_worker_api.py`
- [ ] SpecTracer extension export target (task 3.4)
- [ ] Operator status panel + docs (tasks 7.1, 7.3)
- [ ] Clicky WPF host-side audio recording (unblocks `desktop_act` listen)
