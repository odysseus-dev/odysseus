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

Verifier, using the cumulative manifest: Appendix A exports clean; import graph clean with 0 cycles; layer and state ownership clean; ports clean; removal simulation clean. The initial verifier run reported `UNAUTHORIZED_AST_MUTATION` for `initActionOverflow`: replacing the original bare assignment `_syncOverflow = syncOverflow` with a `globalThis` member assignment changed the AST from Identifier to MemberExpression. The fix seeds the legacy global-object binding at module evaluation when absent, then preserves the original assignment inside initialization. This keeps the callback unset until initialization and preserves callback identity. Rerun result: 10 declarations listed, 10 verified moved, 5 rewrites applied, zero removal violations, simulation clean. The full ledger reports 76 duplicate-across-monolith-and-module findings, exactly matching the 74 assigned declarations plus `stmt#11153` and `stmt#206`, because all moved declarations remain in integration-owned `static/js/document.js`. These are expected before integration; there are no additional duplication findings. Do not describe the overall verifier as passing.

Local checkpoint commit: recorded after the test and verifier run.

## Checkpoint 3: markdown images

Source: `frontend/src/features/documents/ui/editor/markdownImages.js`

Moved declarations: `_handleMarkdownImageUpload`, `_insertMarkdownImages`, `_scheduleMarkdownImageAutosave`, and `_uploadMarkdownImages` (4 declarations, exact Appendix A exports). Imported debounce bindings are read only and passed to `clearTimeout`; the module does not assign imported ES module bindings. Upload and autosave ownership remain with their existing UI and persistence interfaces.

Focused check: `node --test tests/markdownImages.test.mjs` passed (4 executed behavioral cases). Coverage includes Markdown insertion and encoded upload IDs, caret/focus, preview refresh and autosave scheduling, invalid language and missing images, failed/empty upload responses, and Rich Text routing. Direct Node imports traverse the browser host's `/static/js/ui.js` URL, so the suite uses the repository's existing `SourceTextModule` pattern with dependency stubs.

Verifier cumulative manifest result: 14 listed, 14 verified, 9 authorized rewrites, zero removal violations, simulation clean. Exact exports, import graph, layer legality, state ownership, and ports are clean. Overall verifier output still contains exactly 76 expected pre-integration duplicates.

## Checkpoint 4: document flows

Source: `frontend/src/features/documents/ui/documentFlows.js`

Moved all 20 Appendix A declarations: `_autoCreateFromInput`, `_autoCreateSession`, `_autoCreating`, `_closeWithoutDeleting`, `_creatingDoc`, `_detachDocFromSession`, `_ensureDocPaneMounted`, `_moveDocToSession`, `_rememberActiveDoc`, `_visibleDocIdsForCurrentSession`, `closeTab`, `createDocument`, `deleteActiveDocument`, `ensureDocPanel`, `getChatDocumentId`, `injectFreshDoc`, `loadDocument`, `loadSessionDocs`, `moveActiveDocumentToCurrentChat`, and `moveActiveDocumentToNewChat`.

Focused check: `node --test tests/documentFlows.test.mjs` passed (3 behavioral cases): successful load and switch, stale navigation response ignored, and 404 cleanup/error reporting. The VM harness stubs the command and UI boundaries; the tested navigation generation is the real flow implementation against a state mock.

Cumulative extraction verifier: 34 listed, 34 verified, 51 Class 1/2 rewrites, 8 documented Class 4 human-review exceptions, simulation clean, zero removal violations. The eight exceptions are the expected PDF pane lifecycle hook substitutions in `_closeWithoutDeleting`, `_detachDocFromSession`, `deleteActiveDocument`, and `loadSessionDocs`: `_cancelPdfPaneSave`, `_syncPdfPaneToDoc`, and `_hasPendingPdfPaneSave` route through the existing PDF module boundary. They are reported, not silently ignored. State transitions use the authoritative state operations.

## Checkpoint 5: selection highlights

Source: `frontend/src/features/documents/ui/selection/selectionHighlights.js`

