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
- [x] Automated frontend tests for `memoryGraph.js` pure logic — see Milestone 3.

## Milestone 3 — Polish (DONE, committed)

- [x] "Feature flag enabled (beta)" question resolved: user confirmed the current privilege-gate + static "beta" label is sufficient; no separate togglable switch needed.
- [x] Collapsible legend — click "Legend" header to expand/collapse, caret rotates, state persists for the life of the modal.
- [x] "Isolate connected component" affordance — new "Isolate"/"Show all" button in the detail panel; runs an undirected BFS (`_componentNodeIds`) over the currently loaded graph and hides everything outside the selected node's component, with a banner showing the count. Clears on background click or graph reload.
- [x] Keyboard shortcuts: `f` focuses the search box; Arrow keys (Right/Down = next, Left/Up = previous) cycle selection through the currently *visible* nodes (respects active category/similarity/isolate filters) and re-center the camera. Guarded against firing while typing in any input/textarea or while the modal is minimized.
- [x] `MODULE_SUMMARY.md` updated with a `memoryGraph.js` row (§6, Knowledge/Memory/RAG).
- [x] Automated frontend tests: `tests/memoryGraph/pureLogic.test.mjs` (10 tests via `node --test`, wrapped by `tests/test_memory_graph_pure_logic_js.py` per the repo's `node:test` + pytest-shim convention) covering category-color resolution, API-response-to-Cytoscape-elements mapping (including dropping edges with dangling endpoints), the fetch query string, demo-graph referential integrity, and the isolate-component BFS.
- [x] Final regression pass: `node --check` clean on all touched JS; the 27 existing Milestone 1 backend tests + the new JS suite all pass; `pytest --collect-only` across the full suite shows the same 5 pre-existing collection errors as before this session (mcp package version mismatch + one unrelated `UnicodeDecodeError`), zero new failures.
- [x] `src/agent_loop.py` fix: kept, committed separately (see Session 4) — decided low-risk enough to ship without further debate.

## Known pre-existing issues (not introduced by this feature, out of scope to fix here)

- `mcp_servers/rag_server.py` (and a few tests importing it) hit `AttributeError: 'Server' object has no attribute 'list_tools'` — an `mcp` package version mismatch in this sandbox's venv vs whatever version the repo's real environment pins. Not touched, not in scope.
- Several JS-logic tests (`node --test`-backed) and shell/path-confinement tests fail in this sandbox because Node.js version/`rg` binary/Windows path handling differ from the repo's real CI environment. Confirmed identical failure set on baseline `dev` — not caused by this feature.
