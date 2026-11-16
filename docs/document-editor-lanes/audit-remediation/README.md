# Document Editor audit remediation

Status: COMPLETE, READY FOR INDEPENDENT RE-AUDIT. All Document Editor blockers are corrected. Final validation is recorded in `validation-results.json`. This implementation review is not an independent audit.

## Scope and revisions

- Historical remediation worktree/branch (checkpoint `433a4d29`): `document-editor-remediation` / `fix/document-editor-audit-remediation` (subsequent PR `#7292` commits continue on `refactor/document-editor-decomposition`).
- Original audited HEAD: `032d98575f5a7492e2377089cbc68f148753dd64`, preserved unchanged.
- Original decomposition comparison: `f3a05c10c998e4efff14ac6498b01bd50138ad6e`.
- Original 565-item accounting and behavioral reference: `087e6c8837969339e9e7823dd84a4d717d536cea`. The supplied f3a05c10 revision already contains earlier extraction waves, including inherited defects.
- Final implementation HEAD: `433a4d29e817322396c4fe09f4172d8bb3089cfb`. The final documentation commit contains this report and verification receipts. The final remediation HEAD is the tip of `fix/document-editor-audit-remediation`; obtain its exact SHA with `git rev-parse fix/document-editor-audit-remediation`. The completion response records that final commit explicitly.
- No push, merge, PR, GitHub mutation, deployment, or production access occurred. Other worktrees and the preserved Lane C stash were not modified.

Original reports remain unchanged:

| Auditor | Report | SHA-256 |
| --- | --- | --- |
| Claude | `../../audit-artifacts/document-editor-claude-independent-audit-20261115.md` (external audit artifact dir) | `098db1787aeffd0433016f65fb1728ddb6068e5cc3b9daf4434d3bffcfa66b7c` |
| Codex | `../../audit-artifacts/codex-document-editor-runtime-audit-032d985.md` (external audit artifact dir) | `5a14bccd6361d8f27ea3656c3ac68c4e03701a916da9916751446f5591bdea04` |

The historical final audit package remains a snapshot. Its claims describe 032d9857, not this remediation. The canonical decomposition plan, integration ledger, 16 original persistence-hook dispositions, testing standard, and both independent reports were examined before remediation.

## Blocking finding disposition

Paths in this table are under `frontend/src/features/documents/` unless stated otherwise. Every maintained change has its matching published copy under `static/js/document/`.

