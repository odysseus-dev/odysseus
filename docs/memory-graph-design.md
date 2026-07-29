# Memory Graph View — Design

Status: design only, not yet implemented. This proposal builds on the findings in `docs/memory-graph-analysis.md`. No application code, dependencies, or database state has been changed. Implementation should not begin until this design is reviewed and approved.

## Goals / non-goals

**Goals**: an interactive, pannable/zoomable node-link visualization of a user's own memories (Obsidian-graph-like), where nodes are memories and edges represent derived relationships (semantic similarity, shared session, shared category), reachable from the existing "Brain" memory modal, scoped strictly to the owning user.

**Non-goals for v1**: cross-user graphs, graphs spanning memories + documents + sessions in one view (a plausible v2 extension, not required now), real-time multi-tab collaborative editing of the graph, migrating the dormant SQL `memories` table into active use.

## 1. Architecture

The feature is additive and follows the existing "component bag" / manual-wiring conventions already used throughout the codebase — no new architectural pattern is introduced.

```
Browser (static/js/memoryGraph.js, new tab in #memory-modal)
        │  fetch('/api/memory/graph?...')
        ▼
FastAPI route: routes/memory/memory_graph_routes.py
  setup_memory_graph_routes(memory_manager, memory_vector, session_manager)
        │
        ▼
Service: src/memory_graph.py  (new, pure-ish module)
  build_graph(owner, filters) -> {nodes, edges, meta}
        │                              │
        ▼                              ▼
MemoryManager.load(owner=...)   MemoryVectorStore.search(...) per-node kNN via Chroma
(data/memory.json, existing)   (odysseus_memories collection, existing)
```

- **New backend module**: `routes/memory/memory_graph_routes.py`, mounted in `app.py` next to the existing `setup_memory_routes(...)` call, receiving the same already-constructed `memory_manager` and `memory_vector` singletons from `app_initializer.initialize_managers()`. No new singleton, no new startup step.
- **New service module**: `src/memory_graph.py` holds the graph-building logic (node assembly + edge derivation), kept separate from the route file so the edge-derivation logic is unit-testable without FastAPI in the loop (see §10).
- **New frontend module**: `static/js/memoryGraph.js`, imported from `static/app.js` alongside the existing `memory.js` import, following the same default-export-object + optional `window.*` exposure convention.
- **New tab**, not a new modal: added to the existing `.memory-tabs` strip in `index.html` (`Browse | Skills | Add | Settings | Graph`) and registered as a fifth tab panel inside `#memory-modal`. This reuses the modal's existing `Modals.register('memory-modal', ...)` entry, its drag/dock/minimize/z-order behavior, and its existing auth/ownership context — no new modal-manager registration needed. A standalone modal was considered and rejected for v1 (more registration/dock plumbing for no functional benefit; a tab is also the closer analogue to Obsidian's own "Graph view" living alongside the file browser).
- **Graph-rendering library**: recommend vendoring **Cytoscape.js** (single UMD file) into `static/lib/cytoscape.min.js`, following the existing vendoring convention used for `docx.umd.min.js`, `xlsx.full.min.js`, etc. Cytoscape is purpose-built for node-link graphs, ships a canvas renderer (needed for performance past a few hundred nodes — see §5), and bundles pan/zoom/drag interaction out of the box, so no additional libraries (separate zoom/drag/force packages) need to be vendored. D3 (force + zoom + drag composed manually) was considered as an alternative; Cytoscape was chosen because it is a single dependency rather than several composed low-level pieces, and its declarative style/selector API maps naturally onto category-based node styling. **This library choice is a recommendation, not a decision already made — flagging it explicitly for approval before implementation** (see Open Questions).

## 2. Database changes

**None required for v1.** Edges are computed on request from existing data (`data/memory.json` + the `odysseus_memories` Chroma collection); nothing new is persisted. This deliberately avoids two riskier paths identified in the analysis: touching the dormant SQL `memories` table (unused by the runtime today, so "fixing" it is an unrelated, larger migration) and adding a hand-rolled SQLite migration for a brand-new table.

