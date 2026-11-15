# Human-review exception disposition

The final verifier records 16 Class 4 hook substitutions across the full 565-item migration. These are explicit persistence-boundary rewrites that require human review; none is silently waived. The Lane C removal manifest contains two of these 16. Lane C also has two authorized Class 5 `openPanel()` statement wrappers, which are not human-review exceptions.

| Baseline line | Target line | Hook | Disposition evidence |
|---:|---:|---|---|
| 400 | 207 | `_hasPendingPdfPaneSave` | Pending PDF saves are detected before close/session removal and serialized before a document leaves its owning session. |
| 424 | 218 | `_renderDocumentSaveState` | Save-state updates remain routed through the status/persistence interface; dirty and retry state regressions pass. |
| 5358 | 41 | `_cancelPdfPaneSave` | Pane timers are cancelled only after pending content is synchronized or captured; failed deletion retains and reschedules pending PDF work. |
| 5876 | 96 | `_renderDocumentSaveState` | Save-state updates remain routed through the status/persistence interface; dirty and retry state regressions pass. |
| 6017 | 68 | `_cancelPdfPaneSave` | Pane timers are cancelled only after pending content is synchronized or captured; failed deletion retains and reschedules pending PDF work. |
| 6018 | 69 | `_syncPdfPaneToDoc` | PDF pane edits are synchronized into the owning document draft before close/detach persistence. |
| 6145 | 269 | `_syncPdfPaneToDoc` | PDF pane edits are synchronized into the owning document draft before close/detach persistence. |
| 10594 | 370 | `_hasPendingPdfPaneSave` | Pending PDF saves are detected before close/session removal and serialized before a document leaves its owning session. |
| 10596 | 372 | `_cancelPdfPaneSave` | Pane timers are cancelled only after pending content is synchronized or captured; failed deletion retains and reschedules pending PDF work. |
| 10602 | 378 | `_cancelPdfPaneSave` | Pane timers are cancelled only after pending content is synchronized or captured; failed deletion retains and reschedules pending PDF work. |
| 12177 | 230 | `getChatDocumentId` | Document/session lookup is routed through the assigned navigation command and covered by identity/navigation tests. |
| 12186 | 239 | `_cancelPdfPaneSave` | Pane timers are cancelled only after pending content is synchronized or captured; failed deletion retains and reschedules pending PDF work. |
| 12187 | 240 | `_syncPdfPaneToDoc` | PDF pane edits are synchronized into the owning document draft before close/detach persistence. |
| 12313 | 543 | `_hasPendingPdfPaneSave` | Pending PDF saves are detected before close/session removal and serialized before a document leaves its owning session. |
| 12316 | 546 | `_cancelPdfPaneSave` | Pane timers are cancelled only after pending content is synchronized or captured; failed deletion retains and reschedules pending PDF work. |
| 13468 | 88 | `getChatDocumentId` | Document/session lookup is routed through the assigned navigation command and covered by identity/navigation tests. |

## Lane C subset

- `_renderDocumentSaveState`: baseline line 5876, target line 96 in `ui/panel/panelLifecycle.js` dependencies. Save state remains owned by `ui/status/documentStatus.js` and its explicit panel lifecycle interface.
- `_syncPdfPaneToDoc`: baseline line 6145, target line 269 in `ui/panel/documentPresenter.js` dependencies. The document presenter synchronizes the pane draft before it is saved or detached.

## Separate Lane C statement rewrites

- `openPanel#6237` and `openPanel#7564` are Class 5 named statement wrappers recorded in `removal-lane-c.json`. Their bodies are preserved; the removal simulation is clean. They are not counted among the 16 Class 4 persistence exceptions.

The lane B ledger section documents close, detach, deletion rollback, and session-load handling in more detail. Reviewers should inspect each current source path and test rather than treating this disposition as a substitute for review.
