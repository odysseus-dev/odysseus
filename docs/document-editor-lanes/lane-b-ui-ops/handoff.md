# Lane B handoff: document editor UI operations

Worktree: `/home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-b-ui-ops`
Branch: `lane/docs-lane-b-ui-ops`
Shared baseline: `f3a05c10c998e4efff14ac6498b01bd50138ad6e`
Statement baseline: `087e6c8837969339e9e7823dd84a4d717d536cea`
Removal manifest: `docs/document-editor-lanes/lane-b-ui-ops/removal.json`

This lane owns 76 baseline items across seven modules. It does not edit `static/js/document.js`, the composition root, `frontend/modules.json`, `static/sw.js`, or the deployed copies under `static/js/document/`. Do not push or merge from this worktree.

The full extraction ledger stays red while the monolith still declares the moved names. That `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` result is blocked on integration. The removal manifest simulation is the lane gate.

## Checkpoint 1: documentActions

Source: `frontend/src/features/documents/ui/documentActions.js`

Moved declarations, all Appendix A exports:

- `autoTitleFromContent`
- `copyDocument`
- `runDocument`
- `updateLanguage`
- `updateTitle`

Imports: `renderTabs` from `application/documentCommands.js`, `API_BASE`, `activeDocId`, and `docs` from `application/documentState.js`, `_isRenderLang` from `domain/documentKinds.js`, `codeRunnerModule` and `uiModule` from `ui/host.js`, and `toggleHtmlPreview` from `ui/previewModes.js`.

No Class 2 state rewrites. Title and language updates mutate `docs.get(id).title` and `.language`, which are record fields, then call the `renderTabs` command. No new global state.

The monolith import, applied by the integrator:

```js
import { autoTitleFromContent, copyDocument, runDocument, updateLanguage, updateTitle } from "./document/ui/documentActions.js";
```

Focused check:

```bash
node --test tests/documentActions.test.mjs
```

That command passed: 1 runner test, which executed 7 behavior assertions (markdown and HTML titles, existing-title skip, truncation, language patch, clipboard fallback, and run routing).

Verifier:

```bash
node /home/alteixeira20/Projects/Odysseus/audit-artifacts/tools/document-extraction-verifier/bin/verify.mjs \
  --worktree /home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-b-ui-ops \
  --manifest /home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-b-ui-ops/docs/document-editor-lanes/lane-b-ui-ops/removal.json \
  --check-frontend /home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-b-ui-ops/scripts/check_frontend.mjs
```

Checkpoint 1 verifier result, with only this module present:

- Removal manifest passed: 5 of 5 declarations, 5 Class 1 export additions, 0 body violations, simulation clean (463 surviving statements).
- Exports, import graph, state ownership, and ports passed. Graph: 36 modules, 90 edges, 0 cycles.
- Ledger failed only on 5 `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` entries. Those stay blocked until the integrator deletes the declarations from `static/js/document.js`.
- Ports still report `actualObserverCount: 0` against an expected count of 1. The ports gate passed. That observer gap is pre-existing and is not a documentActions defect.

## Checkpoint 2: header overflow

Source: `frontend/src/features/documents/ui/header/overflow.js`

Moved declarations: `_DOC_RECENTS_KEY`, `_DOC_MAX_VISIBLE`, `_getDocRecent`, `_trackDocAction`, and `initActionOverflow` (5 items, all Appendix A exports). The existing page-scope `_syncOverflow` callback is retained with an explicit `globalThis._syncOverflow` assignment. The monolith used an implicit global assignment; explicit assignment is required in strict ES modules. The repository has no direct reads of the bare `_syncOverflow` name. Markdown toolbar overflow callbacks are separate properties on the toolbar element.

Focused test:

```bash
node --test tests/documentOverflow.test.mjs
```

Passed: 1 runner test, behavior suite executed with pass=1, fail=0. It verifies initialization, default visibility, recency-based reorder, callback compatibility, and opening the menu.

Verifier, using the cumulative manifest: Appendix A exports clean; import graph clean with 0 cycles; layer and state ownership clean; ports clean; removal simulation clean. The manifest reports 10 declarations listed, 9 verified moved, 5 rewrites applied, and `Removal Status: VIOLATIONS`; the reason is not yet isolated. The full ledger reports 77 duplicate-across-monolith-and-module findings because all moved declarations remain in integration-owned `static/js/document.js`. This is expected before integration. Do not describe the overall verifier as passing.

Local checkpoint commit: recorded after the test and verifier run.

## Remaining modules

Header overflow, markdown images, document flows, selection highlights (`stmt#11153`), toolbar, and tab bar (`stmt#206`) are still outstanding for this handoff. Continue in that order. Each group gets focused tests, a manifest update, verifier evidence, and a local commit.

## Authority

The next agent may edit Lane B source, tests, and `docs/document-editor-lanes/lane-b-ui-ops/` inside this worktree. Commits stay local. Pushes, merges, and edits to shared integration files or other lanes require a new authorization.