If a later phase adds **explicit user-drawn links** (an Obsidian-style manual connection between two memories, as opposed to a derived one), the proposed change is still additive and still avoids SQL:
- Add an optional `links: []` array field to entries in `data/memory.json`, defaulting to `[]` when absent (`entry.get("links", [])`) so every existing entry remains valid without a migration pass.
- `MemoryManager` already owns read/write of this file; the change is a new optional key, not a schema/format break. Old app versions reading a file with this key simply ignore it (JSON is forward-tolerant), and rollback requires no data cleanup (see §9).

No changes to `core/database.py`, no new Alembic-equivalent `_migrate_add_*` function, no new SQLAlchemy model.

## 3. API design

New endpoints, added to the existing `/api/memory` prefix, following the file's existing conventions (owner-scoped via `get_current_user`/`effective_user`, 404-not-403 on cross-owner id access, `HTTPException` for errors):

### `GET /api/memory/graph`

Returns the full owner-scoped graph (nodes + derived edges), subject to server-side limits (see §5).

Query parameters (all optional):
| Param | Type | Default | Meaning |
|---|---|---|---|
| `category` | string, repeatable | none | Filter nodes to one or more of the existing `MEMORY_CATEGORIES` |
| `min_similarity` | float 0–1 | 0.75 | Minimum cosine similarity to draw a semantic edge |
| `max_edges_per_node` | int | 5 | Cap on nearest-neighbor edges per node (bounds edge count) |
| `include_session_edges` | bool | true | Whether to draw "extracted from the same session" edges |
| `include_category_edges` | bool | false | Whether to draw "same category" edges (off by default — high fan-out) |
| `limit` | int | 1000 | Max nodes returned (see §5 for behavior beyond this) |
| `since` | unix ts | none | Only include memories created/updated after this time (for incremental/lazy loading) |

Response shape:
```json
{
  "nodes": [
    {"id": "mem_123", "text": "...", "category": "preference", "pinned": false,
     "uses": 3, "timestamp": 1769..., "session_id": "sess_abc"}
  ],
  "edges": [
    {"source": "mem_123", "target": "mem_456", "type": "similarity", "weight": 0.86},
    {"source": "mem_123", "target": "mem_789", "type": "session", "weight": 1.0}
  ],
  "meta": {"node_count": 214, "truncated": false, "generated_at": 1769...}
}
```

Auth/ownership: identical pattern to `GET /api/memory` — `require_user(request)` (no `can_manage_memory` needed, matching that read-only memory listing today requires no special privilege), owner resolved via `effective_user(request)` so Bearer-token callers are scoped correctly, and the response is built exclusively from `memory_manager.load(owner=user)` — no id-based lookup path exists on this endpoint, so there's no cross-owner leakage vector to guard beyond the load-time filter.

### `GET /api/memory/graph/{id}/neighbors`

Lazy-expansion endpoint for a single node's immediate neighbors, for progressive loading on large graphs (see §5). Reuses the existing `_verify_memory_owner(id, owner)` 404-on-mismatch helper before returning anything.

### `POST /api/memory/{id}/links` / `DELETE /api/memory/{id}/links/{target_id}` (phase 2, optional)

Manual link create/remove, gated by `require_privilege(request, "can_manage_memory")` exactly like other memory mutations (`PUT`/`DELETE /api/memory/{id}`). Not required for v1; documented here so the API surface is planned coherently rather than bolted on later.

### API token scope

The graph read endpoint is exposed under the **existing** `memory:read` scope already defined in `routes/api_token_routes.py` — no new scope is introduced, consistent with the analysis's note about avoiding further scope proliferation.

## 4. UI design

**Entry point**: new "Graph" tab in the existing `.memory-tabs` strip (`index.html`), alongside Browse/Skills/Add/Settings. Selecting it lazily creates the Cytoscape instance on first open (not on modal load) to avoid any cost for users who never click it.

**Canvas**: fills the tab panel body, resizing with the modal (a `ResizeObserver` on the panel calls `cy.resize()` + `cy.fit()`, since `windowDrag.js`/`tileManager.js` already support the user resizing/tiling the modal itself).

**Node styling** (mirrors the existing Browse-tab visual language rather than inventing a new one):
- Fill color by category, reusing the same category color mapping already defined for the Browse tab's category chips/badges in `style.css`.
- Size scaled by `uses` (more-referenced memories render larger, echoing their real weight in chat context injection).
- Pinned memories get a distinct border/ring, matching the pinned badge already used in the list view.
- Node label: truncated memory text (first ~40 chars), full text on hover tooltip.

