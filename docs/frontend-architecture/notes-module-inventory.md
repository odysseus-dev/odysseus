# Notes source and deployment inventory

The feature has 41 maintained modules. The unchanged F1 action-menu proof and
shared confetti bring the full manifest to 43. Outputs are copied byte-for-byte;
count maintained source once. The legacy entry is a five-line compatibility facade.

| Source under frontend/src | Deployment under static | Lines | Layer |
| --- | --- | ---: | --- |
| [features/notes/application/agentActions.js](../../frontend/src/features/notes/application/agentActions.js) | [js/features/notes/application/agentActions.js](../../static/js/features/notes/application/agentActions.js) | 67 | application |
| [features/notes/application/cardOperations.js](../../frontend/src/features/notes/application/cardOperations.js) | [js/features/notes/application/cardOperations.js](../../static/js/features/notes/application/cardOperations.js) | 79 | application |
| [features/notes/application/checklistOperations.js](../../frontend/src/features/notes/application/checklistOperations.js) | [js/features/notes/application/checklistOperations.js](../../static/js/features/notes/application/checklistOperations.js) | 91 | application |
| [features/notes/application/deletion.js](../../frontend/src/features/notes/application/deletion.js) | [js/features/notes/application/deletion.js](../../static/js/features/notes/application/deletion.js) | 61 | application |
| [features/notes/application/noteSession.js](../../frontend/src/features/notes/application/noteSession.js) | [js/features/notes/application/noteSession.js](../../static/js/features/notes/application/noteSession.js) | 199 | application |
| [features/notes/application/noteVersions.js](../../frontend/src/features/notes/application/noteVersions.js) | [js/features/notes/application/noteVersions.js](../../static/js/features/notes/application/noteVersions.js) | 22 | application |
| [features/notes/application/queries.js](../../frontend/src/features/notes/application/queries.js) | [js/features/notes/application/queries.js](../../static/js/features/notes/application/queries.js) | 72 | application |
| [features/notes/application/reminders.js](../../frontend/src/features/notes/application/reminders.js) | [js/features/notes/application/reminders.js](../../static/js/features/notes/application/reminders.js) | 172 | application |
| [features/notes/application/selection.js](../../frontend/src/features/notes/application/selection.js) | [js/features/notes/application/selection.js](../../static/js/features/notes/application/selection.js) | 39 | application |
| [features/notes/application/state.js](../../frontend/src/features/notes/application/state.js) | [js/features/notes/application/state.js](../../static/js/features/notes/application/state.js) | 83 | application |
| [features/notes/application/workflows.js](../../frontend/src/features/notes/application/workflows.js) | [js/features/notes/application/workflows.js](../../static/js/features/notes/application/workflows.js) | 81 | application |
| [features/notes/domain/markdownSerializer.js](../../frontend/src/features/notes/domain/markdownSerializer.js) | [js/features/notes/domain/markdownSerializer.js](../../static/js/features/notes/domain/markdownSerializer.js) | 345 | domain |
| [features/notes/domain/markdownTasks.js](../../frontend/src/features/notes/domain/markdownTasks.js) | [js/features/notes/domain/markdownTasks.js](../../static/js/features/notes/domain/markdownTasks.js) | 190 | domain |
| [features/notes/domain/noteRules.js](../../frontend/src/features/notes/domain/noteRules.js) | [js/features/notes/domain/noteRules.js](../../static/js/features/notes/domain/noteRules.js) | 126 | domain |
| [features/notes/domain/recurrence.js](../../frontend/src/features/notes/domain/recurrence.js) | [js/features/notes/domain/recurrence.js](../../static/js/features/notes/domain/recurrence.js) | 269 | domain |
| [features/notes/index.js](../../frontend/src/features/notes/index.js) | [js/features/notes/index.js](../../static/js/features/notes/index.js) | 482 | composition |
| [features/notes/infrastructure/http.js](../../frontend/src/features/notes/infrastructure/http.js) | [js/features/notes/infrastructure/http.js](../../static/js/features/notes/infrastructure/http.js) | 46 | infrastructure |
| [features/notes/infrastructure/notifications.js](../../frontend/src/features/notes/infrastructure/notifications.js) | [js/features/notes/infrastructure/notifications.js](../../static/js/features/notes/infrastructure/notifications.js) | 20 | infrastructure |
| [features/notes/infrastructure/storage.js](../../frontend/src/features/notes/infrastructure/storage.js) | [js/features/notes/infrastructure/storage.js](../../static/js/features/notes/infrastructure/storage.js) | 63 | infrastructure |
| [features/notes/ui/cardChecklist.js](../../frontend/src/features/notes/ui/cardChecklist.js) | [js/features/notes/ui/cardChecklist.js](../../static/js/features/notes/ui/cardChecklist.js) | 183 | ui |
| [features/notes/ui/cardInteractions.js](../../frontend/src/features/notes/ui/cardInteractions.js) | [js/features/notes/ui/cardInteractions.js](../../static/js/features/notes/ui/cardInteractions.js) | 344 | ui |
| [features/notes/ui/cardRendering.js](../../frontend/src/features/notes/ui/cardRendering.js) | [js/features/notes/ui/cardRendering.js](../../static/js/features/notes/ui/cardRendering.js) | 227 | ui |
| [features/notes/ui/cardReorder.js](../../frontend/src/features/notes/ui/cardReorder.js) | [js/features/notes/ui/cardReorder.js](../../static/js/features/notes/ui/cardReorder.js) | 181 | ui |
| [features/notes/ui/clipboard.js](../../frontend/src/features/notes/ui/clipboard.js) | [js/features/notes/ui/clipboard.js](../../static/js/features/notes/ui/clipboard.js) | 82 | ui |
| [features/notes/ui/colors.js](../../frontend/src/features/notes/ui/colors.js) | [js/features/notes/ui/colors.js](../../static/js/features/notes/ui/colors.js) | 85 | ui |
| [features/notes/ui/drawing.js](../../frontend/src/features/notes/ui/drawing.js) | [js/features/notes/ui/drawing.js](../../static/js/features/notes/ui/drawing.js) | 442 | ui |
| [features/notes/ui/filters.js](../../frontend/src/features/notes/ui/filters.js) | [js/features/notes/ui/filters.js](../../static/js/features/notes/ui/filters.js) | 139 | ui |
| [features/notes/ui/layout.js](../../frontend/src/features/notes/ui/layout.js) | [js/features/notes/ui/layout.js](../../static/js/features/notes/ui/layout.js) | 114 | ui |
| [features/notes/ui/lifecycle.js](../../frontend/src/features/notes/ui/lifecycle.js) | [js/features/notes/ui/lifecycle.js](../../static/js/features/notes/ui/lifecycle.js) | 36 | ui |
| [features/notes/ui/mobileReorder.js](../../frontend/src/features/notes/ui/mobileReorder.js) | [js/features/notes/ui/mobileReorder.js](../../static/js/features/notes/ui/mobileReorder.js) | 214 | ui |
| [features/notes/ui/noteTags.js](../../frontend/src/features/notes/ui/noteTags.js) | [js/features/notes/ui/noteTags.js](../../static/js/features/notes/ui/noteTags.js) | 93 | ui |
| [features/notes/ui/noteWindow.js](../../frontend/src/features/notes/ui/noteWindow.js) | [js/features/notes/ui/noteWindow.js](../../static/js/features/notes/ui/noteWindow.js) | 504 | ui |
| [features/notes/ui/panel.js](../../frontend/src/features/notes/ui/panel.js) | [js/features/notes/ui/panel.js](../../static/js/features/notes/ui/panel.js) | 497 | ui |
| [features/notes/ui/quickAdd.js](../../frontend/src/features/notes/ui/quickAdd.js) | [js/features/notes/ui/quickAdd.js](../../static/js/features/notes/ui/quickAdd.js) | 43 | ui |
| [features/notes/ui/reminderDisplay.js](../../frontend/src/features/notes/ui/reminderDisplay.js) | [js/features/notes/ui/reminderDisplay.js](../../static/js/features/notes/ui/reminderDisplay.js) | 129 | ui |
| [features/notes/ui/reminderPicker.js](../../frontend/src/features/notes/ui/reminderPicker.js) | [js/features/notes/ui/reminderPicker.js](../../static/js/features/notes/ui/reminderPicker.js) | 339 | ui |
| [features/notes/ui/richBody.js](../../frontend/src/features/notes/ui/richBody.js) | [js/features/notes/ui/richBody.js](../../static/js/features/notes/ui/richBody.js) | 893 | ui |
| [features/notes/ui/selection.js](../../frontend/src/features/notes/ui/selection.js) | [js/features/notes/ui/selection.js](../../static/js/features/notes/ui/selection.js) | 61 | ui |
| [features/notes/ui/text.js](../../frontend/src/features/notes/ui/text.js) | [js/features/notes/ui/text.js](../../static/js/features/notes/ui/text.js) | 39 | ui |
| [features/notes/ui/toolbar.js](../../frontend/src/features/notes/ui/toolbar.js) | [js/features/notes/ui/toolbar.js](../../static/js/features/notes/ui/toolbar.js) | 78 | ui |
| [features/notes/ui/windowLayout.js](../../frontend/src/features/notes/ui/windowLayout.js) | [js/features/notes/ui/windowLayout.js](../../static/js/features/notes/ui/windowLayout.js) | 114 | ui |
| [shared/ui/confetti.js](../../frontend/src/shared/ui/confetti.js) | [js/shared/ui/confetti.js](../../static/js/shared/ui/confetti.js) | 28 | shared UI |

