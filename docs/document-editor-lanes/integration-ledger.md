# Document Editor integration ledger

## Lane A checkpoint

Scope: integrate `lane/docs-lane-a-app-ai` into `refactor/document-editor-decomposition`.
Development head: `e87326f893ae3ae0ae89923080bca938043ad5ae`.
Integrated commits: `400aeb97`, `162566c3`, `2fcadd38`, `e87326f8`.

- Integrated all 43 assigned declarations across `documentUpdate`, `versionHistory`, `liveStream`, and `documentStatus`.
- Removed the 43 original declarations from `static/js/document.js`; imported the maintained modules and retained the stream/update named API through stable forwarding bindings.
- Published all four modules under the mirrored `static/js/document/ui/` paths and added them to the service-worker precache. Cache key advanced to `odysseus-v1144-pr40-editor-precache`.
- Extraction verifier: 565 total, 408 moved, 157 unmoved, zero duplicate, zero missing; Lane A removal 43/43, 26 approved rewrites, zero exceptions, clean simulation. Exact exports, 46-module import graph (178 edges, zero cycles), state ownership, command and hook inventories pass. The observer inventory remains 0/1 and is permitted by the verifier's existing optional observer contract.
- Focused Lane A JavaScript behavior suites: 4 passed, 0 failed, 0 skipped.
- Public module API and persistence lifecycle regressions: 19 passed, 0 failed.
- Frontend architecture checker passed (89 maintained modules); publication check passed with zero stale files; `git diff --check` passed.
- Remaining 157 items are exactly Lane C's 149 assigned items and the integration owner's 8 composition/facade items.

## Lane B checkpoint

Scope: integrate `lane/docs-lane-b-ui-ops` into `refactor/document-editor-decomposition`.
Development head: `e6dc3dbc659fbac43149c2306487c962024c75eb`.
Integration parent before checkpoint: `e6dc3dbc659fbac43149c2306487c962024c75eb`.

- Integrated 76 of 76 assigned baseline items: 74 declarations, `stmt#11153`, and `stmt#206`.
- Removal manifest: 76 verified, 73 authorized rewrites, clean removal simulation.
- Extraction verifier: 565 baseline items accounted for, 200 remain in the monolith, 365 moved, zero missing or duplicated; zero export, import-cycle, layer, state-ownership, or port violations.
- Autosave correction: the original integration baseline `f3a05c10c998e4efff14ac6498b01bd50138ad6e` already assigned imported source-editor debounce bindings from the composition root. The browser edit handler threw before scheduling autosave. Scheduling and timer cleanup now execute through callbacks owned by `sourceEditor.js`; the Appendix A export set remains unchanged.
- JavaScript Lane B suites: 7 passed, 0 skipped.
- `tests/test_document_persistence_lifecycle.py`: 16 passed.
- Targeted browser/Python regression set: 40 passed, including both autosave regressions and panel disposal.
- Orphaned library open regression: 6 passed.
- Frontend publication parity: passed, zero changed modules. Architecture checker: passed, 85 maintained modules. `git diff --check`: passed.

### PDF lifecycle exception review

The eight verifier exceptions are authorized substitutions to the existing PDF tools boundary and remain necessary:

- `_closeWithoutDeleting` cancels the pane timer after pane edits have synchronously updated the document draft. The draft is copied to document state and saved as a closing draft.
- `_detachDocFromSession` cancels the delayed timer, synchronizes the pane into the document draft, then persists nonempty drafts before detaching. Empty drafts follow the delete path.
- `deleteActiveDocument` records pending PDF work before cancellation, waits for serialized writes, and cleans PDF state only after server deletion succeeds. On failure it retains the document and reschedules pending PDF persistence.
- `loadSessionDocs` detects pending PDF work, cancels its timer, and saves the already synchronized draft before removing documents from the previous session. A save already in progress retains the closing draft.

No blocking PDF data-loss defect was found in these paths. Browser tests cover pending saves during successful and failed deletion, serialized persistence, and document lifecycle. Remaining risk: exhaustive real-user PDF editing during every session-switch and close interleaving was not separately exercised in a live application session.

### Observer and publication notes

The optional `setImageSelectionObserver` port has zero registered observers and no contract violation. It is distinct from `_selResizeObserver`, which selection highlights creates when needed and disconnects during panel disposal. Lane B published modules are registered in `frontend/modules.json` and `static/sw.js`; the untracked `history/versionHistory.js` and `status/documentStatus.js` files were stale outputs without maintained sources or publication entries and were removed.

## Lane C checkpoint

Scope: integrate `lane/docs-lane-c-panel-lifecycle` into `refactor/document-editor-decomposition`.
Development branch baseline: `f3a05c10c998e4efff14ac6498b01bd50138ad6e`. Its worktree contained a shared extraction overlay spanning all lanes and no Lane C commits. The final maintained implementation was reviewed and integrated from the authoritative worktree, then the development overlay was preserved before cleanup.

- Integrated all 149 assigned items across nine modules: 26 declarations, 3 top-level statements, and 120 `openPanel()` statements.
- The Lane C removal manifest verifies 149/149 items, 365 authorized rewrites, two documented human-review exceptions, and a clean removal simulation.
- Panel lifecycle initialization, repeated open/close, minimize/restore, document navigation, mobile dock, editor initialization, email wiring, Escape layering, and disposal remain coordinated by explicit module interfaces.
- Source editor debounce work and selection observer cleanup are owned by their modules. Autosave no longer mutates imported source-editor timer bindings.
- Replaced the recursive `generateEmailReply` global bridge with the owned `aiReply` export. Email send workflow now imports its required collaborators directly.
- Rich-text dropdown activation tokens are issued by `formatMenus`, the state owner. The integration overflow callback remains module-owned and no longer creates a global mutable alias.
- Registered and published all nine modules. Maintained/published parity is clean; service-worker cache key is `odysseus-v1145-editor-precache`.
- Canonical verifier against baseline `087e6c8837969339e9e7823dd84a4d717d536cea`: PASS, 565 total, 557 moved, 8 planned composition-root items, zero missing or duplicate items, zero violations. Exact exports: 46 modules, zero violations. Import graph: 55 modules, 320 edges, zero cycles or layer violations. State ownership: 48 files, zero violations. Ports: 12 commands, 6 hooks, zero registered optional observers, zero violations.
- JavaScript document suites: 114 passed, zero failed or skipped. Full document Python/browser-backed regression group: 401 passed, zero failed, one pre-existing SQLAlchemy deprecation warning. Verifier negative and positive controls: 25/25 passed.
- Frontend architecture checker passed (98 maintained modules); publication check passed with zero changed modules; `git diff --check` passed.

## Composition root and final accounting

`static/js/document.js` now imports the extracted modules, binds public commands, initializes the module wiring in order, and provides the compatibility facade. The eight planned index items remain: `init`, `_maybeOpenDocFromHash`, `clearAll`, `getCurrentDocId`, `documentModule`, and three facade publication statements. The facade preserves 30 named exports and 39 default API properties, verified against the source contract and API tests. No duplicate implementation was retained for the extracted Lane A, B, or C modules.

The verifier support was corrected to load the pinned 565-item baseline from Git when the optional baseline worktree is absent. Its full test suite now passes 25/25, including positive extraction, negative mutation, ownership, dependency, persistence-hook, and openPanel-range controls. Lane C's two human-review exceptions are the documented statement rewrites for `openPanel#6237` and `openPanel#7564`; both are represented in the removal manifest and exercised by lifecycle regressions.

The final audit package records exact commands and results, all verifier exceptions, repository revisions, limitations, and two independent audit briefs. No independent audit has yet been performed.