| Finding and original severity | Root cause and confirmed impact | Provenance | Correction and changed files | Behavioral regression and result | Local commit |
| --- | --- | --- | --- | --- | --- |
| Codex F01, P1 | Both persistence capture moments trusted active identity without proving pane ownership. Reproduced an actual empty PUT for A while B's fresh pane mounted. A's title and language also changed to pane defaults. | Inherited mechanism in supplied baseline | `ui/panel/documentPresenter.js`: reject snapshots unless pane `activeDocumentId` matches the active document. Both existing capture calls in `application/persistence.js` pass through this shared boundary. Cached draft object, queue-turn selection and revision acknowledgement remain intact. | Four rapid minimize/open cycles; held outgoing writes; body, title, language and dirty state retained; PUT payloads target A with A's latest content; B unchanged. PASS. | `aa59784b` |
| Codex F03, P1; Claude B2 subset, P1 | Extracted envelope listeners lacked capture, preview and header collaborators. Actual To input threw before draft persistence. Envelope edits also failed to increment the revision. | Introduced by Lane C extraction; adjacent local-draft gaps inherited | `ui/email/composerWiring.js`, `ui/email/localDraft.js`, `ui/email/composerFields.js`, `ui/panel/panelLifecycle.js`: explicit imports, dirty revision, owned snapshots, independent storage-key timers and explicit cancellation. Deferred body focus preserves newer recipient focus. Empty CC/BCC are retained when restoring. Pane disposal cannot cancel another draft's write. | Real To/CC/BCC/Subject input, clear controls, outgoing PUTs, storage/reload, overlapping response acknowledgement, rapid independent drafts, explicit clear cancellation, delayed body focus, Markdown keyboard shortcut. PASS. | `02b59c04`, `e6ac9f3a`, `e827ef51`, `48a9bb08` |
| Codex F07, P2 | Source timer retained A's text but chose the active ID at execution. Markdown image timers had a separate lifetime and survived disposal. | Introduced by timer repair during decomposition | `ui/editor/sourceEditor.js`, `ui/editor/markdownImages.js`: one timer owner; capture text and document ID; guard view work; cancel timers on disposal. Inactive A may receive its own valid title; disposed image work is cancelled. Upload completion also validates its original pane and ID. | A-to-B navigation targets PATCH A and leaves B's title unchanged; image insertion then immediate disposal/reopen produces no stale title PATCH; reinitialized editor can title normally; held upload cannot insert into B. PASS. | `85251f95`, `b80d2119` |
| Codex F08, P2 | Delete cancelled the PDF timer; after disposal rollback rescheduled a view operation with no DOM references. Reproduced dirty captured content with zero recovery PUTs. | Inherited | `ui/documentFlows.js`: capture PDF state before cancellation; recover from `docs` or `_closingDocs` with document autosave. Preserve delete barriers, options, queue ordering and tombstones. Do not await a save inside its own delete task. | Mounted, minimized, tab navigation, detached tab and session switch failure cases issue the captured PDF PUT. Successful deletion produces no recovery PUT. PASS. | `d9d106a7`, `e827ef51` regression expansion |
| Codex F02, P1; Claude B1, P0 | Six root wrappers overwrote global implementations and recursively resolved themselves. Confirmed impact includes the composer-context call on normal chat submission. | Inherited from Wave 5 / supplied f3a05c10 baseline | `static/js/document.js`, `ui/email/emailDocuments.js`, `ui/email/emailBody.js`: direct stable owner exports, no implementation lookup through overwritten globals. Supply draft/session/context dependencies and await draft injection before resolving open. Remaining facade methods also import their owners directly. | All six methods invoked through named and default surfaces; actual chat module saves linked content then sends `/api/chat_stream`; no page errors. PASS. | `e6ac9f3a`, `b80d2119` |
| Codex F04, P2; Claude B2 subset, P1 | Language selector omitted PDF, preview and header dependencies, aborting real format changes. Metadata completion also read a reused selector and active ID after await. | Missing callbacks introduced by Lane C; asynchronous metadata mechanism inherited | `ui/header/languagePicker.js`, `ui/documentActions.js`: import owners; capture request ID and value, check response status, and preserve later selections and other documents. | Real Python, rich text, Markdown, DOCX, HTML, CSV and email transitions; deferred A PATCH across B navigation; form-backed PDF/source selection preserves underlying Markdown metadata. PASS. | `e6ac9f3a` |
| Codex F05, P2; Claude B2 subset, P1 | Divider, grabber and swipe callbacks omitted the `closePanel` command. | Introduced by Lane C | `ui/panel/paneLayout.js`, `ui/header/headerActions.js`: use existing application command port. | Default divider collapse/reopen repeatedly; Chromium mobile grabber tap; synthetic TouchEvents in Chromium dismiss and preserve minimized dock state. Listener cleanup checked. PASS. Synthetic swipe is not physical-device evidence. | `e6ac9f3a` |
| Codex F06, P2; Claude B2 subset, P1 | Successful history response reached missing `enterDiffMode` and was reported as a history failure. | Introduced by Lane C | `ui/header/headerActions.js`: import diff viewer owner. | Actual compare button with valid two-version response activates diff and displays old and new content. PASS. | `e6ac9f3a` |
| Codex F09, P2; Claude B2 subset, P1 | Detector omitted `updateLanguage`; local selection changed without its PATCH. | Inherited source-editor extraction defect | `ui/editor/sourceEditor.js`, `ui/documentActions.js`: explicit language collaborator and captured request identity. | Shipped `highlight.min.js` and actual SVG detector; assert document language and actual PATCH. Existing programming-language unit tests now inspect the real request collaborator. PASS. | `85251f95`, `e6ac9f3a` |
| Claude B2 remainder, P1 | Missing session, spinner, icon and rich image collaborators; additional ambient imports concealed incomplete extraction dependencies. | Mixed: original reports attribute source extraction and Lane C sites separately | Email, rich editor, selection and status consumers now import owners. Rich image selection uses the planned `setImageSelectionObserver` seam; menus own toolbar positioning and disposal. See complete per-file identifier inventory. | Reference/import gate, rich editor and email suites, actual rich image selection/positioning/disposal, and broader selection. PASS. | `e6ac9f3a`, `b80d2119` |

