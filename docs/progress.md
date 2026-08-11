# Memory Graph View — Progress Log

Session-by-session record. Newest entry on top. See `docs/todos.md` for the live checklist and `docs/handoff.md` for the exact next action.

---

## Session 5 — Milestone 3 (polish), committed and visually verified

**Open question resolved first**: asked the user whether "feature flag enabled (beta)" needed a real togglable switch or whether the shipped privilege-gate + static "beta" label was sufficient — confirmed the latter. No code change from this; just closes the open item from Sessions 3/4.

### What was done

1. **Collapsible legend**: restructured the legend markup into a clickable header (`Legend ▾`) plus a body of the existing three legend rows; a `.collapsed` class (toggled on click, wired once in `_wireToolbar()`) hides the body and rotates the caret. `.memory-graph-legend`'s `pointer-events: none` had to be dropped (it predated the legend having anything interactive in it) — the legend DOM node isn't recreated by `_renderGraph()`, so collapse state naturally persists for the life of the modal without extra JS state.
2. **Isolate-component affordance**: added a pure `_componentNodeIds(graph, rootId)` (undirected BFS, no DOM/Cytoscape dependency — deliberately factored out this way so it's unit-testable) and wired it into `_applyFilters()` alongside the existing category filter (`categoryOk && isolateOk`). A new "Isolate"/"Show all" toggle button in the detail panel (`_toggleIsolate`) sets/clears `_isolateRootId`; a banner (`#memory-graph-isolate-banner`, reusing the existing `.memory-graph-demo-banner` CSS class since the two can never be visible at once — isolate is unreachable in demo mode, same as every other detail-panel action) reports "Isolated — showing N connected memories". Clears automatically on background click (`_clearSelection`) and on every graph reload (`_renderGraph`), so it never survives a mutation or a stale state across reopens.
3. **Keyboard shortcuts**: renamed the modal's `_escHandler` to `_keyHandler` (it now does more than Escape) and extended it: `f` (no modifiers) focuses the search box; `ArrowRight`/`ArrowDown` and `ArrowLeft`/`ArrowUp` cycle the selection through `_visibleNodeIds()` (nodes whose Cytoscape `display` style isn't `none` — so navigation automatically respects whatever category/similarity/isolate filters are active) and re-center the camera on the new selection. Both guarded: skipped entirely while typing in any input/textarea (so they don't hijack normal typing in the search box or the edit textarea), and skipped entirely while the modal is minimized (a latent gap the old Escape-only handler already had — Escape would still fire over a hidden modal — fixed as a byproduct of touching this function anyway, not a new regression).
4. **`MODULE_SUMMARY.md`**: added the `memoryGraph.js` row to §6 (Knowledge, Memory, and RAG), describing the module and its M1/M2/M3 feature set for future readers who haven't touched this feature before.
5. **Automated frontend tests**: `tests/memoryGraph/graphHarness.mjs` loads `static/js/memoryGraph.js` under Node via the exact `vm.createContext()` + import-string-shim pattern already established by `tests/markdown_codefence_placeholder_regression.mjs` (that script itself turned out to be an orphan — not wired into pytest or CI anywhere, only referenced as a documented pattern in `docs/memory-graph-analysis.md` — a pre-existing gap, not touched). `tests/memoryGraph/pureLogic.test.mjs` (10 `node:test` cases) covers: `_nodeSize` clamping, `_categoryColor` resolving every known category to a distinct color plus its fallback, `_buildQuery`'s exact query string, `_toElements` label truncation and — importantly — dropping edges whose endpoints aren't in the given node set (the same referential-integrity class of bug that would otherwise crash Cytoscape's element construction), `DEMO_GRAPH`'s own referential integrity and category validity, and `_componentNodeIds` (connected component, an isolated node with no edges, and a nonexistent root id). Wrapped by `tests/test_memory_graph_pure_logic_js.py`, mirroring `tests/test_streaming_segmenter_js.py`'s `node --test` subprocess + skip-if-no-node pattern — this is what actually gets picked up by `pytest -q`, unlike the orphaned codefence script.
6. **Live browser verification**: launched a second isolated instance (port 7011, separate scratch data dir), seeded 5 memories (2 near-duplicate identity nodes for a similarity edge, a manually-linked Lighthouse pair, one fully isolated Python-preference node), and verified: legend collapse/expand, `f` focusing the search box, arrow-key navigation selecting nodes and centering the camera, and the isolate toggle both ways (isolating down to the 2-node Lighthouse component with the correct banner count, then "Show all" correctly restoring the rest of the graph). Cleaned up identically to Session 4: all 5 memories deleted via the real API, confirmed zero orphaned vectors in the shared `odysseus_memories_fastembed` Chroma collection, server killed by verified `Get-NetTCPConnection` PID.
7. **Regression pass**: `node --check` clean on all touched JS. The 27 M1 backend tests + the new 10-case JS suite (as one pytest test) all pass — 28 total. `pytest --collect-only` across the whole suite shows exactly the same 5 pre-existing collection errors as documented before this session (the `mcp` package version mismatch across 4 files, plus one unrelated `UnicodeDecodeError` in a document-diff test) — zero new failures introduced.
8. Committed as 3 commits: the M3 feature code (legend + isolate + keyboard shortcuts + `MODULE_SUMMARY.md`), the new pure-logic test suite, and this docs update.

### Files changed this session

| File | Change |
|---|---|
| `static/js/memoryGraph.js` | collapsible legend, isolate-component affordance (`_componentNodeIds`, `_toggleIsolate`, `_renderIsolateBanner`), keyboard shortcuts (`_keyHandler` rename + `f`/arrow handling, `_visibleNodeIds`/`_navigateNodes`) |
| `static/style.css` | legend header/collapse rules, dropped `pointer-events: none` from `.memory-graph-legend` |
| `static/js/MODULE_SUMMARY.md` | new `memoryGraph.js` row |
| `tests/memoryGraph/graphHarness.mjs`, `tests/memoryGraph/pureLogic.test.mjs`, `tests/test_memory_graph_pure_logic_js.py` | new — pure-logic test suite |
| `docs/handoff.md`, `docs/progress.md`, `docs/todos.md` | updated to reflect M3 done/verified/committed |

### Push attempt

After M3 was committed, `git push -u origin feature/memory-graph-view` was attempted and failed:

```
remote: Permission to odysseus-dev/odysseus.git denied to yakamoz221.
fatal: unable to access '...': The requested URL returned error: 403
```

The `origin` remote is `https://github.com/odysseus-dev/odysseus.git`; the authenticated GitHub account (`yakamoz221`) doesn't have write access to it. Not something a coding session can fix on its own — needs the user to sort out repo permissions, point at a fork, or push via different credentials. The branch is fully committed locally (11 commits total on top of `dev`) and otherwise ready; see `docs/handoff.md` for the exact next step.

---

## Session 4 — Milestone 2 verification, two bugs found and fixed, M2 committed

**Goal**: pick up exactly where Session 3 stopped — get a real, visual confirmation the Memory Graph tab works, then commit M2.

### What was done

1. Launched an isolated instance (`ODYSSEUS_DATA_DIR`/`DATABASE_URL` in the session scratchpad, `AUTH_ENABLED=false`, `LOCALHOST_BYPASS=true`, `CHROMADB_PORT=8100`, `APP_PORT=7010`). ChromaDB was still reachable at `localhost:8100` as documented. FastEmbed model download completed this time (was cached from the aborted Session 3 attempt).
2. Seeded the exact 7-memory set from `docs/handoff.md`'s Session 3 plan via `POST /api/memory/add`, pinned one, and attempted the manual link between the two Lighthouse entries via `POST /api/memory/{id}/links`.
3. **Found bug 1 (backend, Milestone 1 code)**: the link call 404'd and `GET /api/memory/graph` returned zero nodes despite 7 memories existing. Root cause: `memory_graph_routes.py` used `require_user(request)` (returns `""` in single-user/no-auth/localhost-bypass mode) for owner resolution, while every route in `memory_routes.py` uses a local `_owner(request)` = `get_current_user(request)` (returns `None` in that same mode). `MemoryManager.load(owner=...)` and `_verify_memory_owner()` both special-case `None` as "no filter / bypass ownership check" — but `""` is not `None`, so `load(owner="")` filtered strictly against entries that never got `owner=""` stamped (since `add_entry` only sets the `owner` key when it's truthy). Fixed by switching the graph and links routes to the same `_owner()`/`get_current_user()` pattern already used everywhere else in the memory routes (added a local `_owner()` helper to `memory_graph_routes.py`, matching `memory_routes.py`'s). Verified via curl: graph then returned all 7 nodes plus the expected similarity edge (0.875, between the near-duplicate identity pair) and the manual link edge. Committed separately from the M2 frontend work, since it's a fix to already-committed M1 code, not new M2 code.
4. Restarted the server (data survives — only the process was killed) and re-verified the fix, then moved to browser testing.
5. Opened `/` in Chrome, clicked "Memory Graph" in the sidebar Tools list. Modal rendered correctly: 7 nodes, similarity edge, manual link edge, category chips, search box, min-match slider, Link mode button, legend.
6. **Found bug 2 (frontend)**: clicking a node correctly highlighted its neighborhood but no detail panel appeared. Traced to `static/js/memoryGraph.js`: the panel div (`#memory-graph-detail`) is created with a hardcoded `hidden` class in the initial modal template, and `_renderDetailPanel()` only ever set `panel.innerHTML` — nothing removed the `hidden` class on selection. Fixed with a one-line `panel.classList.remove('hidden')` at the top of `_renderDetailPanel()`. Re-verified: panel now shows category tag, text, uses/pinned meta, timestamp, and Unpin/Edit/Start-link/Delete buttons on node click.
7. **Found bug 3 (frontend)**: typing a search term didn't visually surface matches — matching nodes stayed at `opacity:0.08` because a prior node-selection's `.mg-dimmed` class (added by `_highlightNeighborhood`) was never cleared by `_applySearch()`, which only ever added/removed its own `.mg-search-match` class. Fixed by having `_applySearch()` clear `_selectedId` (resetting the detail panel) and strip `mg-highlighted`/`mg-dimmed` at the top of the function, so a fresh search cleanly supersedes any leftover selection state. Re-verified: searching "lighthouse" now shows all nodes at full opacity with the two matching nodes ringed.
8. Verified category chip filtering (isolating "identity" correctly showed only the 2 identity nodes), the min-match similarity slider (raising it above 0.875 correctly hid the identity-pair similarity edge), link-mode's two-click create flow end-to-end through the actual UI (new manual edge appeared after the automatic post-mutation reload, proving the bug-1 fix works from the UI path too, not just curl), the inline Edit textarea (Save/Cancel), and a theme switch (light theme via the Theme modal — all non-canvas chrome recolored live via CSS variables as expected; canvas node fill recompute-on-render-only limitation, already documented, was reconfirmed and left as a Milestone 3 item rather than fixed now).
9. **Cleanup**: deleted all 7 seeded memories via the real `DELETE /api/memory/{id}` endpoint. Queried the shared Chroma `odysseus_memories_fastembed` collection directly (`POST .../collections/{id}/get` with the 7 ids) and confirmed zero vectors remained — symmetric cleanup verified, not just assumed. Killed the server by resolving the actual listening PID via `Get-NetTCPConnection -LocalPort 7010 -State Listen` (not a guessed PID or shell job number — confirmed this matters: the venv's `python.exe` re-execs into a different on-disk interpreter path, so `Get-CimInstance ... -Filter "Name='python.exe'"` alone returns multiple candidates and only the actual socket owner via `Get-NetTCPConnection` disambiguates which one to kill).
10. Committed as 4 commits: the pre-existing `agent_loop.py` import fix (standalone, per Session 3's flag not to fold it silently into a feature commit), the M1 owner-scoping fix (standalone), the M2 frontend (module + vendored Cytoscape + HTML/app.js/init.js wiring + CSS, one commit), and this docs update.

### Files changed this session

| File | Change |
|---|---|
| `routes/memory/memory_graph_routes.py` | owner-resolution bug fix (bug 1) |
| `static/js/memoryGraph.js` | detail-panel visibility fix (bug 2) + search-highlight-clearing fix (bug 3) |
| `docs/handoff.md`, `docs/progress.md`, `docs/todos.md`, `docs/memory-graph-analysis.md`, `docs/memory-graph-design.md` | updated to reflect M2 done/verified/committed |

### Test / verification status

- `node --check` passes on `static/js/memoryGraph.js` after both fixes.
- Full manual browser verification pass completed (see step 8 above) — this is the first real visual confirmation the feature has had; Sessions 1–3 never got past syntax-checking the frontend.
- Backend test suite not re-run this session (only one backend file changed, a two-line owner-variable substitution with no new logic branches; the existing 27 M1 tests don't exercise the no-auth/localhost-bypass code path that the bug lived in, which is exactly how it shipped unnoticed — worth a Milestone 3 follow-up test case for that specific mode).

---

## Session 3 — Milestone 2 (Frontend), stopped mid-verification

**Status when stopped**: all M2 frontend code written and syntax-checked, but **not yet committed** and **not yet visually verified in a browser**. The user interrupted mid-launch to request a documentation-only checkpoint. No further development happened after that instruction — this entry and the other four requested doc files are the only changes made after the stop request.

### What was completed this session

1. Vendored Cytoscape.js 3.34.0 (MIT license) into `static/lib/cytoscape.min.js` via `npm pack cytoscape@3` in a scratch directory, then copied the UMD `dist/cytoscape.min.js` build — not fetched from a hand-typed/guessed URL, and not installed into the project's own `node_modules` (the app has no bundler; this follows the existing vendoring convention used for `xlsx.full.min.js`, `docx.umd.min.js`, etc.).
2. Wrote `static/js/memoryGraph.js` (new file, ~/600 lines) — see full breakdown in "Files changed" below.
3. Added a "Memory Graph" entry to the sidebar Tools section in `static/index.html`, placed directly after the existing "Brain" entry, using the same `.list-item` markup pattern as every other tool.
4. Wired it up in `static/app.js`:
   - New static import of `memoryGraph.js` next to the existing `memory.js` import.
   - New click handler block for `#tool-memory-graph-btn`, copied from the `#tool-calendar-btn` block's exact structure (`Modals.toggle(...)` check, falling back to the module's own `openMemoryGraph()`/`closeMemoryGraph()`).
   - New entry in the `UI_VIS_MAP` object (Customize UI panel) so the nav item can be hidden/shown like any other tool.
   - New `/memory-graph` entry in the `_routeOpen` deep-link map, mirroring the existing `/memory` entry.
5. Added one line to `static/js/init.js`: `hideOn('#tool-memory-graph-btn', privs.can_manage_memory)`, directly under the existing identical line for `#tool-memory-btn`, so the privilege gating stays consistent between the two entry points.
6. Appended a new `/* Memory Graph View (beta) */` CSS block to the end of `static/style.css` (~234 lines) — did not edit any existing rule.
7. Investigated and fixed a **pre-existing, unrelated** bug blocking `app.py` from importing at all: `src/agent_loop.py` used `dict[str, Any]` in a function annotation without ever importing `Any` from `typing`. Confirmed via `git stash` that this reproduces identically on a clean `dev` checkout — not something introduced by this feature. Patched with a one-line import fix so the app could actually be launched for the live demo. **This fix is currently uncommitted and its disposition needs the user's explicit decision** (see `docs/handoff.md` → Risks).
8. Started a local server to seed demo data and take a screenshot, using an isolated `ODYSSEUS_DATA_DIR` / `DATABASE_URL` (a scratch directory, not the repo's real `data/`) with `AUTH_ENABLED=false` and `LOCALHOST_BYPASS=true` for frictionless local testing, pointed at the machine's already-running local ChromaDB (`localhost:8100`) since a completely separate Chroma instance wasn't available. The process was still downloading/loading the FastEmbed embedding model (first-run cache warm-up) when the user's stop instruction arrived. **Killed immediately** (`taskkill` on the confirmed PID, verified via `Get-CimInstance Win32_Process` that it was the right process before killing). Nothing was ever seeded — no demo memory entries were created, no port was ever bound, and the isolated data directory (outside the repo, in the session scratchpad) can simply be discarded.

### Files changed this session (all uncommitted)

| File | Change |
|---|---|
| `static/lib/cytoscape.min.js` | new, vendored, 435 KB |
| `static/js/memoryGraph.js` | new, ~600 lines |
| `static/index.html` | +17 lines (nav item) |
| `static/app.js` | +16 lines (import, click handler, UI_VIS_MAP entry, route entry) |
| `static/js/init.js` | +1 line (privilege gate) |
| `static/style.css` | +234 lines (appended block only) |
| `src/agent_loop.py` | +1/-1 line (missing `Any` import — pre-existing bug, see Risks) |

### Test / verification status

- `node --check` passes on `static/js/memoryGraph.js`, `static/app.js`, `static/js/init.js` (matches the CI `node-syntax` job's check).
- **No live browser verification performed.** No screenshot exists. This is the single most important open item — see `docs/handoff.md`.
- No new automated tests written for the frontend module this session (not requested as part of M2's explicit requirement list, but flagged as a Milestone 3 item in `docs/todos.md`).
- Backend (Milestone 1) test status is unchanged from Session 2 (see below) — nothing in Session 3 touched backend code.

---

## Session 2 — Milestone 1 (Backend), completed and committed

### What was completed

- `src/memory_graph.py`: pure `build_graph()` / `build_similarity_edges()` / `build_session_edges()` / `build_manual_edges()` functions. Similarity edges use one nearest-neighbor query per node against `MemoryVectorStore` (never O(n²) pairwise). Manual edges read an additive `links` field on memory entries.
- `routes/memory/memory_graph_routes.py`: `GET /api/memory/graph` (owner-scoped, filterable by category/min_similarity/max_edges_per_node, with a node `limit` + `truncated` flag), `GET /api/memory/graph/{id}/neighbors` (lazy single-node expansion), `POST /api/memory/{id}/links` and `DELETE /api/memory/{id}/links/{target_id}` (manual relationship editing, gated by the existing `can_manage_memory` privilege).
- `app.py`: mounted the new router **before** `memory_routes.py`'s router, with an explanatory comment — `memory_routes.py`'s `GET/PUT/DELETE /api/memory/{memory_id}` is a single-segment wildcard that would otherwise swallow `GET /api/memory/graph` (Starlette matches routes in registration order, not by specificity, across the whole app).
- Three new test files: `tests/test_memory_graph_edges.py` (14 pure-logic unit tests), `tests/test_memory_graph_routes.py` (12 route/owner-isolation tests, following the repo's "call the endpoint function directly" convention), `tests/test_memory_graph_route_ordering.py` (2 tests using a real `TestClient` — the one place a full ASGI app was needed, specifically to prove the route-ordering fix actually works against real Starlette request matching, which the repo's usual direct-call test style can't verify).

### Test status

- All 27 new tests pass.
- Ran the full existing suite (`pytest -q --continue-on-collection-errors`) both on this branch and on a `git stash`-clean `dev` checkout in the same throwaway venv. Exact same 199 pre-existing failures/errors on both sides — confirmed via `comm -23`/`comm -13` diff that the sets are byte-for-byte identical. Zero regressions, zero newly-fixed tests (none expected).
- All pre-existing failures are sandbox/environment gaps (missing Node.js test runner artifacts for a couple of cases, missing `rg` binary, an `mcp` package version newer than the repo pins, Windows-specific path-confinement test assumptions) — none touch memory, graph, or app.py wiring code.

### Commits made

```
5d91d1a docs: add Memory Graph View repository analysis and design
f488a8d feat(memory): add pure edge-derivation logic for Memory Graph View
366bdbf feat(memory): add Memory Graph API — GET /api/memory/graph + manual links
```

---

## Session 1 — Analysis and design (Phase 0)

- Produced `docs/memory-graph-analysis.md` (19-section repo inventory) and `docs/memory-graph-design.md` (architecture, API, UI, performance, security, migration/rollback, testing strategy) purely from research — no code touched, no dependencies installed, no database changed, nothing committed at the time (the docs commit itself happened at the start of Session 2, once implementation was approved).
- User approved the design and requested implementation with: Cytoscape.js, a dedicated Memory Graph module, automatic semantic relationships, manual relationship editing, a beta feature flag, no breaking changes, small commits, Docker compatibility, passing tests, and incremental delivery with a build/test/preview/approval checkpoint after each milestone.
