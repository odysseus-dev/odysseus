# Add Agentic Operator

## Why

Odysseus already runs the pieces of a perception–action loop — Clicky (mouse/audio overlay), Screenpipe (screen OCR), PixelRAG (visual retrieval), and a multi-provider search layer — but they are disconnected sidecars the agent cannot drive. The agent loop has no way to see the user's screen, grab DOM/dev context, act on the desktop or browser, or fan out research in one coherent workflow. This change unifies them into a single agentic capability ("Operator") so the agent can perceive → retrieve → act → research on the user's behalf.

## What Changes

- New **Operator orchestration service** (`services/operator/`) that brokers the six capabilities behind one internal API, with health/degradation handling for each sidecar (Screenpipe :3030, PixelRAG :30001, unified memory :40001, Clicky worker).
- New **agent tools** registered in `src/tool_schemas.py` / `src/tool_implementations.py` / `src/tool_index.py`: `screen_look` (live OCR), `screen_recall` (PixelRAG history), `spec_trace` (DOM/context grab), `desktop_act` (Clicky mouse/audio), `browser_act` (CDP harness), `operator_research` (search fan-out).
- New **SpecTracer integration**: the existing SpecTracer Chrome extension (`C:\Users\tylar\code\Spec_Tracer`, published on the Chrome Web Store) already captures element context (hierarchy, selector, classes, position, events) client-side to the clipboard. This change adds an Odysseus export target — the extension posts its context bundle to an Odysseus ingest endpoint so the agent can consume captures directly.
- New **browser harness service**: CDP connection to the user's running Chrome (navigate, snapshot, click, type, evaluate) — Odysseus-native, not dependent on Claude Code's skill.
- **Clicky bridge extended** from launch/status-only to action commands (pointer moves/clicks, audio prompts via mic-lease-aware TTS/STT).
- **Search layer extended**: add `perplexity` as a first-class provider in `services/search/providers.py` (TinyFish and Firecrawl already exist); add a fan-out mode that queries TinyFish + Perplexity + Firecrawl in parallel and merges ranked results.
- New **routes** (`routes/operator_routes.py`) for status, capability toggles, and spec-tracer ingest; wired in `app.py`.

## Capabilities

### New Capabilities
- `operator-core`: Orchestrator that exposes the perception/action/research tools to the agent loop, tracks sidecar health, and degrades gracefully when a capability is offline.
- `screen-perception`: Live and recent screen understanding via Screenpipe OCR (what is on screen now / in the last N minutes).
- `pixel-retrieval`: Historical visual retrieval over PixelRAG tiles through the unified memory API (find when/where something appeared on screen).
- `spec-tracer`: Ingest and agent access for SpecTracer extension captures (element pick in extension → structured context bundle → Odysseus → agent tool).
- `desktop-action`: Clicky-mediated mouse and audio actions with explicit user consent and mic-lease coordination.
- `browser-action`: CDP-based control of the user's Chrome for navigation, inspection, and interaction.
- `operator-research`: Parallel search fan-out across TinyFish, Perplexity, and Firecrawl with merged, deduplicated results.

### Modified Capabilities
<!-- No existing openspec/specs yet — this is the first change in the repo. -->

## Impact

- **Code**: `services/operator/` (new), `services/search/providers.py` (Perplexity provider), `routes/operator_routes.py` (new) + `app.py` wiring, `src/tool_schemas.py` / `src/tool_implementations.py` / `src/tool_execution.py` / `src/tool_index.py` (six new tools), `services/clicky_launcher.py` + `clicky_integration/` (action bridge), `static/js/` (operator status UI), SpecTracer repo (`C:\Users\tylar\code\Spec_Tracer` — add "Send to Odysseus" export alongside clipboard copy).
- **Sidecars**: Screenpipe (:3030), PixelRAG serve (:30001), unified memory API (:40001), Clicky worker — all optional; Operator must degrade per-capability, never hard-fail the agent loop.
- **Config**: `memory_stack.env` additions (ports, toggles), `PERPLEXITY_API_KEY` in settings/admin UI.
- **Security**: desktop/browser actions are consent-gated (per-session approval), all routes behind `require_authenticated_request`.
- **Tests**: pytest coverage for the operator service, each tool implementation (mocked sidecars), and search fan-out merge.
