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

After checkpoint 1 the removal manifest verified those four declarations, with 6 Class 2 rewrites and a clean simulation. The ledger's only violations were the four expected `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` entries.

The full ledger stays red because this lane must not delete the declarations from `static/js/document.js`. The removal simulation is the gate for each module.

## Checkpoint 2: versionHistory

Source: `frontend/src/features/documents/ui/history/versionHistory.js`

Moved declarations:

- `_versionClickOutside`
- `_versionHistory`
- `_versionHistoryGeneration`
- `_historyOwnsView`
- `toggleVersionHistory`
- `_closeVersionPanel`
- `loadVersionHistory`
- `previewVersion`
- `restoreVersion`

All nine bodies match `087e6c88` with no state-write rewrites. Class 1 exports are `_closeVersionPanel`, `_historyOwnsView`, `_versionHistory`, and `toggleVersionHistory`. The three internal operations stay unexported and are published on `globalThis` for the monolith wrappers.

`_versionHistory` and `_versionHistoryGeneration` stay owned by this module. External reads of `_versionHistory` use the exported live binding.

Focused check:

```bash
node --test tests/documentVersionHistory.test.mjs
```

Cumulative verifier result after this checkpoint:

- Overall `passed`: false.
- Ledger: 565 total, 289 moved, 263 unmoved, 13 duplicate, 0 missing.
- All 13 violations are `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` for the documentUpdate and versionHistory declarations still present in `static/js/document.js`.
- Removal manifest: 13 declarations verified, 10 rewrites, 0 human-review exceptions, simulation clean, `passed: true`.
- No export, graph, state-ownership, or port violations.

## Checkpoint 3: liveStream

Source: `frontend/src/features/documents/ui/aiUpdates/liveStream.js`

Moved declarations:

- `_streamDocId`
- `_syncStreamDocChrome`
- `streamDocOpen`
- `_editAnimFrame`
- `_animateDocEdit`
- `_streamHlDebounce`
- `streamDocDelta`
- `streamDocFinalize`

All eight bodies match `087e6c88`. `streamDocOpen` carries the three Class 2 rewrites already present on `f3a05c10`: `bumpNavigationGeneration()`, `registerDocument(...)`, and `setActiveDocId(...)`. The only Appendix A export is `streamDocFinalize`. `streamDocOpen` and `streamDocDelta` stay unexported and are published on `globalThis` with `_animateDocEdit` and `_syncStreamDocChrome`.

Focused check:

```bash
node --test tests/documentLiveStream.test.mjs
```

The runner clears `NODE_TEST_CONTEXT` before spawning the behavior suite. A nested `node --test` otherwise skips the file and exits 0. The same runner fix is applied to the documentUpdate and version history wrappers.

```bash
node --test tests/documentLiveStream.test.mjs tests/documentUpdate.test.mjs tests/documentVersionHistory.test.mjs
```

That command passed: 3 runner tests. The live stream suite executed 17 behavior assertions.

Cumulative verifier result after this checkpoint:

- Overall `passed`: false.
- Ledger: 565 total, 289 moved, 255 unmoved, 21 duplicate, 0 missing.
- All 21 violations are `DUPLICATE_ACROSS_DOCUMENT_AND_MODULE` for the documentUpdate, versionHistory, and liveStream declarations still present in `static/js/document.js`.
- Removal manifest: 21 declarations verified, 13 rewrites, 0 human-review exceptions, simulation clean, `passed: true`.
- No export, graph, state-ownership, or port violations.

## Remaining Lane A modules

1. `ui/status/documentStatus` (22 declarations)
