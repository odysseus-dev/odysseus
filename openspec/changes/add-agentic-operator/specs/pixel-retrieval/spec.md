# pixel-retrieval — Spec Delta

## ADDED Requirements

### Requirement: Historical visual retrieval via PixelRAG
The system SHALL provide a `screen_recall` agent tool that performs semantic retrieval over indexed screen tiles by calling the unified memory API (default `http://localhost:40001/query`), returning tile matches with `tile_metadata` (window title, timestamp) when available.

#### Scenario: Recall when something appeared on screen
- **WHEN** the agent calls `screen_recall` with `query="stripe dashboard invoice"` and `k=5`
- **THEN** the tool returns up to 5 visual matches, each with tile id, similarity score, and window title/timestamp metadata when the tile metadata file resolves

### Requirement: Recall results include cross-store context
`screen_recall` SHALL surface the unified memory API's companion result lists (`agent_memory_results`, `notes_results`) alongside `visual_results` so the agent can correlate what was on screen with saved memories and notes in one call.

#### Scenario: Visual match with related note
- **WHEN** a recall query matches both a screen tile and a MemPalace note
- **THEN** the tool result contains the visual match and the note reference in their respective sections

### Requirement: Slow-embedding tolerance
Because CPU query embedding can be slow, `screen_recall` SHALL use a configurable timeout (default 120 seconds) and, on timeout, return a structured degraded result telling the agent the index is still available but slow — it MUST NOT surface a raw socket exception.

#### Scenario: Embedding timeout handled
- **WHEN** the unified memory API does not respond within the timeout
- **THEN** the tool returns `ok: false` with reason `timeout` and a hint to retry with a shorter query

### Requirement: Missing index is a degraded state, not an error
When PixelRAG has no FAISS index (screenshots never captured/indexed), `screen_recall` SHALL report the capability as degraded with the remediation hint `deploy/scripts/start_pixelrag_local.ps1`.

#### Scenario: Recall before first index build
- **WHEN** `screen_recall` is called and the PixelRAG serve reports no index
- **THEN** the tool returns `ok: false`, `reason: no_index`, and the launch-script hint