## Additional findings and audit comments

| Finding and original severity | Examination and disposition | Coverage / commit |
| --- | --- | --- |
| Claude N1 P2; Codex N01 P3 | Writable `_selections` was a confirmed illegal imported-binding assignment. No production writer was found, but compatibility remains supported through `selectionHighlights.replaceSelections`; no imported assignment remains. | Actual global setter write, owner identity and repeated init; `e6ac9f3a` |
| Claude N2 P2 | All eight temporary host callbacks were traced and replaced with direct owner imports. Other extraction globals had no external consumers and were removed. A wider scan found `slashCommands.js` reads ambient `markdownModule`, so it is retained alongside `uiModule`, public `window.documentModule`, the supported selection setter and two inline email intent callbacks. | `global-consumers.json`, API, actual chat and browser lifecycle tests; `b80d2119` |
| Claude N3 P3 | Root reduced from 649 to 247 lines. No extracted business implementation was restored or duplicated. Existing import-time activation and init-time configuration remain at their established moments; idempotence is verified. | API and import-time behavioral characterization; `b80d2119` |
| Claude N4 P2 | Removed 1,181 unused imported names after dependency and bridge corrections. This includes the nine reported mass-import files and four adjacent consumers. There are zero remaining unused imports in the document module set according to the scope-aware scan. The original 1,141 was an earlier diagnostic count before bridge removal made additional imports unused. | `import-cleanup.json`, full graph/reference and runtime suites; `b80d2119` |
| Claude N5 P2 | Added a repeatable scope-aware Acorn gate for free names, import/re-export resolution and writes to imported bindings, including namespace properties. Curated browser globals and per-file guarded fallback exceptions are explicit. Added integrated behavior rather than existence-only API checks. | 12 reference controls, 6 amendment controls, Chromium suite and actual chat submission; `e6ac9f3a`, `b80d2119` |
| Claude N6 P3 | Removed both import-order self-assignments and internal ambient function dependencies. Facade methods use stable owner bindings. | Reference gate and facade execution; `e6ac9f3a`, `b80d2119` |
| Claude N7 P3 | Removed three mutable email stream aliases on globalThis. `resetEmailStreamState` owns cancellation and stream state reset. | Owner unit checks and composer browser behavior; `e6ac9f3a` |
| Claude N8 P3 | This report explicitly distinguishes 087e6c88 item accounting, f3a05c10 supplied decomposition comparison, 032d9857 audited baseline, and remediation changes. Original audit documents are preserved. | This report and re-audit handoff |
| Additional: AI pane ownership | AI creation/streaming did not stamp pane ownership; update could retain a textarea read before a new pane mounted. Populate the owned pane, restore its metadata and stamp the ID. Guard delayed preview/detection against a changed pane. This is required integration of F01's stronger boundary. | Actual AI-create and stream-finalize followed by manual editing and real PUTs; `b80d2119` |
| Additional: attachment and unread completion | Forwarded completion could schedule B's autosave; selected attachment loops could acquire another active draft; mark-unread completion could discard B. Capture initiating identity and preserve it through completion. | Held requests across navigation with payload/state checks; `b80d2119` |
| Additional: local storage lifetime | One debounce could cancel A's independent local draft when B was edited. Captured writes could recreate an explicitly cleared draft. The latter was exposed during remediation of F03; both are now owned and cancelled by storage key. | Both regressions reproduced red before correction, then passed; `e827ef51` |
| Additional: deferred diff preview | Accepting A's Markdown diff scheduled preview activation against whichever document was active at the next frame. The controlled-clock browser test reproduced B's preview flag changing. The callback now retains the accepting ID and mounted pane; it leaves B unchanged and still activates A when A remains open. | Actual accept-all action, immediate Python-document navigation, state and visible editor assertions; red before correction and green afterward; `433a4d29` |
| Additional: deferred composer focus | The inherited 50 ms body-focus callback could steal recipient input focus; a repeated integrated run observed BCC text entering the body. Capture the opening document/focus and leave newer focus choices intact. A controlled-clock regression reproduced the race before correction. | Recipient remains focused across delayed setup; full envelope events and reload continue to pass; `48a9bb08` |
| Candidate: listener growth | Transient visibility listeners came from the existing spinner's documented 2000 ms grace for a loading row removed before its first connected frame. No persistent leak was confirmed. Tests drain that bounded owner grace with the controlled clock and compare all document/window/visualViewport listener counts after repeated rich text, email and PDF disposal. | Repeated lifecycle browser case; no spinner source change |

