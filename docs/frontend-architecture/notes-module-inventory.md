# Notes source and deployment inventory

The feature has 42 maintained modules. The unchanged F1 action-menu proof and
shared confetti bring the full manifest to 44. Outputs are copied byte-for-byte;
count maintained source once. The legacy entry is a five-line compatibility facade.

| Source under frontend/src | Deployment under static | Lines | Layer |
| --- | --- | ---: | --- |
| [features/notes/application/agentActions.js](../../frontend/src/features/notes/application/agentActions.js) | [js/features/notes/application/agentActions.js](../../static/js/features/notes/application/agentActions.js) | 67 | application |
| [features/notes/application/cardOperations.js](../../frontend/src/features/notes/application/cardOperations.js) | [js/features/notes/application/cardOperations.js](../../static/js/features/notes/application/cardOperations.js) | 79 | application |
| [features/notes/application/checklistOperations.js](../../frontend/src/features/notes/application/checklistOperations.js) | [js/features/notes/application/checklistOperations.js](../../static/js/features/notes/application/checklistOperations.js) | 89 | application |
| [features/notes/application/deletion.js](../../frontend/src/features/notes/application/deletion.js) | [js/features/notes/application/deletion.js](../../static/js/features/notes/application/deletion.js) | 51 | application |
| [features/notes/application/drafts.js](../../frontend/src/features/notes/application/drafts.js) | [js/features/notes/application/drafts.js](../../static/js/features/notes/application/drafts.js) | 55 | application |
| [features/notes/application/queries.js](../../frontend/src/features/notes/application/queries.js) | [js/features/notes/application/queries.js](../../static/js/features/notes/application/queries.js) | 66 | application |
| [features/notes/application/reminders.js](../../frontend/src/features/notes/application/reminders.js) | [js/features/notes/application/reminders.js](../../static/js/features/notes/application/reminders.js) | 182 | application |
| [features/notes/application/selection.js](../../frontend/src/features/notes/application/selection.js) | [js/features/notes/application/selection.js](../../static/js/features/notes/application/selection.js) | 39 | application |
| [features/notes/application/state.js](../../frontend/src/features/notes/application/state.js) | [js/features/notes/application/state.js](../../static/js/features/notes/application/state.js) | 83 | application |
| [features/notes/application/submission.js](../../frontend/src/features/notes/application/submission.js) | [js/features/notes/application/submission.js](../../static/js/features/notes/application/submission.js) | 69 | application |
| [features/notes/application/workflows.js](../../frontend/src/features/notes/application/workflows.js) | [js/features/notes/application/workflows.js](../../static/js/features/notes/application/workflows.js) | 78 | application |
| [features/notes/domain/markdownTasks.js](../../frontend/src/features/notes/domain/markdownTasks.js) | [js/features/notes/domain/markdownTasks.js](../../static/js/features/notes/domain/markdownTasks.js) | 147 | domain |
| [features/notes/domain/noteRules.js](../../frontend/src/features/notes/domain/noteRules.js) | [js/features/notes/domain/noteRules.js](../../static/js/features/notes/domain/noteRules.js) | 87 | domain |
| [features/notes/domain/recurrence.js](../../frontend/src/features/notes/domain/recurrence.js) | [js/features/notes/domain/recurrence.js](../../static/js/features/notes/domain/recurrence.js) | 269 | domain |
| [features/notes/index.js](../../frontend/src/features/notes/index.js) | [js/features/notes/index.js](../../static/js/features/notes/index.js) | 505 | composition |
| [features/notes/infrastructure/http.js](../../frontend/src/features/notes/infrastructure/http.js) | [js/features/notes/infrastructure/http.js](../../static/js/features/notes/infrastructure/http.js) | 39 | infrastructure |
| [features/notes/infrastructure/notifications.js](../../frontend/src/features/notes/infrastructure/notifications.js) | [js/features/notes/infrastructure/notifications.js](../../static/js/features/notes/infrastructure/notifications.js) | 20 | infrastructure |
| [features/notes/infrastructure/storage.js](../../frontend/src/features/notes/infrastructure/storage.js) | [js/features/notes/infrastructure/storage.js](../../static/js/features/notes/infrastructure/storage.js) | 63 | infrastructure |
| [features/notes/ui/cardChecklist.js](../../frontend/src/features/notes/ui/cardChecklist.js) | [js/features/notes/ui/cardChecklist.js](../../static/js/features/notes/ui/cardChecklist.js) | 188 | ui |
| [features/notes/ui/cardInteractions.js](../../frontend/src/features/notes/ui/cardInteractions.js) | [js/features/notes/ui/cardInteractions.js](../../static/js/features/notes/ui/cardInteractions.js) | 341 | ui |
| [features/notes/ui/cardRendering.js](../../frontend/src/features/notes/ui/cardRendering.js) | [js/features/notes/ui/cardRendering.js](../../static/js/features/notes/ui/cardRendering.js) | 228 | ui |
| [features/notes/ui/cardReorder.js](../../frontend/src/features/notes/ui/cardReorder.js) | [js/features/notes/ui/cardReorder.js](../../static/js/features/notes/ui/cardReorder.js) | 181 | ui |
| [features/notes/ui/checklist.js](../../frontend/src/features/notes/ui/checklist.js) | [js/features/notes/ui/checklist.js](../../static/js/features/notes/ui/checklist.js) | 192 | ui |
| [features/notes/ui/checklistReorder.js](../../frontend/src/features/notes/ui/checklistReorder.js) | [js/features/notes/ui/checklistReorder.js](../../static/js/features/notes/ui/checklistReorder.js) | 111 | ui |
| [features/notes/ui/clipboard.js](../../frontend/src/features/notes/ui/clipboard.js) | [js/features/notes/ui/clipboard.js](../../static/js/features/notes/ui/clipboard.js) | 82 | ui |
| [features/notes/ui/colors.js](../../frontend/src/features/notes/ui/colors.js) | [js/features/notes/ui/colors.js](../../static/js/features/notes/ui/colors.js) | 111 | ui |
| [features/notes/ui/draftEditor.js](../../frontend/src/features/notes/ui/draftEditor.js) | [js/features/notes/ui/draftEditor.js](../../static/js/features/notes/ui/draftEditor.js) | 67 | ui |
| [features/notes/ui/drawing.js](../../frontend/src/features/notes/ui/drawing.js) | [js/features/notes/ui/drawing.js](../../static/js/features/notes/ui/drawing.js) | 442 | ui |
| [features/notes/ui/editor.js](../../frontend/src/features/notes/ui/editor.js) | [js/features/notes/ui/editor.js](../../static/js/features/notes/ui/editor.js) | 536 | ui |
| [features/notes/ui/filters.js](../../frontend/src/features/notes/ui/filters.js) | [js/features/notes/ui/filters.js](../../static/js/features/notes/ui/filters.js) | 139 | ui |
| [features/notes/ui/layout.js](../../frontend/src/features/notes/ui/layout.js) | [js/features/notes/ui/layout.js](../../static/js/features/notes/ui/layout.js) | 123 | ui |
| [features/notes/ui/lifecycle.js](../../frontend/src/features/notes/ui/lifecycle.js) | [js/features/notes/ui/lifecycle.js](../../static/js/features/notes/ui/lifecycle.js) | 36 | ui |
| [features/notes/ui/mobileEditor.js](../../frontend/src/features/notes/ui/mobileEditor.js) | [js/features/notes/ui/mobileEditor.js](../../static/js/features/notes/ui/mobileEditor.js) | 257 | ui |
| [features/notes/ui/mobileReorder.js](../../frontend/src/features/notes/ui/mobileReorder.js) | [js/features/notes/ui/mobileReorder.js](../../static/js/features/notes/ui/mobileReorder.js) | 214 | ui |
| [features/notes/ui/panel.js](../../frontend/src/features/notes/ui/panel.js) | [js/features/notes/ui/panel.js](../../static/js/features/notes/ui/panel.js) | 506 | ui |
| [features/notes/ui/quickAdd.js](../../frontend/src/features/notes/ui/quickAdd.js) | [js/features/notes/ui/quickAdd.js](../../static/js/features/notes/ui/quickAdd.js) | 170 | ui |
| [features/notes/ui/reminderDisplay.js](../../frontend/src/features/notes/ui/reminderDisplay.js) | [js/features/notes/ui/reminderDisplay.js](../../static/js/features/notes/ui/reminderDisplay.js) | 129 | ui |
| [features/notes/ui/reminderPicker.js](../../frontend/src/features/notes/ui/reminderPicker.js) | [js/features/notes/ui/reminderPicker.js](../../static/js/features/notes/ui/reminderPicker.js) | 335 | ui |
| [features/notes/ui/selection.js](../../frontend/src/features/notes/ui/selection.js) | [js/features/notes/ui/selection.js](../../static/js/features/notes/ui/selection.js) | 61 | ui |
| [features/notes/ui/text.js](../../frontend/src/features/notes/ui/text.js) | [js/features/notes/ui/text.js](../../static/js/features/notes/ui/text.js) | 39 | ui |
| [features/notes/ui/toolbar.js](../../frontend/src/features/notes/ui/toolbar.js) | [js/features/notes/ui/toolbar.js](../../static/js/features/notes/ui/toolbar.js) | 78 | ui |
| [features/notes/ui/windowLayout.js](../../frontend/src/features/notes/ui/windowLayout.js) | [js/features/notes/ui/windowLayout.js](../../static/js/features/notes/ui/windowLayout.js) | 114 | ui |
| [shared/ui/confetti.js](../../frontend/src/shared/ui/confetti.js) | [js/shared/ui/confetti.js](../../static/js/shared/ui/confetti.js) | 28 | shared UI |

Maintained Notes feature total: 6668 lines. Original entry: 5,471 lines.
Legacy facade after decomposition: five lines. Shared confetti: 28 lines.

See [the case study](notes-case-study.md) for responsibilities and public/state/lifecycle
contracts, and [the validation report](notes-validation.md) for executed evidence.

## Changes since the decomposition

The Notes rework changed this graph after the decomposition above was
validated. The table is regenerated from `frontend/modules.json` and the source
tree (line counts are `wc -l`); the case study, roadmap and validation report
are left as the record of the decomposition itself.

Markdown content model: a note is one markdown document, and checklist items
are `- [ ]` task lines inside it (see `specs/calendar-tasks-notes.md`, "Note
Content Model").

| Module | Layer | Role |
| --- | --- | --- |
| `domain/markdownTasks.js` | domain | Task lines in note markdown: parse, toggle, append, and the legacy `items` array to lines. Mirrors `core/notes_markdown.py`; both are pinned by `tests/fixtures/notes_task_vectors.json`. |

`evidence/notes/implementation.sha256` records the decomposition as reviewed.
It was already stale on `dev` before this rework (`ui/panel.js` and its
deployed copy no longer match) and is not regenerated here.
