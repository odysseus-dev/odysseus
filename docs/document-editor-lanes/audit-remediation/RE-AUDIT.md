# Independent re-audit handoff

Claude and Codex should each perform a fresh read-only review and return their own verdict. This remediation was implemented and checked by the remediation team; no independent approval is claimed.

## Identity and diff

```sh
cd /home/alteixeira20/Projects/Odysseus/worktrees/document-editor-remediation
git branch --show-current
git rev-parse HEAD
git status --short
git log --oneline 032d98575f5a7492e2377089cbc68f148753dd64..HEAD
git diff --stat 032d98575f5a7492e2377089cbc68f148753dd64..HEAD
git diff 032d98575f5a7492e2377089cbc68f148753dd64..HEAD -- frontend/src/features/documents static/js/document.js static/sw.js
```

Expected branch: `fix/document-editor-audit-remediation`. Final implementation checkpoint: `433a4d29e817322396c4fe09f4172d8bb3089cfb`. Later changes are the remediation report and receipts. Pin the actual final branch tip before auditing. The working tree should be clean.

Read both original reports at the absolute paths and checksums in README.md. Read this report, `reference-disposition.json`, `global-consumers.json`, the original final audit package, and the exact amendments in `verifier-amendments.json`. Use 087e6c88 for pre-extraction behavioral comparison; f3a05c10 already includes inherited extraction defects.

## Critical review boundaries

1. Verify both persistence capture moments reach the presenter's shared pane ownership guard, and stale panes preserve cached metadata and drafts.
2. Inspect document IDs, record/pane identity and cleanup across title timers, uploads, language PATCHes, AI creation/finalization and PDF delete rollback. Confirm successful deletion suppresses queued writes.
3. Invoke all six public email functions through the real facade, then submit a normal chat using the real chat module. Inspect actual outgoing requests.
4. Drive real envelope listeners, clearing/restoration, independent local-draft timers, explicit clear cancellation, and recipient focus across delayed body setup. Check older save responses cannot acknowledge later edits.
5. Review selector transitions, comparison and delayed preview after accepting changes, divider collapse, mobile grabber and touch dismissal. Distinguish synthetic events from a physical device.
6. Inspect image observer ownership, writable selection compatibility, stream reset and repeated rich text/email/PDF panel lifetimes. No importer may assign an imported binding.
7. Review the 16 internal owner-export additions and the removal of host/global bridges. Root named exports remain 30 and the default object remains 39 properties.
8. Review the validation adapter as code. It must reject any unrecorded body/export/state change. The old canonical manifest is frozen and is expected to reject behavioral fixes. The amended run uses the original canonical engine, exact replacements and hashes; top-level statement reconciliation uses the same engine comparator and retains its original diagnostic evidence.
9. Verify atomic conditional title updates in `routes/document/document_routes.py` (`patch_document`) and `routes/document/document_helpers.py` (`DocumentPatch`) when `if_untitled: true` is requested by auto-title in `ui/documentActions.js`.
10. Verify scheduled email completion in `ui/email/sendWorkflow.js` (`_scheduleSend`) clears local draft only for originating emails with `sourceUid` via `_clearEmailLocalDraft`, avoiding unsafe DOM fallback draft clearing across navigation.

## Reproducible checks

Node resolves Playwright and Acorn from the established local dependency installation. Python dependencies were isolated without changing the existing repository venv. The exact executed commands and expanded 148-file selection are preserved in `validation-results.json`.

```sh
node --experimental-loader ./tests/helpers/document_email_host_loader.mjs --test tests/document*.test.mjs
node scripts/check_document_references.mjs
node scripts/verify_document_remediation.mjs
node scripts/check_frontend.mjs
python3 scripts/build_frontend.py --check
node --test tests/frontendArchitecture.test.mjs tests/documentReferences.test.mjs tests/documentRemediationVerifier.test.mjs tests/markdownImages.test.mjs tests/selectionHighlights.test.mjs
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q tests/test_document_audit_remediation.py tests/test_document_chat_send_remediation.py tests/test_document_references.py tests/test_document_source_test_hygiene.py tests/test_frontend_module_graph.py tests/test_document_active_restore.py tests/test_document_rich_text_tools.py tests/test_document_module_api.py tests/test_document_state_contract.py tests/test_document_session_contract.py tests/test_document_rich_editor_contract.py tests/test_document_source_editor.py tests/test_document_email_workflows.py tests/test_document_title_conditional_patch.py
git diff --check 032d98575f5a7492e2377089cbc68f148753dd64..HEAD
```

`tests/test_document_audit_remediation.py` and `tests/test_document_chat_send_remediation.py` start the established pytest ephemeral static server (`ODYSSEUS_TEST_STATIC_ORIGIN`) and execute the Chromium behavioral suites under `tests/documentAuditRemediation.mjs` and `tests/documentChatSendRemediation.mjs`. `tests/test_document_references.py` exercises `scripts/check_document_references.mjs`, `tests/documentReferences.test.mjs`, and portable `tests/documentRemediationVerifier.test.mjs` modes.

If Playwright or pytest dependencies are run outside the active repository venv, install matching versions in a private environment without altering global or repository venv configuration. Use the installed Chromium executable when needed.

Canonical controls remain external and unchanged:

```sh
cd /home/alteixeira20/Projects/Odysseus/audit-artifacts/tools/document-extraction-verifier
node tests/run-all-tests.mjs
```

The adapter defaults to that canonical tool relative to the worktree collection. `DOCUMENT_EXTRACTION_VERIFIER_ROOT` can point to the same frozen verifier snapshot in another location. Review baseline and amendment hash pins before using a different copy.

## Limits and next actions

The isolated document HTTP smoke used a temporary loopback app and temporary database/auth/data (process stopped and temporary runner removed during PR cleanup). Other release-smoke areas were not run. Real email providers, AI providers, physical devices, Firefox/WebKit, offline cache upgrade and maintainer manual QA remain unverified.

A photo-editor cut test fails identically against untouched 032d9857 assets. Its isolated baseline failure and current failure are recorded separately. It is outside the Document Editor remediation and was not changed or skipped.

Return verified defects with exact revision, source coordinates, reproduction, observed request/state and severity. Treat missing evidence separately. Do not approve based only on the structural gate or this report. No merge, push, GitHub mutation, production access or publication is authorized by this handoff. Independent verification must precede integration or publication.
