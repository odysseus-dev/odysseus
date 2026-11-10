# Notes reference decomposition roadmap

Status: N0 through N6 implemented as an unpublished Notes review diff after F1
merged in PR #6701. Actual decisions, interfaces, module ownership, checkpoint
receipts and execution limits are in the [case study](notes-case-study.md) and
[validation report](notes-validation.md). The proposal below retains the original
responsibility map for comparison. Notes integration and the recipient's independent
H reproduction remain pending.
F1 remains an entirely separate foundation-only PR. The complete Notes
decomposition, regression tests, compatibility validation and final reference
case study belong to one comprehensive draft PR targeting official `dev`.

## Verified current responsibility and compatibility map

Historical assessment baseline: `8feb50e89468235cd3e0986da80edbd3acaf2c40`.
The implementation starts from merged F1 at
`1cc56cb9d78f3e2399d1d61155b71d771a17b85b`.
The module is `static/js/notes.js`, about 5,470 lines, with browser identity
`/static/js/notes.js?v=20260911notesselectioncancel1`. Preserve that identity across
the app import, HTML, SW and lazy consumers until a separately reviewed migration
changes the contract everywhere.

| Responsibility | Current evidence | Intended owner after its reviewed slice |
| --- | --- | --- |
| State and CRUD | `_notes`, selection/filter/editing/loading variables, `_undoStack`, `_fetchNotes`, `_saveNote`, `_patchNote`, `_archiveNoteById` | One application state owner and workflow operations; no second reminder/UI copy |
| Dates and recurrence | `_normalizeRepeat`, `_advanceRecurring`, `_nthWeekdayOfMonth`, `_lastWeekdayOfMonth`, `_toLocalDatetimeStr`, repeat formatting helpers | Pure domain calculations with explicit clock inputs |
| Network and persistence | Notes CRUD/reorder, upload, reminder firing, draft and reminder localStorage keys | Infrastructure adapters preserving request payloads, credentials and error contracts |
| Reminders | `_checkReminders`, `_startReminderLoop`, `_initReminders`, notification permission/display and due/fired badge calculations | Application reminder workflow plus injected clock/storage/notification/API ports |
| Draft autosave | `_draftKey`, `_collectFormDraft`, `_wireDraftAutosave`, `_applyDraftToNote`, `_commitOpenInPlaceEditor` | Draft workflow with editor view/persistence ports and serialized saves |
| UI | `openPanel`, `closePanel`, `_renderNotes`, forms/cards/checklists, masonry, color/photo/drawing, mobile swipe/reorder | Scoped UI modules with explicit mount/dispose and application operations |
| Agent context | `#notes-send-agent` handler, selected IDs, `odysseus:notes-context-selected` | Preserve event payload and synchronous acknowledgement; Chat owns attachment/session state |
| Agent solve action | `_agentSolveNote`: default-chat lookup, session creation and chat stream | Separate existing workflow; do not conflate with selected-note attachment |
| Compatibility coordinator | Default controller, named aliases, `window.notesModule`, delayed reminder startup | Stable legacy facade/composition bridge until consumers safely migrate |

The default API currently includes `openPanel`, `closePanel`, `togglePanel`,
`isPanelOpen`, `openNote`, `newNote`, `openNotes`, `closeNotes`, `isNotesOpen` and
`refreshDueBadge`. Named exports also include `dismissFiredReminderDot` and the
panel/open aliases. Characterization must pin the complete actual namespace and
default object separately, including identity of aliases and the window bridge.

Consumers include `static/app.js` sidebar/routes/badge polling,
`chatStream.js` note tool updates, `chatRenderer.js` note links and
`document.js` coordination. `static/index.html` and `static/sw.js` are entry/cache
contracts. The shared UI, confetti, modal, color picker, drag, tiling, docking,
z-order and Escape helpers are direct dependencies. Those legacy dependencies
remain in the coordinator until their ownership is reviewed, rather than moving
all UI coupling into domain modules.

Import-time behavior includes window origin/storage reads and
`setTimeout(_initReminders, 3000)`. The successful bootstrap loads Notes and starts
a 30-second reminder interval. App composition separately refreshes the badge
every five minutes. Panel/editing listeners, draft timers, highlight animations,
ResizeObserver and drawing/touch handlers have shorter scopes. Record them before
extraction; do not stop background reminders merely because a panel closes.
Listener cleanup concerns require characterization, not an unrelated fix hidden
inside an extraction.

Persistence includes `odysseus-notes-view`, label collapse and hint preferences,
fired/glowed/pending/active reminder markers, dismissed timestamp, and
`odysseus-note-draft-<id>` including `__new__`. Current direct APIs include Notes
CRUD/reorder/fire-reminder and upload, plus default-chat/session/chat-stream for
the existing solve action. Backend owner isolation and permission handling stay
authoritative and are outside this frontend decomposition's implementation scope.

## PR #6495 contract that every relevant slice preserves

Notes sends `odysseus:notes-context-selected` with `{ noteIds, accepted: false }`.
Chat's synchronous listener accepts only a valid active or pending chat, updates
session/pending selection, sets `accepted`, and updates the context indicator.
Notes clears select mode only after acceptance. Session lifecycle events
`odysseus:session-changed` carry session/materialization information. Chat appends
selected IDs as `selected_note_ids` when sending; server parsing validates IDs,
filters by owner and treats the resulting prompt context as untrusted.

Keep selection transfer to a materialized chat, reset on a new chat, rejection
when no chat/model can accept it, aliases and indicator behavior. Do not broaden
frontend selections into permissions or bypass the backend. See
`tests/selectedNotesContext.test.mjs`, `tests/test_selected_notes_context.py`,
`static/js/chat.js` selected-context listeners and send payload, and
`static/js/sessions.js` lifecycle event producers.