## Systematic dependency and ownership evidence

`reference-disposition.json` enumerates 103 original file/name groups, comprising 67 unique identifier names, including guarded fallbacks and the imported setter write. Every non-platform unresolved dependency is corrected or explicitly classified. `global-consumers.json` records external consumer evidence; `import-cleanup.json` records removed import counts. These are derived evidence, not a replacement for source review.

The 16 intentional per-file fallback allowances remain only in pre-existing dependency-injected `pdfTools`, `previewModes` and `diffViewer`. They require a rationale and a matching `typeof` probe. Integrated callers supply init collaborators. The allowlist does not authorize new arbitrary globals. Browser facilities such as `Highlight`, `matchMedia`, DOM constructors and timers are separately classified as platform names.

The temporary callbacks in `ui/host.js` were classified individually. All were obsolete internal host detours after their modules landed; they had no non-document consumers in the recorded scan. Their current consumers use these existing owners:

| Removed temporary callback | Current owner | Representative consumer |
| --- | --- | --- |
| `_activeDocLanguage` | `ui/status/documentStatus.js` | `ui/editor/markdownImages.js` |
| `_closeVersionPanel` | `ui/history/versionHistory.js` | `ui/header/headerActions.js` |
| `_scheduleDocumentStats` | `ui/status/documentStatus.js` | `ui/richEditor/richBodyWiring.js` |
| `_setDocumentHistoryControlState` | `ui/status/documentStatus.js` | `ui/panel/documentPresenter.js` |
| `_syncEditorPlaceholder` | `ui/status/documentStatus.js` | `ui/documentFlows.js` |
| `_wireSwipeDismiss` | `ui/panel/paneLayout.js` | `ui/header/headerActions.js` |
| `toggleFullscreen` | `ui/panel/paneLayout.js` | `ui/toolbar/toolbar.js` |
| `toggleVersionHistory` | `ui/history/versionHistory.js` | `ui/tabs/tabBar.js` |

The remaining host exports are required internal integration interfaces to legacy UI, sessions, Markdown, library, rendering, menus and helpers. They are pure re-exports with their established query strings; they contain no global lookup or callbacks into the composition root. The root's `uiModule` and `markdownModule` globals are required external compatibility, as the recorded consumers show. `_selections`, `window.documentModule` and the two inline intent callbacks retain their supported compatibility behavior. The three email stream globals and all other temporary internal publications were unnecessary global dependencies and were removed after consumer tracing. No wrong-direction application/domain import of the host remains.

The fixed interfaces remain 12 view commands, 6 persistence hooks, and 1 image selection observer. The observer is now actually registered. Document map and record identities, serialized write barriers, dirty revisions, closing-draft retention and deletion tombstones remain in their owning modules. No imported binding writes, import cycles or layer/state ownership violations remain.

## Extraction verification and intentional amendments

Behavioral fixes cannot be verbatim copies of buggy baseline bodies. The frozen original canonical CLI with its historical removal manifest therefore reports expected body/export failures; that FAIL is recorded, not represented as a clean original-manifest pass.

`node scripts/verify_document_remediation.mjs` runs the unchanged canonical engine against the original hash-pinned 087e6c88 baseline with `verifier-amendments.json`. The amendments record exact replacements, corrected function hashes, 16 explicit owner exports and rationales. They are implementation changes requiring independent review, not independent approval.

