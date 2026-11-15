# Document Editor integration ledger

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

### Remaining integration dependencies

Lanes A and C and the final facade/composition review remain. Host compatibility callbacks currently bridge to integration-owned functions in the composition root; reconcile them with Lane A's maintained status module during Lane A integration. Lane C integration waits for its completed handoff. The full 565-item decomposition is not yet complete.
