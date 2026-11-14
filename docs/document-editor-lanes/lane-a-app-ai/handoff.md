# Lane A handoff: document editor app and AI updates

Worktree: `/home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-a-app-ai`
Branch: `lane/docs-lane-a-app-ai`
Shared baseline: `f3a05c10c998e4efff14ac6498b01bd50138ad6e`
Statement baseline: `087e6c8837969339e9e7823dd84a4d717d536cea`
Removal manifest: `docs/document-editor-lanes/lane-a-app-ai/removal.json`

This lane does not edit `static/js/document.js`, the composition root, `frontend/modules.json`, `static/sw.js`, or the deployed copies under `static/js/document/`.

## Checkpoint 1: documentUpdate

Source: `frontend/src/features/documents/ui/aiUpdates/documentUpdate.js`

Moved declarations:

- `_animationCancel`
- `lineDiff`
- `animateDocChange`
- `handleDocUpdate`

Appendix A lists no exports for this module. The file keeps the declarations private and publishes `lineDiff`, `animateDocChange`, and `handleDocUpdate` on `globalThis`, matching the email-document bridge. The manifest's `addedImports` are the side-effect import and the three monolith wrappers. The integrator applies them. This lane does not.

`handleDocUpdate` state writes already present on `f3a05c10` match the verifier's Class 2 rewrites against `087e6c88`:

- `bumpNavigationGeneration()`
- `removeDocument(streamingId)`
- `registerDocument(...)` for both map inserts
- `setActiveDocId(...)` for both active-document assignments

The other three declarations match the statement baseline with no rewrites.

Focused check:

```bash
node --test tests/documentUpdate.test.mjs
```

That command passed: 1 runner test, which executed 14 behavior assertions.

Verifier, run after this checkpoint:

```bash
node /home/alteixeira20/Projects/Odysseus/audit-artifacts/tools/document-extraction-verifier/bin/verify.mjs \
  --worktree /home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-a-app-ai \
  --manifest /home/alteixeira20/Projects/Odysseus/worktrees/document-editor-lanes/lane-a-app-ai/docs/document-editor-lanes/lane-a-app-ai/removal.json
```

Verifier result for this checkpoint:

- Overall `passed`: false.
- Ledger: 565 total, 289 moved, 272 unmoved, 4 duplicate, 0 missing.
- The only ledger violations are `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` for `_animationCancel`, `lineDiff`, `animateDocChange`, and `handleDocUpdate`.
- Removal manifest: 4 declarations verified, 6 Class 2 rewrites, 0 human-review exceptions, simulation clean, `passed: true`.
- No export, graph, state-ownership, or port violations were reported.

The full ledger stays red because this lane must not delete the declarations from `static/js/document.js`. The removal simulation is the gate for this module. The other Lane A declarations are still unmoved, so a fully green ledger is not expected yet.

## Remaining Lane A modules

1. `ui/history/versionHistory` (9 declarations)
2. `ui/aiUpdates/liveStream` (8 declarations)
3. `ui/status/documentStatus` (22 declarations)