All ledger, dependency, layer, state and port checks remain enabled. Export checks use the canonical export checker with the exact amended inventory. One canonical routing omission for top-level `stmt#11782` is handled with that engine's same exact statement comparator; only the proven matching diagnostics are reconciled, and the original diagnostics are retained in the receipt. Failed comparisons and unrelated failures remain failures. Negative controls reject unrecorded body changes, extra exports, incorrect hashes and unrelated state/body violations. Original external verifier code and audit reports were not modified.

Result: 565/565 items, 557 moved and 8 retained, zero missing or duplicates; clean graph, state ownership, exports and bounded ports. Final machine receipt: `validation/final-verifier.json`. The historical 16 hook substitutions were re-examined through current source and joined lifecycle regressions. The engine's current exception count can differ because exact recorded function amendments supersede earlier automatic rewrite classifications; it is not a waiver of persistence behavior.

## Validation and limits

The exact commands, expanded selections, exit codes, output tails and statuses are in `validation-results.json`. Final source-dependent gates identify implementation HEAD and confirm zero tracked source changes when started. The two temporary runners are preserved in `validation/` for reproducibility; paths in them describe this checkout and isolated environment. Counts overlap; do not add wrapper, nested browser and broader-suite totals.

Final executed gates:

| Status | Check | Result |
| --- | --- | --- |
| PASS | Document Python/browser-backed suite | 403 passed, exit 0 |
| PASS | Document Node suites | 132 passed, 0 failed/skipped, exit 0; includes 12 reference and 6 amendment controls |
| PASS | Integrated Chromium remediation suite | 48 actual browser cases; separate real chat submission passes |
| PASS with SKIPPED cases | Broader decomposition G3 selection, 148 files | 1,030 passed, 3 skipped, exit 0 |
| SKIPPED | G3 optional cases | `fitz` unavailable; `openpyxl` unavailable; opt-in Ajax fixture not enabled |
| PASS | Canonical verifier controls | 25/25, unchanged external tool |
| PASS | Canonical engine with exact remediation amendments (historical `433a4d29` snapshot; later PR commits keep 565/565 passing across 59 document modules) | 565/565; zero missing, duplicates, cycles, layer or unauthorized state violations; 55 modules / 372 import edges at `433a4d29` (expanded to 59 modules later); 12 commands, 6 hooks, 1 observer |
| FAIL, expected historical manifest mismatch | Frozen canonical CLI with original manifest | Rejects intentional body/export corrections; full diagnostic summary recorded in `validation/original-manifest-result.json` |
| PASS | Frontend Node architecture plus image/selection wrappers | 12 passed, exit 0 |
| PASS | Frontend Python build/offline/module/parity/panel and CodeQL security selection | 42 passed, exit 0 |
| PASS | Reference/import gate; frontend checker; build parity (`433a4d29` snapshot; later PR commits maintain zero stale published modules across 102 maintained modules) | Zero diagnostics; 98 maintained modules at `433a4d29`; zero stale published modules |
| PASS | Document module publication inventory (`433a4d29` snapshot; later PR commits expand to 59 document modules) | All 55 maintained files mirrored, registered and precached at `433a4d29`; root API remains 30 named / 39 default properties |
| PASS | Whitespace | `git diff --check` and staged diff checks clean |

Isolated live backend: document create/edit/version/restore/delete smoke passed, 1 test, using an ephemeral loopback app, temporary database/data/auth and an owned process stopped afterward. The other 11 release-smoke areas were NOT RUN.

Python Playwright 1.62.0 was installed in `/tmp/document-editor-remediation-venv`, with the existing repository dependency directory referenced only from that temporary environment. No global or existing venv configuration changed. The prior missing-package limitation is resolved for these checks.

An unrelated photo-editor test, `test_cut_placed_image_rasterizes_once_without_confirmation`, fails with `Resulting promise was garbage collected`. The full file executed 62 passing tests and this one failure. The same test fails against a Git archive of untouched 032d9857 static assets and its untouched test file. This is a confirmed baseline limitation outside this mission; it was not suppressed or changed. Both command results are recorded.

