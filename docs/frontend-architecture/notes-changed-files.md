# Notes complete changed-file inventory

Changed files in PR #6713 (`refactor/notes-reference-decomposition`) against
`dev`. 121 files: 16 modified and 105 added. No files are deleted. Deployment copies
are byte-identical to their registered maintained sources. Image files are review
evidence, not production dependencies.

The unchanged foundation worktree, backend, app bootstrap, HTML, CSS, dependency
lockfiles, Dockerfile and CI workflow are outside this diff. The only Compare
change delegates its existing stateless confetti export to maintained shared UI.

## Maintained source (42)

| Status | File |
| --- | --- |
| A | [frontend/src/features/notes/application/agentActions.js](../../frontend/src/features/notes/application/agentActions.js) |
| A | [frontend/src/features/notes/application/cardOperations.js](../../frontend/src/features/notes/application/cardOperations.js) |
| A | [frontend/src/features/notes/application/checklistOperations.js](../../frontend/src/features/notes/application/checklistOperations.js) |
| A | [frontend/src/features/notes/application/deletion.js](../../frontend/src/features/notes/application/deletion.js) |
| A | [frontend/src/features/notes/application/drafts.js](../../frontend/src/features/notes/application/drafts.js) |
| A | [frontend/src/features/notes/application/queries.js](../../frontend/src/features/notes/application/queries.js) |
| A | [frontend/src/features/notes/application/reminders.js](../../frontend/src/features/notes/application/reminders.js) |
| A | [frontend/src/features/notes/application/selection.js](../../frontend/src/features/notes/application/selection.js) |
| A | [frontend/src/features/notes/application/state.js](../../frontend/src/features/notes/application/state.js) |
| A | [frontend/src/features/notes/application/submission.js](../../frontend/src/features/notes/application/submission.js) |
| A | [frontend/src/features/notes/application/workflows.js](../../frontend/src/features/notes/application/workflows.js) |
| A | [frontend/src/features/notes/domain/noteRules.js](../../frontend/src/features/notes/domain/noteRules.js) |
| A | [frontend/src/features/notes/domain/recurrence.js](../../frontend/src/features/notes/domain/recurrence.js) |
| A | [frontend/src/features/notes/index.js](../../frontend/src/features/notes/index.js) |
| A | [frontend/src/features/notes/infrastructure/http.js](../../frontend/src/features/notes/infrastructure/http.js) |
| A | [frontend/src/features/notes/infrastructure/notifications.js](../../frontend/src/features/notes/infrastructure/notifications.js) |
| A | [frontend/src/features/notes/infrastructure/storage.js](../../frontend/src/features/notes/infrastructure/storage.js) |
| A | [frontend/src/features/notes/ui/cardChecklist.js](../../frontend/src/features/notes/ui/cardChecklist.js) |
| A | [frontend/src/features/notes/ui/cardInteractions.js](../../frontend/src/features/notes/ui/cardInteractions.js) |
| A | [frontend/src/features/notes/ui/cardRendering.js](../../frontend/src/features/notes/ui/cardRendering.js) |
| A | [frontend/src/features/notes/ui/cardReorder.js](../../frontend/src/features/notes/ui/cardReorder.js) |
| A | [frontend/src/features/notes/ui/checklist.js](../../frontend/src/features/notes/ui/checklist.js) |
| A | [frontend/src/features/notes/ui/checklistReorder.js](../../frontend/src/features/notes/ui/checklistReorder.js) |
| A | [frontend/src/features/notes/ui/clipboard.js](../../frontend/src/features/notes/ui/clipboard.js) |
| A | [frontend/src/features/notes/ui/colors.js](../../frontend/src/features/notes/ui/colors.js) |
| A | [frontend/src/features/notes/ui/draftEditor.js](../../frontend/src/features/notes/ui/draftEditor.js) |
| A | [frontend/src/features/notes/ui/drawing.js](../../frontend/src/features/notes/ui/drawing.js) |
| A | [frontend/src/features/notes/ui/editor.js](../../frontend/src/features/notes/ui/editor.js) |
| A | [frontend/src/features/notes/ui/filters.js](../../frontend/src/features/notes/ui/filters.js) |
| A | [frontend/src/features/notes/ui/layout.js](../../frontend/src/features/notes/ui/layout.js) |
| A | [frontend/src/features/notes/ui/lifecycle.js](../../frontend/src/features/notes/ui/lifecycle.js) |
| A | [frontend/src/features/notes/ui/mobileEditor.js](../../frontend/src/features/notes/ui/mobileEditor.js) |
| A | [frontend/src/features/notes/ui/mobileReorder.js](../../frontend/src/features/notes/ui/mobileReorder.js) |
| A | [frontend/src/features/notes/ui/panel.js](../../frontend/src/features/notes/ui/panel.js) |
| A | [frontend/src/features/notes/ui/quickAdd.js](../../frontend/src/features/notes/ui/quickAdd.js) |
| A | [frontend/src/features/notes/ui/reminderDisplay.js](../../frontend/src/features/notes/ui/reminderDisplay.js) |
| A | [frontend/src/features/notes/ui/reminderPicker.js](../../frontend/src/features/notes/ui/reminderPicker.js) |
| A | [frontend/src/features/notes/ui/selection.js](../../frontend/src/features/notes/ui/selection.js) |
| A | [frontend/src/features/notes/ui/text.js](../../frontend/src/features/notes/ui/text.js) |
| A | [frontend/src/features/notes/ui/toolbar.js](../../frontend/src/features/notes/ui/toolbar.js) |
| A | [frontend/src/features/notes/ui/windowLayout.js](../../frontend/src/features/notes/ui/windowLayout.js) |
| A | [frontend/src/shared/ui/confetti.js](../../frontend/src/shared/ui/confetti.js) |

