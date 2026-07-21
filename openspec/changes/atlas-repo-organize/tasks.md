# Atlas Repo Organization — Tasks

## 1. OpenSpec + scaffold

- [x] 1.1 Create OpenSpec change artifacts (proposal, design, specs, tasks)
- [x] 1.2 Create `atlas/{goals,tools,context,hardprompts,args,.tmp,memory}` directories
- [x] 1.3 Write `atlas/README.md` with GOTCHA map and handbook pointer
- [x] 1.4 Copy `build_app.md` into `atlas/goals/` from `atlas_framework`
- [x] 1.5 Write baseline `atlas/SIZE.md` after scaffold

## 2. Relocate loose root clutter

- [x] 2.1 Move handoffs/transcripts/maps into `atlas/context/`
- [x] 2.2 Move temp scripts and scrape dumps into `atlas/.tmp/`
- [x] 2.3 Remove empty junk (`and`, `Odysseus`, `~/` if empty)
- [x] 2.4 Update `atlas/SIZE.md` after moves

## 3. Manifests and indexes

- [x] 3.1 Write `atlas/tools/manifest.md` indexing `tools/`, `scripts/`, `mcp_servers/`
- [x] 3.2 Write `atlas/goals/manifest.md` indexing common workflows
- [x] 3.3 Write `atlas/context/INDEX.md` mapping `docs/`, `research-orch/`, `session-review-*`
- [x] 3.4 Write `atlas/hardprompts/INDEX.md` pointing at `prompts/`

## 4. Smoke test + pre-push audit

- [x] 4.1 Run path integrity, import, and compose smoke checks
- [x] 4.2 Run `openspec validate` / status for change completeness
- [x] 4.3 Git status audit for nested apps, data/, secrets before push