**Edge styling**:
- Semantic-similarity edges: solid line, opacity/thickness scaled by `weight` (cosine similarity).
- Same-session edges: dashed line, fixed low opacity, to visually separate "structurally related" from "semantically related" without cluttering the primary read.
- A small legend (reusing the existing modal's compact panel styling) explains the two edge types and lets the user toggle each on/off — mapped directly to the `include_session_edges`/`include_category_edges` query params.

**Interactions**:
- Pan/zoom: native Cytoscape gestures.
- Click node: opens a side panel (or reuses the existing inline-edit affordance from the Browse tab) showing full text, category, pin state, use count, and Pin/Edit/Delete actions — deliberately reusing `memory.js`'s existing edit/pin/delete logic rather than re-implementing it, so behavior (and any future changes to it) stays in one place.
- Double-click node: same inline-edit flow already used in the Browse tab.
- Drag node: repositions it client-side only (not persisted), matching how Obsidian's own graph behaves — dragged position is not sent back to the server.
- Search box (reusing the existing `#memory-search` pattern): highlights/centers matching nodes instead of filtering a list.
- Category filter chips (reusing the existing Browse-tab chip component): toggle node visibility by category.
- Similarity threshold slider: re-queries `min_similarity` and redraws edges (debounced, see §5).
- Click-to-isolate: clicking a node dims everything outside its connected component, a common Obsidian-graph affordance, implemented purely client-side against the already-loaded graph (no extra request).

**Empty/loading/error states**: follow existing modal conventions — spinner (`spinner.js`) while the initial `GET /api/memory/graph` is in flight, an empty-state message reusing the Browse tab's "no memories yet" copy/style when the user has zero memories, and a plain inline error message (no full-modal takeover) on request failure, consistent with how other tabs degrade.

**Accessibility note**: a pure canvas graph is not screen-reader navigable; the existing Browse tab remains the accessible list view of the same data, so the Graph tab is explicitly an additional visualization, not a replacement — nothing about memory access requires the graph to work.

## 5. Performance considerations

- **Server-side edge computation must avoid O(n²) pairwise similarity.** Use Chroma's own ANN query per node (`MemoryVectorStore.search(text, k=max_edges_per_node)`) rather than brute-force pairwise cosine over all memories. This is a set of N approximate-nearest-neighbor queries (already fast, since Chroma is built for this), not N² raw comparisons.
- **Cap response size.** Default `limit=1000` nodes; if an owner has more memories than that, the endpoint returns the most-recent/most-used `limit` nodes plus `meta.truncated=true`, and the frontend shows a "showing N of M — refine filters to see more" notice rather than silently dropping data. The `/graph/{id}/neighbors` endpoint exists precisely so a user can drill into the truncated remainder on demand instead of the server ever needing to return everything at once.
- **Canvas over SVG.** Cytoscape's canvas renderer is required, not optional, once node count exceeds roughly 200–300 — an SVG-per-node approach (as some hand-rolled D3 examples use) degrades badly at that scale by DOM node count alone. This is why Cytoscape (canvas-first) was preferred over a naive D3+SVG approach in §1.
- **Debounce filter changes.** The similarity-threshold slider and category toggles should debounce their re-fetch (e.g. 250ms) rather than firing a request per slider tick.
- **Server-side short-lived cache.** Cache the computed `{nodes, edges}` graph per owner (in-process, e.g. a small dict keyed by `owner` with a TTL of a minute or two, or invalidated eagerly on `memory_added`/`memory_updated`/`memory_deleted` if the optional SSE work in §1 of the analysis is picked up later). This avoids recomputing kNN edges on every tab-open within a short window, at negligible memory cost given the existing single-process assumption already baked into the app.
- **Target scale.** This is a personal/local-assistant memory store, not a general knowledge base — the design explicitly targets smooth interaction for roughly 200–2,000 memories per user, with graceful truncation (not failure) beyond that, rather than engineering for unbounded scale from day one.

## 6. Scalability

- **Multi-user isolation carries no new risk**: the graph endpoint reads through the same `memory_manager.load(owner=user)` owner filter every other memory route already uses, so per-user data volume and per-user isolation both scale exactly as well (or as poorly) as the existing memory list/search endpoints do today.
- **No new infrastructure dependency.** No new database engine, no new container (confirmed in the analysis: ChromaDB is already a separate container the app already depends on). Scaling the graph feature scales exactly with scaling the app's existing single-process/single-SQLite/single-Chroma-container deployment model — this proposal does not change that model or its known limits.
- **Phase-2 scaling lever (only if needed)**: if a user's memory count grows large enough that on-request kNN computation becomes noticeably slow even with caching, the next lever is an incrementally-maintained nearest-neighbor cache (refreshed only for the new/changed memory on each `memory_added`/`memory_updated`, not recomputed from scratch), rather than anything to do with the graph *rendering* — the render side is already the cheap part once node count is capped per §5.

## 7. Security

- **No new privilege boundary.** `GET /api/memory/graph` requires only `require_user`, matching the existing `GET /api/memory` and `/timeline` endpoints — consistent with `THREAT_MODEL.md` explicitly listing memory management as available to non-admins. Mutating endpoints (optional manual links, phase 2) require `can_manage_memory`, matching existing memory-mutation routes.
- **No new cross-owner leakage vector.** The list endpoint has no id-based lookup path (built entirely from an owner-filtered load), and the neighbors/link endpoints reuse the existing `_verify_memory_owner` 404-on-mismatch helper rather than introducing a new ownership check pattern.
- **XSS hygiene in rendering.** Memory text is user-authored/LLM-extracted content rendered into node labels/tooltips — it must go through the same escaping helper already used elsewhere in the frontend (`uiModule.esc()` per the existing markdown-rendering convention) before being placed in any DOM tooltip; Cytoscape's own canvas node labels are not DOM-injected and are safe by construction, but any HTML side-panel showing full memory text on click must still escape it.
- **API token scope reuse, not expansion.** Exposing the read endpoint under the existing `memory:read` scope avoids adding to the token-scope surface, per the analysis's note on the "coarse scopes" gap already tracked in `THREAT_MODEL.md`.
- **Light rate limiting.** Even in a trusted-local-network threat model, add a light per-owner limit (reusing `src/rate_limiter.py`, the same mechanism already used for login) on `GET /api/memory/graph` so a misbehaving client/tab can't hammer Chroma with repeated kNN queries — cheap insurance, not a response to a specific known threat.
- **No prompt-injection surface introduced.** This is a display endpoint; it does not feed memory text back into an LLM prompt, so the existing `untrusted_context_message()` wrapping requirement (which already governs memory text reaching the agent loop) is unaffected and does not need to be duplicated here.

## 8. Migration strategy

No data migration is required for v1 (§2). The rollout is staged purely at the feature-flag / code level:

- **Phase 1 — backend only.** Ship `routes/memory/memory_graph_routes.py` + `src/memory_graph.py`, mounted in `app.py`, with no frontend entry point yet. Fully testable via the existing route-factory test convention (§10) and manual `curl`, with zero user-visible surface — the lowest-risk possible increment.
- **Phase 2 — frontend behind an opt-in setting.** Ship the "Graph" tab gated by a Settings-tab toggle (e.g. "Enable Memory Graph (beta)"), defaulting **off**, so existing users see no change until they opt in. This mirrors how a genuinely new, unproven interaction pattern should be introduced into an app whose UI conventions (flat list, modal-based navigation) this feature is a first departure from.
- **Phase 3 — default-on.** After a soak period with no material bug reports, flip the default to on and drop the "(beta)" label. The toggle itself can remain as a permanent "hide Graph tab" preference for users who simply don't want it, or be removed — a call to make at that time, not now.
- **Phase 4 — optional extensions**, proposed separately and not part of this design's approval scope: manual linking (§2/§3 phase-2 bits), live SSE-based updates (per the analysis's §14 finding that no such channel exists today), and cross-linking memories to the documents/sessions they were extracted from.