## Deployment copies (42)

| Status | File |
| --- | --- |
| A | [static/js/features/notes/application/agentActions.js](../../static/js/features/notes/application/agentActions.js) |
| A | [static/js/features/notes/application/cardOperations.js](../../static/js/features/notes/application/cardOperations.js) |
| A | [static/js/features/notes/application/checklistOperations.js](../../static/js/features/notes/application/checklistOperations.js) |
| A | [static/js/features/notes/application/deletion.js](../../static/js/features/notes/application/deletion.js) |
| A | [static/js/features/notes/application/drafts.js](../../static/js/features/notes/application/drafts.js) |
| A | [static/js/features/notes/application/queries.js](../../static/js/features/notes/application/queries.js) |
| A | [static/js/features/notes/application/reminders.js](../../static/js/features/notes/application/reminders.js) |
| A | [static/js/features/notes/application/selection.js](../../static/js/features/notes/application/selection.js) |
| A | [static/js/features/notes/application/state.js](../../static/js/features/notes/application/state.js) |
| A | [static/js/features/notes/application/submission.js](../../static/js/features/notes/application/submission.js) |
| A | [static/js/features/notes/application/workflows.js](../../static/js/features/notes/application/workflows.js) |
| A | [static/js/features/notes/domain/noteRules.js](../../static/js/features/notes/domain/noteRules.js) |
| A | [static/js/features/notes/domain/recurrence.js](../../static/js/features/notes/domain/recurrence.js) |
| A | [static/js/features/notes/index.js](../../static/js/features/notes/index.js) |
| A | [static/js/features/notes/infrastructure/http.js](../../static/js/features/notes/infrastructure/http.js) |
| A | [static/js/features/notes/infrastructure/notifications.js](../../static/js/features/notes/infrastructure/notifications.js) |
| A | [static/js/features/notes/infrastructure/storage.js](../../static/js/features/notes/infrastructure/storage.js) |
| A | [static/js/features/notes/ui/cardChecklist.js](../../static/js/features/notes/ui/cardChecklist.js) |
| A | [static/js/features/notes/ui/cardInteractions.js](../../static/js/features/notes/ui/cardInteractions.js) |
| A | [static/js/features/notes/ui/cardRendering.js](../../static/js/features/notes/ui/cardRendering.js) |
| A | [static/js/features/notes/ui/cardReorder.js](../../static/js/features/notes/ui/cardReorder.js) |
| A | [static/js/features/notes/ui/checklist.js](../../static/js/features/notes/ui/checklist.js) |
| A | [static/js/features/notes/ui/checklistReorder.js](../../static/js/features/notes/ui/checklistReorder.js) |
| A | [static/js/features/notes/ui/clipboard.js](../../static/js/features/notes/ui/clipboard.js) |
| A | [static/js/features/notes/ui/colors.js](../../static/js/features/notes/ui/colors.js) |
| A | [static/js/features/notes/ui/draftEditor.js](../../static/js/features/notes/ui/draftEditor.js) |
| A | [static/js/features/notes/ui/drawing.js](../../static/js/features/notes/ui/drawing.js) |
| A | [static/js/features/notes/ui/editor.js](../../static/js/features/notes/ui/editor.js) |
| A | [static/js/features/notes/ui/filters.js](../../static/js/features/notes/ui/filters.js) |
| A | [static/js/features/notes/ui/layout.js](../../static/js/features/notes/ui/layout.js) |
| A | [static/js/features/notes/ui/lifecycle.js](../../static/js/features/notes/ui/lifecycle.js) |
| A | [static/js/features/notes/ui/mobileEditor.js](../../static/js/features/notes/ui/mobileEditor.js) |
| A | [static/js/features/notes/ui/mobileReorder.js](../../static/js/features/notes/ui/mobileReorder.js) |
| A | [static/js/features/notes/ui/panel.js](../../static/js/features/notes/ui/panel.js) |
| A | [static/js/features/notes/ui/quickAdd.js](../../static/js/features/notes/ui/quickAdd.js) |
| A | [static/js/features/notes/ui/reminderDisplay.js](../../static/js/features/notes/ui/reminderDisplay.js) |
| A | [static/js/features/notes/ui/reminderPicker.js](../../static/js/features/notes/ui/reminderPicker.js) |
| A | [static/js/features/notes/ui/selection.js](../../static/js/features/notes/ui/selection.js) |
| A | [static/js/features/notes/ui/text.js](../../static/js/features/notes/ui/text.js) |
| A | [static/js/features/notes/ui/toolbar.js](../../static/js/features/notes/ui/toolbar.js) |
| A | [static/js/features/notes/ui/windowLayout.js](../../static/js/features/notes/ui/windowLayout.js) |
| A | [static/js/shared/ui/confetti.js](../../static/js/shared/ui/confetti.js) |

