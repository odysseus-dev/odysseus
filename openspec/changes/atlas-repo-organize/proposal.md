# Atlas Framework Repo Organization

## Why

The Odysseus repo root has accumulated session handoffs, transcripts, temp scripts, and junk artifacts alongside runtime-critical paths. Before pushing to GitHub, we need a non-destructive organization layer that keeps every folder the app needs to run while giving agent workflows a clear GOTCHA home.

## What Changes

- Add a new **`atlas/`** overlay directory shaped by the Atlas/GOTCHA framework (goals, tools manifests, context, hardprompts, args, scratch).
- Relocate **loose root clutter files only** (handoffs, transcripts, Graphy map, ssh debugging dumps, temp scripts) into `atlas/context/` and `atlas/.tmp/`.
- Add **index manifests** that point at existing folders (`docs/`, `research-orch/`, `session-review-*`, `prompts/`, `tools/`, `scripts/`, `mcp_servers/`) without moving them.
- Remove empty junk artifacts at root (`and`, `Odysseus`, accidental `~/`).
- Record exact post-organize size in `atlas/SIZE.md`.
- **No runtime moves**: `app.py`, `core/`, `routes/`, `src/`, `services/`, `static/`, `data/`, `docker/`, `tools/`, etc. stay in place.

## Capabilities

### New Capabilities

- `atlas-gotcha-overlay`: GOTCHA scaffold under `atlas/` with README, handbook pointer, manifests, and size reporting.
- `repo-root-hygiene`: Relocation of loose root session artifacts into `atlas/` and removal of empty junk paths.

### Modified Capabilities

<!-- No existing openspec/specs yet — first repo-layout change. -->

## Impact

- **Code**: None in FastAPI runtime paths.
- **Docs**: New `atlas/` tree; OpenSpec change `atlas-repo-organize`.
- **Git**: Renames/adds for loose markdown and temp files only; runtime directories unchanged.
- **Agents**: Skills referencing moved filenames may need INDEX lookups (mitigated by `atlas/context/INDEX.md`).