Each phase is independently shippable, independently revertable, and does not block on the next phase being designed yet.

## 9. Rollback strategy

- **Backend rollback**: the new route module is additive and isolated — removing its `include_router(...)` call (or the whole file) from `app.py` has zero blast radius on any other route, since it shares only already-existing, already-stable singletons (`memory_manager`, `memory_vector`) and defines no new schema. Rollback is a plain code revert.
- **Frontend rollback**: with the Phase-2 opt-in flag, disabling the flag hides the tab immediately with no deploy needed. A full revert (removing the module, the tab markup, and the flag) is likewise a plain code revert — no other module should come to depend on `memoryGraph.js` internals, which should be verified (grep for imports of it) before merging any phase past Phase 1.
- **No data to roll back.** Because v1 stores nothing new (§2), there is no "undo a migration" step, no backfill to reverse, and no risk of orphaned rows or half-migrated JSON — rollback is strictly a code-level operation at every phase of v1.
- **If the optional `links` JSON field (§2, phase-2 extension) ships later**: rolling back the code that reads/writes it leaves harmless, inert data behind (`links: []` or a populated array), which older `MemoryManager` code already tolerates by construction (unknown/extra JSON keys are never rejected) — no cleanup pass is required unless the user explicitly wants the field purged, which would be a separate, deliberate data-cleanup action, not an automatic part of rollback.