## Compatibility and deployment configuration (5)

| Status | File |
| --- | --- |
| M | [.gitattributes](../../.gitattributes) |
| M | [frontend/modules.json](../../frontend/modules.json) |
| M | [static/js/compare/vote.js](../../static/js/compare/vote.js) |
| M | [static/js/notes.js](../../static/js/notes.js) |
| M | [static/sw.js](../../static/sw.js) |

## Tests (21)

| Status | File |
| --- | --- |
| A | [tests/browser/notesNative.mjs](../../tests/browser/notesNative.mjs) |
| A | [tests/browser/notesOffline.mjs](../../tests/browser/notesOffline.mjs) |
| A | [tests/browser/notesReference.mjs](../../tests/browser/notesReference.mjs) |
| A | [tests/fixtures/notesRecurrenceBaseline.mjs](../../tests/fixtures/notesRecurrenceBaseline.mjs) |
| A | [tests/helpers/notes_source.py](../../tests/helpers/notes_source.py) |
| A | [tests/notesApplication.test.mjs](../../tests/notesApplication.test.mjs) |
| A | [tests/notesCharacterization.test.mjs](../../tests/notesCharacterization.test.mjs) |
| M | [tests/notesDraftAutosave.test.mjs](../../tests/notesDraftAutosave.test.mjs) |
| A | [tests/notesInfrastructure.test.mjs](../../tests/notesInfrastructure.test.mjs) |
| A | [tests/notesRecurrence.test.mjs](../../tests/notesRecurrence.test.mjs) |
| A | [tests/notesReminders.test.mjs](../../tests/notesReminders.test.mjs) |
| A | [tests/notesWorkflows.test.mjs](../../tests/notesWorkflows.test.mjs) |
| M | [tests/selectedNotesContext.test.mjs](../../tests/selectedNotesContext.test.mjs) |
| M | [tests/test_notes_document_mutual_minimize.py](../../tests/test_notes_document_mutual_minimize.py) |
| M | [tests/test_notes_dom_xss_helpers.py](../../tests/test_notes_dom_xss_helpers.py) |
| M | [tests/test_notes_mobile_checklist_toggle_static.py](../../tests/test_notes_mobile_checklist_toggle_static.py) |
| A | [tests/test_notes_reference_browser.py](../../tests/test_notes_reference_browser.py) |
| A | [tests/test_notes_reference_contracts_js.py](../../tests/test_notes_reference_contracts_js.py) |
| M | [tests/test_notes_search_reset_on_reopen_js.py](../../tests/test_notes_search_reset_on_reopen_js.py) |
| M | [tests/test_notes_select_esc_listener_js.py](../../tests/test_notes_select_esc_listener_js.py) |
| M | [tests/test_notes_sidebar_new_note.py](../../tests/test_notes_sidebar_new_note.py) |

## Runtime validation (2)

| Status | File |
| --- | --- |
| M | [scripts/verify_frontend_runtime.py](../../scripts/verify_frontend_runtime.py) |
| A | [scripts/verify_notes_browser_runtime.py](../../scripts/verify_notes_browser_runtime.py) |

## Documentation and execution receipts (9)

| Status | File |
| --- | --- |
| A | [docs/frontend-architecture/evidence/notes/implementation.sha256](../../docs/frontend-architecture/evidence/notes/implementation.sha256) |
| A | [docs/frontend-architecture/evidence/notes/native-desktop.png](../../docs/frontend-architecture/evidence/notes/native-desktop.png) |
| A | [docs/frontend-architecture/evidence/notes/native-mobile.png](../../docs/frontend-architecture/evidence/notes/native-mobile.png) |
| M | [docs/frontend-architecture/handbook.md](../../docs/frontend-architecture/handbook.md) |
| A | [docs/frontend-architecture/notes-case-study.md](../../docs/frontend-architecture/notes-case-study.md) |
| A | [docs/frontend-architecture/notes-changed-files.md](../../docs/frontend-architecture/notes-changed-files.md) |
| A | [docs/frontend-architecture/notes-module-inventory.md](../../docs/frontend-architecture/notes-module-inventory.md) |
| M | [docs/frontend-architecture/notes-roadmap.md](../../docs/frontend-architecture/notes-roadmap.md) |
| A | [docs/frontend-architecture/notes-validation.md](../../docs/frontend-architecture/notes-validation.md) |

See [the case study](notes-case-study.md) for ownership and handoff,
[the module inventory](notes-module-inventory.md) for line counts, and
[the validation report](notes-validation.md) for exact executed checks.
