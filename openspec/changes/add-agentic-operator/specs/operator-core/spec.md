# operator-core — Spec Delta

## ADDED Requirements

### Requirement: Operator service brokers all capability calls
The system SHALL provide an Operator service (`services/operator/`) that is the single entry point for screen-perception, pixel-retrieval, spec-tracer, desktop-action, browser-action, and operator-research calls made from the agent loop. Agent tool implementations MUST NOT call sidecar processes directly.

#### Scenario: Tool call routed through operator service
- **WHEN** the agent invokes any operator tool (e.g. `screen_look`)
- **THEN** the call is dispatched through the Operator service, which selects the backing sidecar and returns a normalized result envelope (`ok`, `capability`, `data`, `degraded`)

### Requirement: Per-capability health tracking
The Operator service SHALL track health for each sidecar (Screenpipe :3030, PixelRAG serve :30001, unified memory API :40001, Clicky worker, Chrome CDP endpoint) with a cached status refreshed at most every 30 seconds.

#### Scenario: Health snapshot exposed via API
- **WHEN** a client requests `GET /api/operator/status`
- **THEN** the response lists each capability with `available: true|false`, the probed endpoint, and the last probe timestamp

### Requirement: Graceful degradation per capability
The system SHALL degrade per-capability when a sidecar is offline: the corresponding tool returns a structured "capability unavailable" result with a remediation hint (e.g. the launch script path). An offline sidecar MUST NOT raise an unhandled exception in the agent loop or block other capabilities.

#### Scenario: Screenpipe offline, search still works
- **WHEN** Screenpipe is not running and the agent calls `screen_look` and then `operator_research`
- **THEN** `screen_look` returns `ok: false` with hint `deploy/scripts/start-screenpipe.ps1`, and `operator_research` completes normally

### Requirement: Operator routes require authentication
All Operator HTTP routes SHALL enforce `require_authenticated_request` before performing any action or returning status.

#### Scenario: Unauthenticated status request rejected
- **WHEN** a request without a valid session or API token calls `GET /api/operator/status`
- **THEN** the system responds with HTTP 401 and performs no sidecar probes

### Requirement: Operator tools registered in the tool index
The six operator tools SHALL be registered in `src/tool_schemas.py`, `src/tool_implementations.py`, and `src/tool_index.py` with retrieval descriptions so RAG-based tool selection can surface them from natural-language intents (e.g. "what's on my screen", "click the submit button", "when did I see that error").

#### Scenario: Intent retrieves the right tool
- **WHEN** the user asks "what am I looking at right now?" in agent mode
- **THEN** tool retrieval includes `screen_look` in the selected tool set
