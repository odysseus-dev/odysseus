# Agentic Operator — Design

## Context

Odysseus already operates the raw ingredients of a perception–action loop, but each lives in its own silo:

| Piece | State today | Endpoint |
|---|---|---|
| Screenpipe | Launched via `deploy/scripts/start-screenpipe.ps1`, screen-only OCR (`windows-native`), audio disabled by default | `:3030` |
| PixelRAG | Export → tiles → FAISS pipeline via `start_pixelrag_local.ps1`, served by `tools/pixelrag_serve_quiet` | `:30001` |
| Unified memory API | `tools/unified_memory_api.py` — merges PixelRAG visual + agent memory + MemPalace notes | `:40001/query` |
| Clicky | WPF overlay; Odysseus can only launch/status it (`services/clicky_launcher.py`, `routes/clicky_routes.py`); `clicky_integration/clicky_client.py` reads unified memory | worker port |
| SpecTracer | Separate repo `C:\Users\tylar\code\Spec_Tracer` — published Chrome extension capturing element context to clipboard | n/a |
| Search | `services/search/providers.py` has TinyFish + Firecrawl (+ Brave, Tavily, Serper, SearXNG); Perplexity only reachable via research pipeline/MCP | n/a |
| Browser control | None in Odysseus (only a Claude Code skill exists) | `:9222` (target) |

The agent loop (`src/agent_loop.py` + `src/tool_*` modules) selects tools via RAG (`src/tool_index.py`). Adding capabilities means: schema in `tool_schemas.py`, implementation in `tool_implementations.py`, dispatch in `tool_execution.py`, retrieval description in `tool_index.py`.

## Goals / Non-Goals

**Goals:**
- One orchestration layer (`services/operator/`) the agent calls for perceive / recall / trace / act / browse / research.
- Every capability optional and independently degradable — the agent must always get a structured answer, never an exception.
- Consent-gated, audited action surface (desktop + browser mutations).
- Reuse existing sidecars and providers; net-new code is glue, not engines.

**Non-Goals:**
- No autonomous multi-step computer-use planner in this change — the agent invokes discrete tools; long-horizon "operate my PC" flows come later.
- No Screenpipe audio capture (mic stays with Clicky/voice via mic lease).
- No headless browser fleet — CDP attaches to the user's running Chrome only.
- No rebuild of SpecTracer's picker UI — only an export target + ingest.
- No cloud relay: everything is localhost-only in this change.

## Decisions

### D1: Single operator service vs. per-capability services
**Chosen:** one `services/operator/` package with a module per capability (`perception.py`, `recall.py`, `tracer.py`, `desktop.py`, `browser.py`, `research.py`) behind a common `OperatorService` facade with a shared result envelope (`ok`, `capability`, `data`, `degraded`, `hint`).
**Why:** the agent-facing contract (health, degradation, consent) is identical across capabilities; one facade avoids six slightly-different error styles. Alternative — six independent services — rejected: duplicates health/consent/audit plumbing and multiplies route wiring.

### D2: Tool granularity — six tools, not one mega-tool
**Chosen:** `screen_look`, `screen_recall`, `spec_trace`, `desktop_act`, `browser_act`, `operator_research` as separate tools.
**Why:** RAG tool selection (`tool_index.py`) works on per-tool descriptions; one mega-tool with an `action` enum would always be retrieved (or never), and its schema would bloat every prompt. Separate tools also let consent gating target only the action tools. Alternative — a single `operator` tool — rejected for retrieval precision and schema size.

### D3: Recall goes through the unified memory API, not PixelRAG directly
**Chosen:** `screen_recall` calls `:40001/query` (reusing `tools/unified_memory_api.py` result shape with `visual_results` + `agent_memory_results` + `notes_results`).
**Why:** tile-metadata enrichment and cross-store correlation already live there; `clicky_integration/clicky_client.py` proves the client pattern. Direct PixelRAG `:30001/search` remains an internal fallback if the unified API is down but PixelRAG is up.

### D4: Desktop click targeting via OCR frame geometry
**Chosen:** `desktop_act` accepts either raw coordinates or a `target_text` resolved against the most recent `screen_look` frames (Screenpipe OCR boxes → screen coordinates), executed by the Clicky worker.
**Why:** pure-coordinate clicking is brittle for an LLM; text-anchored targeting matches how the model perceives the screen. Alternative — a vision-model grounding step — deferred (slower, heavier; can be added behind the same interface).

