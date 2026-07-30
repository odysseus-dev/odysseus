# Memory Graph View — TODO

Live checklist. Update as work proceeds. See `docs/handoff.md` for the exact next action and `docs/progress.md` for the session-by-session log.

## Milestone 1 — Backend API (DONE, committed)

- [x] `src/memory_graph.py` — pure edge-derivation logic (similarity / session / manual)
- [x] `routes/memory/memory_graph_routes.py` — `GET /api/memory/graph`, `GET /api/memory/graph/{id}/neighbors`, `POST /api/memory/{id}/links`, `DELETE /api/memory/{id}/links/{target_id}`
- [x] Mounted in `app.py` before `memory_router` (route-ordering fix, regression-tested)
- [x] 27 tests (`test_memory_graph_edges.py`, `test_memory_graph_routes.py`, `test_memory_graph_route_ordering.py`) — all passing
- [x] Full-suite diff vs clean `dev` baseline — zero new failures
- [x] 3 commits + 1 docs commit made

## Milestone 2 — Frontend (DONE, committed, visually verified)

- [x] Vendored `static/lib/cytoscape.min.js` (3.34.0, MIT, via `npm pack` — not hand-fetched from a guessed URL)
- [x] `static/js/memoryGraph.js` — full module: modal shell, lazy Cytoscape load, fetch + client-side filter/search, node/edge styling incl. dark/light theme via CSS custom properties, node selection + neighborhood highlighting, link-mode manual relationship editing, detail panel (pin/edit/delete/link management), demo-data fallback
- [x] `static/index.html` — new "Memory Graph" nav item in the sidebar Tools section
- [x] `static/app.js` — module import, click-handler wiring (mirrors `tool-calendar-btn` pattern), Customize-UI visibility map entry, `/memory-graph` deep-link route entry
- [x] `static/js/init.js` — privilege gating (`hideOn('#tool-memory-graph-btn', privs.can_manage_memory)`, mirrors the Brain button)
- [x] `static/style.css` — new `.memory-graph-*` block appended at end of file
- [x] `node --check` passes on all three touched/added JS files
- [x] Live browser verification against a real seeded dataset: render, node click + neighborhood highlight, detail panel, inline edit, search, category filters, similarity slider, link-mode create, theme switch. Found and fixed two bugs in the process (detail panel permanently `hidden`; search leaving stale selection-dim opacity over matches) — see `docs/progress.md` Session 4.
- [x] Found and fixed a real Milestone-1 backend bug while seeding real data: `memory_graph_routes.py`'s graph/links endpoints used `require_user()` (returns `""`) instead of the rest of the memory routes' `get_current_user()`-based `_owner()` (returns `None`), which meant the graph was always empty and links always 404'd in single-user/no-auth mode. Fixed and committed separately from the M2 frontend commit.
- [x] Seeded memories cleaned up: all 7 deleted via the real `DELETE /api/memory/{id}` endpoint, confirmed zero orphaned vectors left in the shared Chroma `odysseus_memories_fastembed` collection, demo server process killed (confirmed real PID via `Get-NetTCPConnection`, not the bash job id).
- [x] Commit M2 work — 4 commits: pre-existing `agent_loop.py` fix (standalone), the M1 owner-scoping fix (standalone), the M2 frontend (module + vendored lib + wiring + CSS), this docs update.
- [ ] **NOT DONE**: explicit on/off feature-flag setting. Right now the nav item is always visible to anyone with `can_manage_memory` (same gating as the existing Brain button) and the UI carries a static "(beta)" label — there is no separate toggle a user/admin can flip to hide the feature independent of that privilege. The user's instruction said "Feature flag enabled (beta)"; this was interpreted as "ship visible, labeled beta" rather than "build a togglable flag primitive." **Needs confirmation** — see Open Questions in `docs/memory-graph-design.md`.
- [ ] No automated frontend tests written yet for `memoryGraph.js` (pure-logic pieces like category-color resolution, filter logic, demo-graph shape are candidates for a `.test.mjs` file per the repo's `node --test` convention — see `docs/memory-graph-design.md` §10).

## Milestone 3 — Polish (NOT STARTED)

- [ ] Legend refinement / collapsible legend
- [ ] "Isolate connected component" affordance beyond the existing tap-to-highlight-neighborhood
- [ ] Keyboard shortcuts (e.g. `f` to focus search, arrow-key node navigation)
- [ ] `MODULE_SUMMARY.md` update to document the new `memoryGraph.js` module
- [ ] Final full regression pass (backend + a real frontend smoke test once Playwright/browser access is available)
- [ ] Decide whether to keep, adjust, or revert the incidental `src/agent_loop.py` fix (see Risks in `docs/handoff.md`)

## Known pre-existing issues (not introduced by this feature, do not "fix" without separate sign-off)

- `src/agent_loop.py` was missing `from typing import Any` — a hard `NameError` at import time that blocks `app.py` (and therefore the whole server) from starting under Python 3.11/3.14 in this sandbox. Confirmed identical on a clean `dev` checkout via `git stash`. **Patched locally** (one-line import fix, uncommitted) purely so the app could be launched for the M2 demo. Flagged for the user's explicit decision in `docs/handoff.md` — do not assume it should ship as part of this feature branch.
- `mcp_servers/rag_server.py` (and a few tests importing it) hit `AttributeError: 'Server' object has no attribute 'list_tools'` — an `mcp` package version mismatch in this sandbox's venv vs whatever version the repo's real environment pins. Not touched, not in scope.
- Several JS-logic tests (`node --test`-backed) and shell/path-confinement tests fail in this sandbox because Node.js version/`rg` binary/Windows path handling differ from the repo's real CI environment. Confirmed identical failure set on baseline `dev` — not caused by this feature.