## One Notes branch, worktree and draft PR

After F1 integration and authorization to begin Notes, create one worktree and
branch, suggested name `refactor/notes-reference-decomposition`, from the latest
official `upstream/dev`. N0 through N6 are sequential internal engineering
checkpoints on that same branch. No independent GitHub PR is required for a
checkpoint or a UI substage. Maintain one draft PR targeting official `dev`, with
checkpoint evidence linked from its description. Keep it a draft until the
complete decomposition passes all acceptance gates below.

| Checkpoint | Responsibility | Deliverable and exit evidence |
| --- | --- | --- |
| N0 | Characterization | Public API/consumer, responsibility, state, lifecycle and network map; deterministic behavior tests; PR-overlap refresh; no extraction |
| N1 | Recurrence domain | Only pure date/recurrence rules to `features/notes/domain/recurrence.js`; preserve old wrapper call contracts, time-read position and returned strings; new offline dependency registered |
| N2 | Infrastructure | HTTP and storage adapters with exact request/error/key contracts; retain state ownership and backend authorization |
| N3 | Application | Single state owner and CRUD/optimistic/undo workflows; UI/reminders call operations; no competing arrays or stores |
| N4 | Reminders | Timer/notification/reminder orchestration through explicit ports; characterize startup, catch-up, deduplication, badge and background behavior |
| N5 | UI substages | Bounded card/panel, editor/draft, checklist, drawing and mobile reviews within the same PR; explicit listener/observer disposal and the same application operations |
| N6 | Compatibility and reference case study | Reduce coordinator; full API/consumer/lifecycle/offline comparison; retain legacy facade unless removal is proven safe; complete the final reference case study in the same PR |

Do not create the Notes worktree, branch or PR during F1. N5 remains several
bounded internal reviews rather than a second monolith move. Every checkpoint
and UI substage must have its own focused tests, review evidence, lessons,
exceptions and a rollback plan. Record the validated revision or reproducible
patch/snapshot, prior known-good checkpoint, exact test commands/results and
how to restore its matching source/output/facade/precache set while preserving
unrelated work. Checkpoint commits require the applicable maintainer authority;
no checkpoint or commit is created by this planning document.

Advance only after the current checkpoint's required checks and review findings
are resolved. A failed checkpoint returns to its prior validated state or receives
a bounded correction on the same branch; it does not require a separate PR.
If upstream changes require updating the baseline, update the same Notes branch,
record that revision and rerun affected checkpoint and compatibility gates.

## Complete Notes PR acceptance gates

Before marking the single draft PR ready for review:

- Complete N0 through N6, including all N5 substages, with their tests, review
  receipts and reproducible checkpoint/rollback evidence.
- Pass the required full Notes regression and compatibility checks on the final
  combined revision, including PR #6495 selection/context behavior, public APIs,
  module identities, single state ownership, storage formats, timers and teardown.
- Pass source/output parity, LF attribute, architecture, import graph and exact
  offline manifest gates; verify current bootstrap/lazy behavior and relevant
  native/packaging contracts. Record platform limits and distinguish preserved
  offline module behavior from separately certified root offline reload.
- Resolve required gate failures and review findings; document unrelated existing
  failures or coverage gaps explicitly rather than bypassing required checks.
- Include the final reference case study in this PR: actual before/after APIs,
  dependency/state/lifecycle maps, stage decisions and lessons, validation recipes,
  temporary exceptions, known debt and tested rollback/compatibility procedures.

These are readiness gates for the complete PR, not separate stage merges.
The receiving maintainer may choose smaller independent PRs for future frontend
features; this one-PR delivery decision applies to the Notes reference program.

## N0 and N1 characterization requirements

Pin recurrence normalization for legacy bare repeat values and explicit values;
none/unknown/malformed input; daily/weekly/monthly/yearly advancement; month ends,
leap years, nth/last weekdays including absent fifth occurrences; overdue catch-up
and the 5,000-step guard; future anchors; local serialization and preserved hours;
DST gaps/overlaps under explicit time zones. `_advanceRecurring` currently reads
`Date.now()` after its first step. Inject a clock without changing that ordering.
Record the existing behavior of invalid dates rather than inventing improvements.

Characterize request error handling, optimistic rollback/undo, archive/delete,
selection/filter/reset, ordering, uploads and notification permission errors.
For drafts, preserve synchronous local backup before the 500 ms persistence
timer, intentional empty text, checklist type fallback, serialized writes,
failure recovery and refusal to clear a newer draft when an older save finishes.

Use stub clocks/ports and temporary storage for unit behavior; browser tests for
public namespace evaluation, DOM lifecycle, selected context and actual offline
loading. Run real modules through their output paths. Do not rewrite source-range
tests wholesale; migrate each only with its responsible slice and equivalent
behavioral assertions. Existing source-sensitive Notes autosave tests currently
read `notes.js`, so extraction has an explicit test-maintenance dependency.

Before a slice, refresh #6585/#6647/#6652 backend behavior and #6470/#6695 shared
UI/platform overlaps from the [assessment](assessment.md). Existing fixes may
change characterization inputs, but this program does not merge or silently
reimplement those PRs. The independent root-SW fix #6227 is required before a
claim of full SPA offline reload readiness.

## H completion and stopping rule

The final reference case study is completed inside the Notes PR before it becomes
ready for review. After that PR is integrated, finalize H with the integrated
revision and checkpoint-to-integration receipts, outstanding dependencies and
the recipient's independent reproduction. Have the receiving maintainer
reproduce setup and validation independently and record that receipt. Transfer
future feature ownership explicitly. Do not automatically begin Calendar,
Documents, Email, Chat, Skills or Settings; the initiating architect continues
with separately approved non-frontend monolith work.
