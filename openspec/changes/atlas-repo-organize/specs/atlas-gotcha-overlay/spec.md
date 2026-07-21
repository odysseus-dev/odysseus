# atlas-gotcha-overlay — Spec Delta

## ADDED Requirements

### Requirement: Atlas GOTCHA overlay directory exists
The repository SHALL contain an `atlas/` directory with GOTCHA layers: `goals/`, `tools/`, `context/`, `hardprompts/`, `args/`, `.tmp/`, and `memory/`, plus a root `README.md` describing the overlay and pointing to the vendor handbook at `atlas_framework/atlas_framework/CLAUDE.md`.

#### Scenario: Agent discovers GOTCHA layout
- **WHEN** an agent reads `atlas/README.md`
- **THEN** it finds the GOTCHA layer map and a pointer to the canonical Atlas handbook

### Requirement: Tools layer is manifest-only
The `atlas/tools/` directory SHALL contain a `manifest.md` index of existing runtime tools under `tools/`, `scripts/`, and `mcp_servers/` without relocating executable scripts into `atlas/tools/`.

#### Scenario: Manifest indexes runtime tools
- **WHEN** an agent reads `atlas/tools/manifest.md`
- **THEN** it finds one-line descriptions and paths to scripts that remain in their original locations

### Requirement: Size reporting
The repository SHALL include `atlas/SIZE.md` documenting the measured byte size of the `atlas/` directory after organization.

#### Scenario: Size documented after organize
- **WHEN** organization completes
- **THEN** `atlas/SIZE.md` lists total bytes and a breakdown by top-level subfolder