## 10. Testing strategy

- **Backend route tests** (`tests/test_memory_graph_routes.py`, new): follow the repo's established convention exactly — build the router via `setup_memory_graph_routes(...)` directly (no `TestClient`/ASGI app), look up the endpoint function off `router.routes` by path, call it directly with a hand-built `Request` stand-in, and bypass auth via `monkeypatch.setattr(memory_graph_routes, "get_current_user", ...)` / `require_user`. Required cases:
  - Owner isolation: a graph built for owner A contains none of owner B's memories (mirrors `tests/test_memory_owner_isolation.py`).
  - `min_similarity`/`max_edges_per_node`/`category` filters change the returned edge/node sets as expected.
  - `limit`/`meta.truncated` behavior when an owner has more memories than the limit.
  - `/graph/{id}/neighbors` 404s on a foreign-owned id (reusing the existing `_verify_memory_owner` test pattern).
- **Edge-derivation unit tests** (`tests/test_memory_graph_edges.py`, new): the similarity/session/category edge-building logic in `src/memory_graph.py` should be a pure function taking pre-fetched memory + neighbor data and returning edge lists, so it's table-driven-testable without Chroma or FastAPI in the loop — pass in synthetic embeddings/neighbor lists and assert threshold and top-k truncation behavior deterministically.
- **Frontend pure-logic tests**: any pure-JS piece of `memoryGraph.js` that isn't DOM-dependent (mapping an API response into Cytoscape `elements`, category→color lookup, client-side connected-component isolation on click) gets a `.test.mjs` file run via `node --test`, wrapped by a thin pytest shim — following the exact precedent of `tests/test_streaming_segmenter_js.py` / `tests/streaming/invariant.test.mjs`.
- **Manual/DOM verification**: since the repo has no DOM/browser test harness (§17 of the analysis), pan/zoom/drag/click interactions and visual correctness (category colors, edge styling, legend, resize behavior) are verified manually against the running dev server before merge, and this should be stated explicitly in the PR description rather than implied — consistent with how the repo already documents this limitation for other rendering-heavy modules.
- **Performance smoke test**: seed a temporary `MemoryManager` with a few hundred and a few thousand synthetic entries and assert `GET /api/memory/graph` completes within a defined latency budget with a warm cache — a regression guard against an accidental reintroduction of O(n²) pairwise comparison.
- **Marker tagging**: new tests get the existing `area_routes`/`area_services` markers per `tests/_taxonomy.py` so they're picked up by the same `-m area_routes` selective-run convention the rest of the suite already uses.

## Open questions requiring a decision before implementation

1. **Graph library**: confirm Cytoscape.js (recommended, §1) vs. a hand-composed D3 stack vs. a from-scratch canvas renderer. This is the single highest-leverage decision in the whole design.
2. **Tab-in-modal vs. standalone modal**: confirmed recommendation is a tab inside the existing Brain modal (§1); flag if a standalone full-screen view is actually wanted instead.
3. **Manual linking (phase 2)**: is user-drawn explicit linking between memories in scope at all, or should the feature stay purely derived-edges-only indefinitely? This affects whether the `links` JSON field (§2) is ever needed.
4. **Opt-in beta flag vs. shipping straight to default-on**: confirmed recommendation is opt-in first (§8); flag if the team prefers to skip that staging given how isolated the feature already is.

No further action will be taken until this design (and the open questions above) are reviewed.