The full broad fast lane was collected, with 17,882 selected of 17,887 nodes and 5 deselected, but not executed in full. `unverified-fast-tests.txt.gz` identifies 16,768 selected pytest nodes outside the recorded executed selections. `coverage-inventory.json` lists those executed selections and the five slow nodes deselected by the fast lane. Read the exact unverified node list with `gzip -dc unverified-fast-tests.txt.gz`. Optional G3 skips retain their reported reasons. No skip/xfail was added to hide a failure.

NOT RUN: physical mobile devices, Firefox/WebKit, real SMTP/IMAP and AI providers, offline service-worker cache upgrade, exhaustive manual QA and the remaining release-smoke areas. Synthetic Chromium touch events are not device validation. Frontend publication here means local maintained/published parity only; no deployment or remote publication occurred.

## Post-2accadfa re-audit remediation delta

| Source finding | Defect / gap | Corrective owner change | Chromium & automated proof | Commits |
| --- | --- | --- | --- | --- |
| Codex R02, P2 | Delayed `/versions` response in Compare Changes could activate diff mode after navigation/disposal and persist A content to B on exit; `enterDiffMode` errors were reported as history fetch failures. | `ui/header/headerActions.js` (`openPanel#7527`): capture doc ID, record, pane, navigation generation, and request sequence; validate owner before error reporting and before `enterDiffMode`; separate fetch errors from diff init errors. | 6 Chromium exit paths (`accept-all`, `reject-all`, `toggle-off`, `navigate-c`, `minimize`, `escape`), `A -> B -> A` invalidation, normal compare, and error separation in `tests/documentAuditRemediation.mjs`. PASS. | `1ffc775b` |
| Codex R01, P2 | Rich-text input debounced auto-title on `rich._autoTitleTimer` without capturing doc identity, read `rich.innerText` lazily, defaulted to `activeDocId`, and survived pane disposal. | `ui/editor/sourceEditor.js`, `ui/richEditor/richBodyWiring.js`: export `scheduleAutoTitle` sharing `_autoTitleDebounce` and `disposeSourceEditor` cleanup; capture doc ID, record, and text synchronously with connected-pane check. | Rich->Markdown, Rich->Rich, minimize->B, rapid switch/removal, pane disposal, repeated remount, and normal rich auto-title in `tests/documentAuditRemediation.mjs`. PASS. | `1ffc775b` |
| Claude NB8, P3 | AI-created documents without explicit title temporarily showed `Untitled` after switching away and back because `autoTitleFromContent` ran before `setActiveDocId`/`titleInput` setup and `updateTitle` did not sync `titleInput`. | `ui/aiUpdates/documentUpdate.js`, `ui/documentActions.js`: order `setActiveDocId` and pane ownership stamp before deriving auto-title; check PATCH status, record identity, and sync empty/Untitled `#doc-title-input` in `updateTitle`. | Chromium regression verifying AI-created doc with and without closed panel retains derived title across `prev` tab round-trips in `tests/documentAuditRemediation.mjs`. PASS. | `1ffc775b` |
| Claude NB1, NB2, NB3, NB4, NB5, NB6, NB9 | Reference check not in CI/pytest; compat suppressions were file-wide; unused imports not failed; verifier unit test used absolute user path; publication set needed explicit test. | `scripts/check_document_references.mjs`, `.github/workflows/ci.yml`, `tests/documentReferences.test.mjs`, `tests/documentRemediationVerifier.test.mjs`, `tests/test_document_references.py`, `tests/documentAuditRemediation.mjs`: per-use-site AST `typeof` guards, `unused-import` enforcement, negative-control fixture, portable verifier path with skip when external tool absent, Chromium global publication assertion, CI/pytest integration. Retained `_emailLocalDraftDebounce` (NB5) for Appendix A baseline export parity (`pendingLocalDraftWrites` owns per-key timers). Recorded amendment behavioral notes (NB6). | All reference checker, verifier, architecture, and Chromium facade checks PASS. | `1ffc775b`, `28976a0a` |