Moved 20 declarations plus `stmt#11153` (the 21 assigned baseline items). The cumulative removal gate reports 55 listed, 55 verified, 60 rewrites, 8 expected PDF lifecycle hook exceptions, zero removal violations, and a clean simulation. Focused test `node --test tests/selectionHighlights.test.mjs` passes. It checks nearest-occurrence selection reanchoring and removal of stale references, plus one-time page resize registration and idempotent listener disposal.

Confirmed implementation fix: the generated resize installer registered during module evaluation but had no disposer and was not idempotent. It now keeps the evaluation-time registration for current behavior, returns a stable disposer, and safely removes the listener once. Exact Appendix A exports remain unchanged. Test setup instruments the module-local selection array in the VM fixture because native module namespace exports are intentionally read-only.

## Checkpoint 6: toolbar

Source: `frontend/src/features/documents/ui/toolbar/toolbar.js`

Moved all 10 Appendix A declarations. Focused test `node --test tests/documentToolbar.test.mjs` passes for rich-text versus textarea selection priority and click/keyboard dropdown activation.

Confirmed implementation fix: the generated module imported `_mdDdActivationSerial` read-only and fabricated tokens by adding one million, bypassing the `formatMenus` owner and risking collisions with the owning serial. The toolbar no longer imports or reads the owner’s serial. Its local token adapter returns null, which `_showMdDropdown` treats as a request to issue its own serial. Pointerdown/click and keyboard activation retain their event lifecycle without cross-module writes or fabricated serials. Exact exports are unchanged. Cumulative verifier: 65 listed, 65 verified, clean removal gate, zero removal violations; 8 PDF hook exceptions remain documented.

## Checkpoint 7: tab bar

Source: `frontend/src/features/documents/ui/tabs/tabBar.js`

Moved 10 Appendix A declarations and `stmt#206` (11 assigned baseline items). Focused test `node --test tests/documentTabBar.test.mjs` passes, covering escaped titles, active and current-session filtering, new-tab markup, and the open empty-state ghost tab. The existing statement installer remains the style initialization owner.

Final cumulative extraction verifier: 76 listed, 76 verified, 73 authorized rewrites, 8 reported PDF lifecycle hook exceptions, zero removal violations, clean simulation. Exact exports, import graph (0 cycles), layer legality, state ownership, and ports are clean. Overall status remains FAIL solely for 76 expected duplicate-across-monolith-and-module findings pending integration.

## Remaining modules

Header overflow, markdown images, document flows, selection highlights (`stmt#11153`), toolbar, and tab bar (`stmt#206`) are still outstanding for this handoff. Continue in that order. Each group gets focused tests, a manifest update, verifier evidence, and a local commit.

## Authority

The next agent may edit Lane B source, tests, and `docs/document-editor-lanes/lane-b-ui-ops/` inside this worktree. Commits stay local. Pushes, merges, and edits to shared integration files or other lanes require a new authorization.

## Final Lane B validation

- Focused JavaScript suite: `node --test tests/documentActions.test.mjs tests/documentOverflow.test.mjs tests/markdownImages.test.mjs tests/documentFlows.test.mjs tests/selectionHighlights.test.mjs tests/documentToolbar.test.mjs tests/documentTabBar.test.mjs` passed: 7 runners, 7 passed, 0 failed, 0 skipped. Each runner executed its behavior suite.
- Requested Python regressions, using `/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python`: 30 passed, 1 failed. The failure is `test_mobile_explicit_load_restores_full_editor_from_bottom_dock` in `tests/test_doc_library_open_orphaned.py`; browser execution errors in integration-owned `static/js/document.js:2017` with `Assignment to constant variable` during `pane._dispose`. This is outside Lane B's allowed files. The other 30 Python cases passed.
- `python3 -m pytest` with the system interpreter could not collect because that interpreter resolves a local `starlette` module instead of the installed package. The recorded repository virtualenv was then used successfully for the regression run above.
- Final verifier: removal manifest 76/76 verified, 73 authorized rewrites, 8 reported PDF lifecycle hook exceptions, simulation clean, zero removal violations. Appendix A exports, import graph (0 cycles), layers, state ownership, and ports are clean. Overall verifier is FAIL solely for exactly 76 expected `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` findings before integration.
- Lane B commits are local and the worktree is clean. No integration-owned files were modified.
