# Agentic Operator — Tasks

## 1. Operator core (service + routes + health)

- [ ] 1.1 Create `services/operator/__init__.py` with `OperatorService` facade and shared result envelope (`ok`, `capability`, `data`, `degraded`, `hint`)
- [ ] 1.2 Implement per-sidecar health probes with 30 s cache (Screenpipe :3030, PixelRAG :30001, unified memory :40001, Clicky worker, CDP :9222)
- [ ] 1.3 Create `routes/operator_routes.py` with `GET /api/operator/status` behind `require_authenticated_request`; wire router in `app.py`
- [ ] 1.4 Add `operator_audit` SQLite table (timestamp, action, target, session id) in `core/database.py`
- [ ] 1.5 Tests: status route auth, health cache, degraded envelope shape (mocked sidecars)

## 2. Perception + recall (read-only tools)

- [ ] 2.1 Implement `services/operator/perception.py` — Screenpipe query (latest frames, text query, `minutes` lookback, 8,000-char budget with truncation sentinel)
- [ ] 2.2 Implement `services/operator/recall.py` — unified memory `:40001/query` client (reuse `clicky_integration/clicky_client.py` pattern), 120 s timeout → structured `timeout`/`no_index` results, direct PixelRAG fallback
- [ ] 2.3 Register `screen_look` and `screen_recall` in `src/tool_schemas.py`, `src/tool_implementations.py`, `src/tool_execution.py`, `src/tool_index.py`
- [ ] 2.4 Tests: OCR truncation at frame boundary, recall degraded states, tool retrieval from "what's on my screen" intent

## 3. SpecTracer integration

- [ ] 3.1 Implement trace store (SQLite, 24 h / 50-trace retention purge) in `services/operator/tracer.py`
- [ ] 3.2 Add `POST /api/operator/spec-trace` (API-token auth, 256 KB cap → 413, returns `trace_id`, accepts `bundle_version`)
- [ ] 3.3 Register `spec_trace` tool (`latest`, `list`, `get` actions)
- [ ] 3.4 Spec_Tracer repo: add "Send to Odysseus" export (settings for URL + API token, POST bundle, clipboard fallback on failure)
- [ ] 3.5 Tests: ingest happy path, oversized rejection, retention purge, `latest` retrieval

## 4. Desktop action (Clicky bridge)

- [ ] 4.1 Audit Clicky worker IPC; add pointer command channel if absent (move/click/double_click/drag) — resolve open question D-Q1
- [ ] 4.2 Implement `services/operator/desktop.py` — coordinate actions + `target_text` resolution from Screenpipe OCR boxes (unique match required; return candidates on ambiguity)
- [ ] 4.3 Add audio actions (`speak`/`listen`) with mic-lease acquire/release; `mic_busy` when lease held
- [ ] 4.4 Implement per-session consent gate (held action → `ask_user` approval → execute) + audit-log writes
- [ ] 4.5 Register `desktop_act` tool; `clicky_offline` degraded result with launch hint
- [ ] 4.6 Tests: consent flow, ambiguity refusal, mic-lease busy, offline degradation

## 5. Browser action (CDP harness)

- [ ] 5.1 Implement `services/operator/browser.py` — CDP client (`/json` target list + per-target websocket): `tabs`, `navigate`, `snapshot`, `click`, `type`, `evaluate`
- [ ] 5.2 Apply consent gate to mutating actions only; read-only `tabs`/`snapshot` ungated; audit-log all executed actions
- [ ] 5.3 Register `browser_act` tool; `cdp_unreachable` degraded result with `--remote-debugging-port=9222` hint
- [ ] 5.4 Tests: mocked-CDP action round-trips, consent gating split (read vs mutate), unreachable degradation

## 6. Research fan-out

- [ ] 6.1 Add `perplexity` to `PROVIDER_INFO` + provider implementation in `services/search/providers.py`; `PERPLEXITY_API_KEY` in settings/admin UI
- [ ] 6.2 Implement `services/operator/research.py` — parallel fan-out (TinyFish + Perplexity + Firecrawl), skip unkeyed providers, per-provider errors isolated, URL-dedupe + rank merge, 20 s overall deadline with partial results
- [ ] 6.3 Register `operator_research` tool with description steering to explicit research intents
- [ ] 6.4 Tests: merge/dedupe, missing-key skip, single-provider timeout isolation, deadline partials

## 7. UI + config + docs

- [ ] 7.1 Operator status panel in `static/js/` (capability health, consent state, launch hints)
- [ ] 7.2 `memory_stack.env` / `.env.example` additions (ports, toggles, `PERPLEXITY_API_KEY`)
- [ ] 7.3 Update `Graphy.md` / docs with the operator architecture and sidecar matrix
- [ ] 7.4 End-to-end smoke: all sidecars up → each tool exercised once; all sidecars down → every tool returns a degraded envelope
