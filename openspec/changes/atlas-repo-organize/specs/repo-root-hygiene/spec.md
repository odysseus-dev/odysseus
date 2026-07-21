# repo-root-hygiene — Spec Delta

## ADDED Requirements

### Requirement: Loose root clutter relocated
Loose session artifacts at the repository root (handoffs, transcripts, Graphy map, ssh debugging files, temp catalog scripts) SHALL be moved into `atlas/context/` or `atlas/.tmp/` and SHALL NOT remain at repo root after organization.

#### Scenario: Handoff file no longer at root
- **WHEN** organization completes
- **THEN** `odysseus-handoff.md` is absent from repo root and present under `atlas/context/` (or a dated subfolder)

### Requirement: Runtime paths unchanged
The following paths SHALL remain at their original repo-root locations after organization: `app.py`, `core/`, `routes/`, `src/`, `services/`, `static/`, `data/`, `logs/`, `config/`, `docker/`, `mcp_servers/`, `companion/`, root `tools/`, and Docker compose files.

#### Scenario: FastAPI entry intact
- **WHEN** organization completes
- **THEN** `app.py` exists at repo root and `python -c "import app"` succeeds from repo root

### Requirement: Empty junk artifacts removed
Empty junk files and directories at repo root (`and`, `Odysseus`, accidental `~/` when empty) SHALL be removed after organization.

#### Scenario: Junk files gone
- **WHEN** organization completes
- **THEN** `and` and `Odysseus` do not exist at repo root

### Requirement: Existing folders indexed not moved
Directories `docs/`, `research-orch/`, `session-review-*`, `prompts/`, and nested side-apps SHALL remain in place; organization SHALL reference them via `atlas/context/INDEX.md` without relocating them.

#### Scenario: Research outputs stay put
- **WHEN** organization completes
- **THEN** `research-orch/` remains at repo root and is listed in `atlas/context/INDEX.md`