### D5: Browser harness = thin CDP client over websockets
**Chosen:** implement with `websockets`/`httpx` against Chrome's DevTools endpoint (`/json` + per-target websocket), no Playwright dependency.
**Why:** Playwright brings a large dependency and its own browser lifecycle; we only need attach/navigate/snapshot/click/type/evaluate on an existing Chrome. Alternative — Playwright — revisit if snapshot fidelity (a11y tree) proves too costly to hand-roll; the `browser_act` contract is implementation-agnostic so swapping later is non-breaking.

### D6: Consent model
**Chosen:** per-session consent flag stored on the chat session; first mutating `desktop_act`/`browser_act` triggers the existing `ask_user` flow; read-only actions (`tabs`, `snapshot`, all perception/recall/trace/research) never gate. All executed actions append to an audit log (SQLite table `operator_audit`).
**Why:** mirrors how users already approve things in-chat; session scope prevents stale grants. Alternative — global settings toggle only — rejected: too easy to forget it is on.

### D7: Perplexity lands as a provider, fan-out as a mode
**Chosen:** add `perplexity` to `PROVIDER_INFO` (normal single-provider path), and implement `operator_research` as a parallel fan-out (asyncio.gather with per-provider timeout inside an overall 20 s deadline) over TinyFish + Perplexity + Firecrawl, deduping by normalized URL and interleaving by provider rank.
**Why:** keeps the existing single-provider `web_search` contract untouched; fan-out is additive. Reuses `services/search` normalization and the analytics/error logger.

### D8: SpecTracer integration direction
**Chosen:** extension → Odysseus push (`POST /api/operator/spec-trace` with API token), stored in SQLite with retention (24 h / 50 traces), read via `spec_trace` tool. Extension change lives in the Spec_Tracer repo (export target + settings for URL/token), mirroring how Aether posts to Odysseus.
**Why:** push keeps Odysseus passive and works with the published-extension model; polling the extension is impossible. Clipboard fallback preserves the existing UX when Odysseus is down.

## Risks / Trade-offs

- [Sidecar sprawl — five processes must be up for full capability] → per-capability health cache + degraded results with launch-script hints; `GET /api/operator/status` gives one view; nothing hard-fails.
- [PixelRAG CPU embedding latency (minutes)] → 120 s tool timeout with structured `timeout` result; recall marked "slow" in status; future GPU path unaffected.
- [Desktop/browser actions are inherently dangerous] → consent per session, audit log, no action tools in `ALWAYS_AVAILABLE`, mutating actions require explicit approval before first use.
- [OCR-geometry click targeting misfires on ambiguous text] → require unique match else return candidates for the agent to disambiguate; never click a fuzzy match silently.
- [Hand-rolled CDP snapshot may be lower fidelity than Playwright] → contract is action-based, not implementation-based; swap-in later is invisible to specs (D5).
- [Fan-out burns three API quotas per call] → tool description steers it to explicit research intents; single `web_search` remains the default cheap path; skipped-provider logic avoids paying for unconfigured keys.
- [SpecTracer is a separate repo/release channel] → clipboard fallback means the integration degrades to today's UX, never worse; ingest endpoint is versioned (`bundle_version` field) to tolerate extension lag.

## Migration Plan

1. Ship Odysseus side first (service, routes, tools) — everything degrades gracefully with sidecars absent, so it is safe to merge dark.
2. Add `perplexity` provider + admin key field (independently useful).
3. Extend Clicky worker protocol for pointer/audio actions; version-check so old workers report `unsupported_action`.
4. Update SpecTracer extension with the export target; publish when ready — until then `spec_trace` reports "no traces yet".
5. Rollback: remove router include in `app.py` + tool registrations; sidecars and data are untouched.

## Open Questions

- Clicky worker's current IPC surface — does it already accept pointer commands, or does the WPF worker need a new command channel? (Determines task 4 scope.)
- Perplexity API tier to target (`sonar` vs `sonar-pro`) for the provider default.
- Should `operator_research` results feed the existing research report renderer (`src/visual_report.py`) or stay chat-inline only in v1?
- Trace ingest auth: reuse existing API-token scheme as-is, or mint a scoped token type for the extension?