Maintained Notes feature total: 7344 lines. Original entry: 5,471 lines.
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

Note window as the single editor: a note is read and edited in one window, on
desktop and phones, and the in-grid form, the mobile fullscreen editor and the
form draft autosave are removed (see `specs/calendar-tasks-notes.md`, "Notes
Frontend State And Lifetimes").

| Module | Layer | Role |
| --- | --- | --- |
| `domain/markdownSerializer.js` | domain | Editor DOM back to markdown in the notes dialect, block splitting, and the rules for blocks that can only be edited as source. Pure, tested under `node --test`. |
| `application/noteSession.js` | application | One open note's edit session: dirty flag, 600 ms autosave of the whole document with `body_revision`, 409 conflicts, the local draft backup and the single write queue. |
| `application/noteVersions.js` | application | Note versions this tab wrote itself, so the window follows them instead of reporting a conflict. |
| `ui/noteWindow.js` | ui | The note window: header, toolbar, body, footer, dock chip, conflict and draft banners. |
| `ui/richBody.js` | ui | The editable rendered body, the formatting toolbar, keyboard handling, locked blocks and Markdown source mode. |
| `ui/noteTags.js` | ui | Tag chips with suggestions; the input accepts `#tag`. |

Removed with the form: `application/drafts.js`, `application/submission.js`,
`ui/checklist.js`, `ui/checklistReorder.js`, `ui/draftEditor.js`,
`ui/editor.js` and `ui/mobileEditor.js`. The case study and validation report
still describe them, as they were at the decomposition.

`evidence/notes/implementation.sha256` fingerprints the files #6713 listed
that still exist, plus every Notes module in both trees, as they are now:
`sha256sum -c docs/frontend-architecture/evidence/notes/implementation.sha256`
passes from the repository root. The seven deleted modules are no longer in it.