### Verifier amendment behavioral notes (NB6)

- `scheduleAutoTitle` (`ui/editor/sourceEditor`): added as an approved Appendix A export amendment so rich-text input and source/Markdown-image editing share a single `_autoTitleDebounce` owner cancelled by `disposeSourceEditor`.
- `openPanel#7527` (`ui/header/headerActions`): wraps the listener registration in an `if (diffToggleBtn) { ... }` block holding a closure-local `_compareRequestSeq` counter, preserving single-`IfStatement` AST parity with the extracted statement while rejecting superseded or navigated-away responses.
- `updateTitle` (`ui/documentActions`), `_wireEmailRichbody` (`ui/richEditor/richBodyWiring`), and `handleDocUpdate` (`ui/aiUpdates/documentUpdate`): amended with exact source replacements and SHA-256 pins; runtime tests verify record identity and UI synchronization across await and tab switching.

## Final acceptance audit remediation delta (b0a6e333..HEAD)

| Finding and severity | Problem and impact | Corrective owner change | Automated proof |
| --- | --- | --- | --- |
| NB-F1, P3 | Stale auto-title PATCH response arriving after manual title edit overwrote `doc.title` in memory and tab label, and could persist stale title data out of order. | `ui/documentActions.js` (`updateTitle`, `autoTitleFromContent`): cancel in-flight title requests with `doc._titleAbortController.abort()`, assign monotonic sequence `doc._titleRequestSeq`, pass `{ isAutoTitle }` option, drop stale responses when superseded, and guard title input against overwriting non-untitled values. | Chromium regression in `tests/documentAuditRemediation.mjs`: auto-title held while user edits title manually, manual update succeeds, held auto-title released, title and tab remain manual; also covers document navigation, failed PATCH, and repeated edits. PASS. |
| NB-F1-Server, P3 | Stale auto-title PATCH arriving at backend database after manual title edit committed could overwrite title in database because PATCH handler updated title unconditionally. | `routes/document/document_helpers.py` and `routes/document/document_routes.py`: added atomic conditional title update support via `if_untitled: Optional[bool] = False` and `expected_title: Optional[str] = None`. When `if_untitled` is true, SQL UPDATE updates only if `(title = 'Untitled' OR title IS NULL OR title = '')`; if 0 rows updated, raises HTTP 409 Conflict. In `ui/documentActions.js` (`updateTitle`), sends `{ title, if_untitled: true }` on auto-title and handles 409 silently. | Deterministic suite in `tests/test_document_title_conditional_patch.py` (6 tests: untitled auto-title, manual title replacement, auto-title 409 rejection after manual title, delayed auto-title race simulation, multi-threaded separate-session concurrency, and legacy client backward compatibility). Updated Chromium suite in `tests/documentAuditRemediation.mjs`. PASS. |
| NB-F2, P3 | `_saveDraft` read `docs.get(activeDocId)` after awaiting asynchronous draft save, leaking draft UID and folder metadata into an unrelated active document if user navigated away during save. | `ui/email/sendWorkflow.js` (`_saveDraft`): capture initiating document identity (`docId = activeDocId`, `doc = docs.get(docId)`), read draft UID/folder/attachments from captured `doc`, and update only when `savedDoc === doc`. | Chromium regression in `tests/documentAuditRemediation.mjs`: save draft A held, user navigates to doc B, release response, doc B receives no metadata, doc A receives metadata; also covers deletion during save, normal save, and failure. PASS. |
| NB-F3, P3 | `_scheduleSend` called `_closeWithoutDeleting(true)` on `data.success`, closing `activeDocId` instead of the scheduled email if the user switched tabs while scheduling. | `ui/email/sendWorkflow.js` (`_scheduleSend`): capture `sendDocId` and `doc`, clear draft for captured email, and cleanly delete/close only `sendDocId` when still matching `doc` without touching or closing newly active document across navigation. | Chromium regression in `tests/documentAuditRemediation.mjs`: schedule email A held, user navigates to doc B, release response, doc B remains open and active, email A is deleted/closed; also covers normal schedule without navigation and failed schedule. PASS. |
| NB-F3-Edge, P3 | `_scheduleSend` called `_clearCurrentEmailLocalDraft()` as fallback when `sourceUid` was empty (fresh compositions), deleting active DOM reply draft in localStorage if user navigated to a reply while scheduling. | `ui/email/sendWorkflow.js` (`_scheduleSend`): removed the unsafe fallback call to `_clearCurrentEmailLocalDraft()`. Fresh compositions do not persist reply drafts to localStorage. Draft clearing strictly requires captured originating email `sourceUid` via `_clearEmailLocalDraft(sourceUid, sourceFolder, inReplyTo || '')`. Removed unused import `_clearCurrentEmailLocalDraft`. | Chromium regression in `tests/documentAuditRemediation.mjs`: fresh composition A scheduled while navigating to reply B; reply B localStorage draft remains intact; email A is deleted; scheduling an actual reply clears its own localStorage draft. PASS. |
| NB-F4, P4 | Retained `_emailLocalDraftDebounce` in `ui/email/localDraft.js` is verified as required by Appendix A baseline extraction accounting (item #116 in canonical ledger). | Justified retention: `pendingLocalDraftWrites` owns actual per-key timers, while `_emailLocalDraftDebounce` mirrors the active timer for extraction catalog compatibility. | Canonical verifier (565/565 items passed) and Node/Chromium suites pass cleanly. |
| NB-F5, P4 | Retention of `_markUnreadAndClose` guard (`if (activeDocId === docId && docs.get(docId) === doc) _discardEmail()`) in `ui/email/sendWorkflow.js` is verified as safe fail-closed behavior preventing cross-document closure. | Confirmed safe design: closing is guarded so navigating away during mark-unread preserves the newly active document without discarding it. | Existing browser test and canonical hash-pinned amendment pass cleanly. |

## Local commits and final state

| Commit | Checkpoint |
| --- | --- |
| `aa59784b` | Pane ownership before snapshots |
| `02b59c04` | Email envelope persistence and explicit collaborators |
| `85251f95` | Document-owned delayed source/image work |
| `d9d106a7` | PDF delete rollback after disposal |
| `e6ac9f3a` | Facade, missing dependencies, owner setters, reference gate and public interactions |
| `b80d2119` | Stable owner interfaces, bridge/import cleanup, AI/attachment ownership and verifier amendments |
| `e827ef51` | Independent local draft persistence/cancellation and strengthened lifecycle regressions |
| `48a9bb08` | Preserve recipient focus across delayed composer setup |
| `433a4d29` | Retain diff acceptance preview ownership across navigation |
| `2accadfa` | Documentation and re-audit handoff at prior checkpoint |
| `1ffc775b` | Guard async version compare, rich-text auto-title, AI title sync, and Chromium regressions |
| `28976a0a` | Enforce reference checker, per-site compat guards, and portable verifier tests in CI/pytest |
| `b0a6e333` | Documentation of R01, R02, NB8, and CI remediation details |
| `26e2b7fe` | Guard async title sequencing and email draft/schedule navigation races |
| `c393117a` | Documentation of final acceptance findings remediation and regression proofs |
| `5719f0f0` | Atomic conditional backend title updates and safe scheduled draft cleanup |

At checkpoint `433a4d29`, implementation changes were confined to document modules and their published copies (55 maintained/published document modules at that checkpoint), the compatibility facade, local service-worker cache key, focused validation scripts, tests and remediation documentation. Subsequent PR `#7292` commits expanded the document feature set to 59 maintained/published modules with 102 maintained frontend modules overall, updated the service-worker cache key to `odysseus-v1148-pr40-editor-precache`, and applied CodeQL security and CI compatibility remediations while preserving exact manifest/precache coverage and 30 named / 39 default root API properties. See `changed-files.txt` for the historical `433a4d29` checkpoint summary.

Remaining confirmed Document Editor findings: none. The re-audit must independently assess the behavioral corrections, explicit ownership interfaces and verifier amendments. Clean final worktree and exact final tip are checked after the documentation commit and reported in the completion response.

Re-audit instructions: [RE-AUDIT.md](RE-AUDIT.md).
