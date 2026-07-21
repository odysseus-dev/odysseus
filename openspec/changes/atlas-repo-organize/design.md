# Atlas Repo Organization — Design

## Context

Odysseus is a FastAPI app with Docker bind mounts fixed to repo-root paths (`data/`, `static/`, `core/`, etc.). The repo also hosts agent tooling (`.cursor/`, `.agents/`, `integrations/`), research outputs (`research-orch/`, `session-review-*`), and vendor handbook material (`atlas_framework/`). Root clutter (~0.36 MB loose files) pollutes the workspace before GitHub push.

The Atlas/GOTCHA framework (`atlas_framework/atlas_framework/CLAUDE.md`) defines six agent-ops layers. This change applies GOTCHA as an **overlay**, not a restructure of runtime code.

## Goals / Non-Goals

**Goals:**

- Introduce `atlas/` as the organized agent-ops home.
- Move only loose root files into `atlas/context/` and `atlas/.tmp/`.
- Index existing folders via manifests without relocating them.
- Measure and document `atlas/` size in `SIZE.md`.
- Pass smoke checks (path integrity, import, compose, git diff sanity).

**Non-Goals:**

- Moving or renaming runtime directories (`tools/`, `data/`, `routes/`, etc.).
- Relocating `session-review-*`, `research-orch/`, `docs/`, or nested side-apps (`Aether/`, `Spec_Tracer/`, `my-worker/`).
- Rewiring Docker compose bind mounts or env contracts.
- Promoting Atlas memory Python package into production use (defer).

## Decisions

### D1: Overlay at `atlas/` vs root GOTCHA folders

**Chosen:** `atlas/{goals,tools,context,hardprompts,args,.tmp,memory}` at repo root.

**Why:** Avoids collision with existing root `tools/` (runtime scripts). Clear namespace for agent ops.

### D2: Vendor handbook stays in `atlas_framework/`

**Chosen:** `atlas/README.md` points to `atlas_framework/atlas_framework/CLAUDE.md` as canonical handbook; copy `build_app.md` into `atlas/goals/`.

**Why:** Single source of truth; no drift from duplicate CLAUDE.md bodies.

### D3: Manifest-only tools layer

**Chosen:** `atlas/tools/manifest.md` indexes `tools/`, `scripts/`, `mcp_servers/` — no script duplication.

**Why:** Runtime scripts must stay where imports and deploy scripts expect them.

### D4: Loose-file moves use `git mv` when tracked

**Chosen:** Prefer `git mv` for tracked files; filesystem move for untracked.

**Why:** Preserves history for files already in git.

## Risks / Trade-offs

- [Skills hardcode moved root paths] → `atlas/context/INDEX.md` maps old → new paths; grep before push.
- [Future organizer moves `tools/` or `data/`] → `atlas/README.md` banner: runtime folders frozen.
- [Nested side-apps staged accidentally] → pre-push git audit; gitignore if needed (Phase 4 backlog).
- [`atlas/` vs root `tools/` confusion] → Atlas tools dir is manifest-only; README states this explicitly.

## Migration Plan

1. Create OpenSpec artifacts and `atlas/` scaffold.
2. Move loose root clutter; delete empty junk.
3. Write manifests and INDEX files.
4. Re-measure size; run smoke checklist.
5. Git audit before GitHub push.

Rollback: reverse `git mv` paths; delete `atlas/` if needed (no runtime impact).

## Open Questions

- Whether to gitignore nested side-apps (`Aether/`, `Spec_Tracer/`, `my-worker/`) — deferred to pre-push audit outcome.
