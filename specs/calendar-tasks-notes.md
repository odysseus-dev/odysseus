# Calendar, Tasks, And Notes

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers calendar, reminders, tasks, assistant runs, and notes in:

- app route wiring, auth exemptions, and scheduler startup in `app.py`;
- canonical database models in `core/database.py`, with `src/database.py` as a compatibility re-export;
- `routes/calendar_routes.py`, `src/caldav_sync.py`, and `src/caldav_writeback.py`;
- canonical `routes/task/task_routes.py`, compatibility shim `routes/task_routes.py`, `src/task_scheduler.py`, `src/task_endpoint.py`, `src/event_bus.py`, and `src/interactive_gate.py`;
- shared privileged task-action policy in `src/task_action_policy.py`;
- `routes/assistant_routes.py`;
- canonical `routes/note/note_routes.py`, compatibility shim
  `routes/note_routes.py`, `src/builtin_actions.py`, and `src/action_intents.py`;
- the note content model in `core/notes_markdown.py`, its boot migrations in `core/database.py`, and the notes rendering options of `static/js/markdown.js`;
- agent/tool call sites in `src/tool_index.py` and `src/tool_implementations.py`;
- scoped Codex wrappers in `routes/codex_routes.py`;
- database models `CalendarCal`, `CalendarEvent`, `ScheduledTask`, `TaskRun`, `Note`, and `CrewMember`;
- direct DB CLIs `scripts/odysseus-calendar`, `scripts/odysseus-notes`, and `scripts/odysseus-tasks`;
- frontend modules `static/js/calendar.js`, `static/js/calendar/*`, `static/js/tasks.js`, the `static/js/notes.js` compatibility facade and maintained `frontend/src/features/notes/` graph deployed under `static/js/features/notes/`, and `static/js/assistant.js`;
- tests covering calendar routes/utilities, CalDAV, recurrence, timezone handling, scheduler behavior, task webhooks, notes CLI/tool behavior, and task CLI behavior.

## Calendar

`routes/calendar_routes.py` owns `/api/calendar` behavior: config, multi-account CalDAV CRUD, connection test, sync, local calendar CRUD, event CRUD, recurrence expansion, ICS import/export, quick parse, and user timezone offset handling.

`src.caldav_sync` owns CalDAV fetch/sync. `src.caldav_writeback` owns pushing local changes back to remote calendars. Calendar routes request those behaviors; they do not own CalDAV protocol details.

Runtime behavior:

- local default calendars are created lazily per owner with stable UUID5 candidates. Default creation remains inside the caller's transaction so a failed event write cannot leave an orphaned calendar; SQLite serializes the absent-row check with `BEGIN IMMEDIATE`, other backends recover insert races inside a savepoint, and renamed-owner ID collisions advance through deterministic slots. List-only callers explicitly commit the lazy default.
- route-level no-login calendar access normalizes empty owner values to `ODYSSEUS_FALLBACK_OWNER` or `owner@localhost`, so route-created calendar rows do not use the empty string as their storage owner;
- CalDAV account config lives in per-user prefs as `caldav_accounts`, with the legacy `/api/calendar/config` route reading/upserting the first account;
- recurring rules are expanded server-side, including compound recurrence IDs;
- RRULE expansion is capped and marks truncated responses;
- event datetimes preserve UTC/local metadata through `CalendarEvent.is_utc` where supported;
- CalDAV pull uses a bounded sync window, scopes existing UID lookups to the synced calendar, stamps account ids and remote metadata on local calendars, maps Google principal URLs to event collections, preserves locally-created or writeback-pending events that are not yet remote-owned, and deletes stale in-window remote events only when remote object parsing did not fail;
- CalDAV writeback stores `remote_href`/`remote_etag`, clears `caldav_sync_pending` only after successful remote writes, and leaves create/update/delete pending markers for retry on failure;
- pull and writeback paths always close their `DAVClient`, including discovery,
  database, and remote-write failure paths;
- sync direction can be pull, push, or both, and pending local writeback rows are included even before remote href metadata exists;
- ICS import is per-owner, capped, creates fresh local IDs in the target import calendar, and preserves zero-duration events as visible imported rows rather than dropping them as empty ranges;
- writeback is best-effort and local SQLite remains source of truth when remote writes fail.

Calendar credentials are encrypted at rest and are not returned to clients. CalDAV URL validation rejects unsafe schemes, credentials, fragments, localhost names, bad ports, unsafe IP literals, and hostnames resolving to disallowed addresses, with `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` as the explicit private-IP escape hatch. CalDAV sync/writeback clients disable redirects so credentials are not followed to another origin. The connection-test client keeps proxy/environment trust disabled but explicitly loads an operator `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` when the file exists so private/self-signed deployments use the same CA trust intent as real sync.

## Tasks And Assistant Runs

`src.task_scheduler.TaskScheduler` owns scheduled task execution, next-run computation, strict single-slot execution, queued/running cleanup at startup, overdue next-run advancement, webhook-triggered tasks, notifications, run records, chained tasks, and event-triggered actions.

Cookbook serve scheduling crosses this domain. The Cookbook UI creates `cookbook_serve` scheduled tasks, can mirror them as Cookbook calendar events with `cookbook_event_uid`, and task deletion cleans up the linked event when present, falling back to exact-summary matching for legacy events without a stored UID. Cookbook command execution/lifecycle details stay in `cookbook-hwfit.md`.

`routes.task.task_routes` owns task CRUD, status, manual run/stop/cancel, pause/resume, owner-scoped run/activity history, metadata, onboarding defaults, cache clearing, parse endpoints, and webhook-token regeneration. `app.py` imports the canonical package path; `routes/task_routes.py` replaces its module entry with the canonical module for legacy import and monkeypatch compatibility. Chained-task `then_task_id` values are validated as same-owner relationships on create/update, and scheduler execution also rejects cross-owner or cyclic chains.

`src.tools.system.do_manage_tasks()` translates structured schedules: `day_of_month` is an integer 1–31 for monthly tasks, while recurring `weekdays` names plus `scheduled_time` in UTC `HH:MM` become a cron expression. Conflicting day/cron inputs are rejected. One-off `scheduled_date` values are parsed to naive UTC and must be future dates; recurring schedules reject supplied one-off dates, and invalid cron expressions are refused. A time-only edit of an existing five-field cron rewrites its minute/hour fields while preserving calendar fields and recomputes `next_run`; it does not merely update the legacy display clock.

Task webhook paths are auth-exempt at the app middleware layer only for `/api/tasks/{task_id}/webhook/{token}`. The route still validates active task state plus task-specific webhook token before dispatch.

Task runtime behavior:

- task runs move through queued/running/success/error/skipped/aborted states;
- scheduler/background execution can wait for `src.interactive_gate` to report a quiet foreground window, and running background work can use browser heartbeat/chat-stream activity as a cancellation/defer signal where implemented;
- output targets include chat sessions, notifications, email, and MCP delivery paths;
- LLM and research tasks can carry a built-in `character_id` persona prompt that the scheduler prepends at execution time;
- task-created chat sessions can be foldered under `Tasks`, and startup migration backfills task/research folders for legacy sessions;
- event-bus triggers persist counters and `next_run` before scheduler handoff;
- the in-process scheduler is gated by `ODYSSEUS_INPROCESS_TASKS`, and multiple enabled app processes can double-run work.
- action tasks with `run_local`, `run_script`, `ssh_command`, or
  `cookbook_serve` are admin-only. `routes.task_routes` enforces this on create/update/manual run and hides those actions from `/meta/actions` for non-admin owners; webhook and scheduler execution pause the task and clear `next_run` if an admin-only action belongs to a non-admin owner.
- background LLM task execution uses the background workload path, and the
  scheduler can abort/cancel active in-process task runs when foreground browser activity appears.
- `tidy_research` scans all persisted research files because broken JSON has no trustworthy owner stamp, so it runs only for admins or the explicit auth-disabled single-user operator and refuses regular/pre-setup callers before enumeration.

`routes.assistant_routes.py` owns crew/assistant settings and run-status surfaces that use the scheduler. `TaskScheduler.ensure_assistant_defaults()` currently seeds the personal assistant crew member and pinned assistant session, but no longer auto-creates Morning/Midday/Evening check-in tasks. Existing crew-linked check-in tasks are still rendered and managed when present.

## Notes And Reminders

`routes.note.note_routes` owns notes/todos/reminders, and `app.py` imports that canonical path. `routes.note_routes` replaces its module entry with the canonical module for legacy import and monkeypatch compatibility. Notes are SQLAlchemy `Note` rows and can include due dates, ordering, images, repeat state, AI classification, source/session provenance, and agent session linkage.

Notes CRUD/reorder/reminder routes resolve the acting owner through `require_user()`: auth-enabled anonymous requests fail closed before hitting owner-scoped queries, while documented no-login/single-user modes still resolve to the compatibility owner path.

### Note Content Model

A note is one markdown document, `Note.content`. Checklist items are GitHub-style task lines inside it and images are markdown images. `core/notes_markdown.py` owns the rules; it is stdlib-only so the migration, the routes, the agent tool and the CLI share it without import cycles.

Task-line grammar (`_TASK_RE`): optional spaces or tabs, a `-`, `*` or `+` bullet, one or more spaces or tabs, a `[ ]`, `[x]` or `[X]` box, then the item text to the end of the line. Nesting is two spaces per level, a tab counts as one level, and emitted lines are capped at three levels. Lines split on `\n` only (CRLF counts as one break), not `str.splitlines()`, so numbering agrees with the JS mirror.

Fence rule: a line opening with three or more backticks or tildes starts fenced code, and only a delimiter of the same character that is at least as long closes it (CommonMark). An unclosed fence runs to the end of the document. Task-looking lines inside a fence are code, not items: they are not numbered, toggled, counted for `note_type`, or treated as already present by the migration.

A task's index is its 0-based ordinal among task lines outside fences. `core/notes_markdown.py`, `frontend/src/features/notes/domain/markdownTasks.js` and the interactive task renderer in `static/js/markdown.js` all number the same way, pinned by `tests/fixtures/notes_task_vectors.json`, so a toggle by index means the same line in the API, the agent tool and the UI.

### Derived Fields And Write Compatibility

`content` is the source of truth; the API keeps its shape by deriving the legacy fields on read (`_note_to_dict`):

- `items` is `[{index, text, done, indent}]` from the task lines, or `null` when there are none (so a note written with `items: []` reads back `null`). An item's old `id` and any other key a client sent are not kept;
- `note_type` is `checklist` when the content is only task lines and blank lines, `note` otherwise. `goal` and `draw` are stored modes, not derived (`STORED_NOTE_TYPES`), so the Goals/Today views and drawings keep working;
- `image_url` returns the legacy attachment URL for any note type, including a drawing's canvas. Markdown-only images do not populate this field.
- `body_revision` is an opaque token for the body and its legacy fields. Use the token from the read that supplied the task index or editor contents.

Writers that still send the legacy fields are folded into content server-side, on `POST /api/notes` and `PUT /api/notes/{id}`:

- `items` rewrites the task lines in place (`apply_items_to_content`): the n-th item rewrites the n-th task line, an unchanged line is kept byte for byte, extra items are inserted after the last task line, task lines past the end of the array are dropped, and prose and fenced code are untouched. An item with blank text removes its task line, so the items read back are the array without it. An omitted `items` leaves the task lines alone;
- `image_url` identifies one legacy attachment. A new URL replaces its folded standalone line or adds the reference once. An empty string or `null` clears the attachment. Inline Markdown images and ordinary links keep their destinations.
- A `content` write with omitted `image_url` keeps the legacy attachment even when the payload contains only prose. Without `items`, it also keeps old task lines when the new text contains none. On REST PUT, `content: null` clears the prose, `items: null` or `[]` clears the checklist, and `image_url: null` clears the attachment. Omitted fields retain their compatibility behavior.
- Send `replace_content: true` with `content` to replace the complete document. Omitted task lines and images are removed. The server clears the compatibility attachment pointer if its image is absent. The text editor uses this mode for a Markdown document. A legacy field sent explicitly is still applied.
- Send the observed `body_revision` with body edits to reject a stale client edit with HTTP 409. PUT without a token remains available for older callers and conflicts if another body write commits after the server read.

Boot folds older rows on SQLite. Readers project unmigrated fields with `effective_content()`. The first body write folds readable legacy items and the image. Unreadable items survive unless explicitly replaced. See [the migration and rollback notes](../docs/notes-markdown-migration.md).

Clients that relied on persistent checklist IDs, custom item properties, the exact stored `note_type`, or a bodyless index toggle need updating. A mixed note takes the derived type `note` even when it contains tasks.

### Checklist Endpoints

- Read the note with `GET /api/notes/{id}`. Take the intended task's zero-based `items[].index` and the `body_revision` from that same response. Send them as the path index and JSON body in `POST /api/notes/{id}/items/{index}/toggle` with `{"body_revision":"<token>"}`. Success returns `{ok, item, items, note}` and changes only the task's completion marker. With a matching revision, an invalid index or a note without tasks returns 400. A missing or stale revision returns 409. Read the note again and verify the intended task before retrying with its current index and revision.
- `POST /api/notes/{id}/items` accepts `{text, indent}` and appends one open task after the last task line, or as a new block when there are none. It retries from current state when needed and returns 409 if concurrent writes persist. Empty text returns 400. Success returns `{ok, item, note}`.

Card checkboxes use the toggle route; the card's add-item input uses the append route.

### Note Rendering

Note cards and the note window render content with `mdToHtml(content, {tasks: 'interactive', images: 'notes', dialect: 'notes'})` (`_renderNoteMarkdown` in `frontend/src/features/notes/index.js`). Only the Notes UI passes these options. Rendered task rows also get the existing `.note-checkbox` classes, so the card, mobile and select-mode handlers apply to them unchanged.

- `tasks: 'interactive'` numbers task lines on the raw source with the fence rule above, before any other pass, and renders them as `li.md-task[data-task-index]` rows with a `role="checkbox"` box. A task line that lands where a list cannot render (inline code, a `<details>` block) keeps its text but is not clickable.
- `images: 'notes'` permits `/api/upload/<id>` on this origin, base64 PNG, JPEG, GIF and WebP data URLs, and same-origin blob URLs. Other Markdown image URLs render as links. The Notes HTML sanitizer removes raw inline styles and filters resource attributes in an inert template. Drawings use the same Markdown path on cards, and their canvas editor uses the same URL policy.
- In notes mode the sanitizer also keeps a link target (`href`, `xlink:href`, `action`, `formaction`) only when it is `http`, `https`, `mailto`, `tel`, a path or an anchor (`NOTE_LINK_SCHEMES`). Raw HTML is sanitized after the dialect's escapes are put back into it, so an escape such as `javascript\:` cannot carry a scheme past the sanitizer. A task's checkbox gets an `aria-label` with the task's text, read off the finished markup as a string; the note is never parsed a second time. In the note window a plain click or a tap on a link opens it in a new tab (`noopener,noreferrer`), only when its target passes the link menu's check (`normaliseLinkUrl`); a drag, a click that leaves a selection and a Shift or Alt click stay edits, and the caret gets into a link with a click beside it or the arrow keys, where Cmd/Ctrl+K opens the link menu.
- `dialect: 'notes'` is what the note editor writes back. A backslash escapes one markdown character (`` \ ` * ~ # - + . > | $ < _ ! [ ] : ``), so typed `2 \* 3 \* 4` stays text; code spans and `\[ … \]` / `\( … \)` math keep their backslashes. Indented `- ` and `* ` bullets nest (two spaces per level, up to three), and an ordered list that starts at a number other than 1 keeps it as `<ol start>`.

Reminder policy:

- "remind me at 5pm" should become a todo/note with a due date;
- calendar event alarm/reminder UI writes reminder Notes;
- calendar events are for scheduled time blocks, meetings, appointments, or explicit calendar requests;
- creating a calendar event named "Reminder" does not create notification behavior.

Built-in reminder/persona prompt text is mirrored server-side for reminder synthesis and scheduled task execution; frontend persona selectors are UI over that server-owned id map, not the authority.

Reminder dispatch is Note-owned:

- `dispatch_reminder()` owns browser, email, ntfy, generic webhook, in-app notification, optional LLM reminder text, and dedupe behavior;
- the scheduler note scanner calls note-ping actions for backend due-note delivery with per-owner notification state, and calendar-event reminders are treated as Note-owned reminders rather than separate scheduler event pings;
- the notes frontend has a browser-tab fallback for visible sessions;
- calendar frontend reminder UI stores reminder records as Notes, not calendar-event notification jobs.

Reminder text comes from one builder, `core.notes_markdown.reminder_body()`, shared by `/api/notes/fire-reminder` and the scheduler's `action_ping_notes()`: the note's prose with task lines removed (first 400 characters), then `Pending (N):` and up to eight open tasks as `- text` lines, with `...and K more` past that. A note with tasks but none open gets its prose, or `N items`; a note with no task lines gets its first 400 characters. The text is plain words (`reminder_text()`): image markdown is dropped, a link becomes its text, heading and quote markers go, fenced code stays as written, and a task that is only an image is not listed. The browser's local notification follows the same rules (`reminderBody` in `domain/noteRules.js`), and both are pinned by `tests/fixtures/notes_reminder_vectors.json`. The synthesis filter that drops an echoed "Pending" line matches `Pending (N):`. The daily brief (`action_daily_brief()`) lists up to three open tasks from any note with task lines, not only `note_type == "checklist"`, and skips the calendar's own reminder notes, which its Calendar section already covers.

Email/ntfy failures degrade into channel result fields rather than blocking every reminder path. ntfy and generic webhook reminder URLs run through outbound URL safety checks, with `REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS` controlling whether private/LAN targets are allowed. ntfy notification titles are converted to ASCII with replacement and capped at 200 characters before entering HTTP headers. Reminder dedupe uses owner-scoped cache files under `data/`.

`src.builtin_actions.action_ping_notes()` counts delivery only when `dispatch_reminder()` reports success for the configured browser/email/ntfy/webhook channel. Typed receipts in `data/note_pings_<owner-slug>.json` record time and channel. A browser fallback or legacy timestamp does not prove external delivery: the next eligible scan retries the configured external channel without queueing the recent browser fallback again. The scanner reloads dispatcher-owned receipts before pruning so it cannot replace a successful channel receipt with a timestamp; total delivery failure is not checkpointed. Retries occur only within 90 seconds of the due time, while recent matching-channel receipts suppress delivery for 25 minutes. Existing retry tests cover webhook/ntfy failures, legacy receipts, browser dedupe, total failure, and receipt-preserving pruning; live channel delivery remains separate validation.

## Agent, Codex, And CLI Surfaces

`do_manage_tasks`, `do_manage_notes`, and `do_manage_calendar` own agent-side writes. `do_manage_calendar` supports batch event creation plus list range aliases (`start`, `start_time`, `start_date`, `range_start`, `from`, `dtstart`, `since`, and matching end aliases), calendar name/short-id lookup, importance/tag aliases, and reminder offsets expressed as numbers, minute/hour words, or common abbreviations such as `min`/`mins`/`hr`/`hrs`. If a model supplies a loose `query`, `date_range`, or `range` without explicit start/end datetimes, `list_events` returns an error asking the caller to resolve the range and call again instead of guessing. Event classification reads `Memory.text` for personal context before LLM classification. `src.tool_index` encodes the reminder policy that notes/todos own reminders while calendar events own time blocks.

Agent native tool owner handling is not uniform today. `do_manage_tasks()` filters lists only when `owner` is truthy and creates tasks with the passed owner, so `owner=None` can create legacy/null-owner tasks. For authenticated/non-empty owners, edit/delete/pause/resume/run require an exact stored owner match and reject both cross-owner and null-owner rows; `owner=None` retains single-user compatibility. `do_manage_notes()` list/query behavior distinguishes `None` from `""`, with `None` acting as broader single-user compatibility while `""` filters to empty-owner rows in some paths. `do_manage_calendar()` query helpers filter only when owner is not `None`, while calendar creation routes through the calendar fallback owner for default calendars. These are compatibility behaviors, not a cross-user sharing model.

`do_manage_notes()` reads task lines from content. `get` (aliases `view`, `read`, `show`, `open`) prints each item as `[x] i: text` with the index `toggle_item` takes, and those aliases count as reads in `src/tool_capabilities.py` and `src/clean_agent_preview.py`. `list` stays a locator and does not print bodies. `toggle_item` needs an explicit integer index and `body_revision` from the same get/view result. It accepts an optional boolean `done`. On a conflict, read and verify the item before retrying. The Codex bridge translates these conflicts to HTTP 409. Explicit nulls on tool update body fields retain the previous behavior of leaving their values in place. To create or replace a checklist the tool still takes `checklist_items` (written into content through `apply_items_to_content`), because that path carries the validation for empty lists and tasks in titles; content the model writes may contain task lines directly. An `update` with `content` and no `checklist_items` keeps the existing task lines when the new text has none, and `checklist_items: []` clears them; the Codex todo bridge goes through the same code. Selected-notes chat context (`_format_selected_notes_context()` in `routes/chat_routes.py`) sends the content verbatim plus an `Items:` block restating the tasks, with the derived `note_type`. `scripts/odysseus-notes` derives `items` and `note_type` the same way, and `create --item TEXT` (repeatable) writes task lines.

Note and calendar route/tool writers owner-reserve any canonical internal upload references in content, checklist/color/image fields, descriptions, and locations before their database writes. Missing or wrong-owner uploads fail the write instead of creating a dangling durable reference; reservations serialize with upload cleanup.

Chat forwards browser timezone offset and IANA timezone name so natural-language note/calendar tools can anchor dates to the user clock. A valid IANA zone wins over the fixed offset for current-time/DST reasoning; invalid or absent names fall back to the offset and then server-local/UTC compatibility behavior. Chat can auto-promote note/calendar/reminder intents to agent mode.

Codex todo/calendar wrappers enforce bearer-token owner and `todos:*` or `calendar:*` scopes, then delegate to note/calendar behavior as the token owner. Normal calendar/task/note routes are current-user/cookie routes and should not be treated as scoped bearer-token APIs unless they explicitly use token owner/scope policy.

Direct DB CLIs are local compatibility tools. They bypass HTTP route behavior, CalDAV writeback, and some owner/timezone parsing policy.

## Event Bus

`src.event_bus` owns event-triggered task counters and scheduler handoff. Current emitters include chat/session/document/memory/research/email/skill paths. Ownerless events resolve to a primary configured user instead of broadcasting to every owner.

The current event bus is not a calendar-event emitter despite the adjacent calendar/task/reminder domain.

## Timezone And Date Semantics

- calendar events store offset-aware input as UTC/naive fields plus `is_utc`;
- note `due_date` uses ISO-like strings interpreted through note/tool parsers;
- chat forwards browser UTC offset into `routes.calendar_routes` request-local state for natural-language date anchoring in calendar/note tool parsing;
- generic scheduled task clock times are stored as UTC values after local conversion;
- assistant check-ins can use an IANA timezone on `CrewMember`, with UTC fallback.

Dateutil fallbacks strip timezone-aware parser results back to the naive-UTC contract before recurrence/window comparisons. Calendar agent list tools accept current range aliases implemented by `src.tool_implementations`, and equal/same-day start/end ranges are normalized to a one-day window instead of silently returning no rows.

Natural-language parsers prefer time-first interpretations for short reminder/event phrases where the user supplies a clock time before a date phrase.

Calendar frontend week-start preference is browser-local (`cal-week-start`) with Monday/Sunday controls; it is not persisted as a server preference.

Natural-language date parsing and timezone behavior are compatibility-sensitive and need route/tool/frontend regression coverage when changed. Request-local timezone context is ephemeral and must not be persisted as user state. A valid browser IANA timezone is authoritative over a possibly stale or wrong-sign fixed offset because it carries daylight-saving rules.

## Degraded And Optional Behavior

- CalDAV sync no-ops with shaped errors when unconfigured, invalid, offline, or missing the optional `caldav` dependency.
- CalDAV writeback failures are non-fatal to local calendar writes and are mostly visible through logs.
- Missing or invalid `croniter` rejects cron schedules or yields no next run.
- Missing timezone support falls back to UTC or legacy behavior.
- ICS import depends on `icalendar`; missing dependency can fail before route-shaped error handling today.
- Notes reminders can still use local browser fallback when backend email/ntfy channels fail.
- App backup import/export does not currently include calendar events, scheduled tasks, task runs, or notes; calendar ICS import/export is separate and calendar-only.

## Security And Provenance

Calendar, task, note, and assistant routes are owner-scoped for normal users. Legacy null-owner behavior is compatibility-sensitive and should not silently grant authenticated owners broad mutation rights.

Because auth-disabled chat owners can arrive as `None`, tool-created rows may not use the same owner value as route-created rows. Multi-user or owner-model changes must audit both route and agent paths.

Task creation/update/manual run/webhook/scheduler execution blocks shell-like and Cookbook serve action types for non-admin users through `src.task_action_policy`, and tool security blocks privileged task/calendar tools for non-admin use. Assistant defaults reject synthetic owners such as `api` and `internal-tool`.

Note routes store caller-provided `source`, `session_id`, and agent-session provenance; a caller-provided `image_url` is folded into content as a markdown image. Canonical internal upload references in persisted note/calendar fields are owner-reserved before writes, and upload-backed bytes remain protected when fetched through upload routes. Arbitrary non-upload image URLs in content are not otherwise normalized or validated by note storage; the boundary is at display, where the notes image policy above refuses to load anything that is not same-origin.

## Testing Coverage

Existing coverage is strongest around CalDAV URL hardening/writeback, client cleanup and operator CA handling, bidirectional/pending CalDAV sync markers, CalDAV UID calendar scoping, calendar recurrence/timezone helpers, owner-scoped calendar basics, exact-owner task-tool mutations, scheduler restart/cancel/next-run behavior, webhook auth-exemption source shape, canonical/legacy note-module identity, note-route unauthenticated fail-closed behavior, note/calendar attachment reservations, notes CLI/tool due-date behavior, calendar reminder abbreviation parsing, task CLI preview, task persona fields, and same-owner chained task validation.

Notes rework coverage: task-line rules in Python and JS against `tests/fixtures/notes_task_vectors.json` (`tests/test_notes_markdown.py`, `tests/notesMarkdownTasks.test.mjs`); legacy-field folding and the checklist routes (`tests/test_notes_markdown_routes.py`); the migration, its per-row isolation and the documented rollback SQL (`tests/test_notes_unify_migration.py`); rendering and the image policy (`tests/notesMarkdownRender.test.mjs`); serializer round trips (`tests/notesMarkdownSerializer.test.mjs`); autosave and 409 handling (`tests/notesDraftAutosave.test.mjs`); the panel's search and order (`tests/notesQueries.test.mjs`). `scripts/verify_notes_browser_runtime.py` boots a disposable instance with a legacy-format note and drives the note window on desktop and phone viewports.

Route-level coverage is thinner for full calendar route behavior, task CRUD/security/run controls, live webhook token dispatch, notes reminder delivery, assistant defaults/run status, event-bus triggers, Codex todo/calendar scopes, and frontend panel wiring.

## Calendar Reminder And Invitation State

Calendar event responses now include reminder note id, due time, minutes-before, and source-email provenance. Create/update accept `reminder_minutes`; an explicitly supplied null clears the reminder, while omission preserves it. Reminders are owner-scoped `Note` todos with calendar label/source. Passed events skip creation, and a reminder whose intended time passed is clamped to now. Legacy matching uses generated title plus due time rather than an event UID; deleting reminders uses the title/source/owner match, so similarly named events remain a compatibility-sensitive boundary. `GET /api/calendar/events/{uid}` resolves recurring occurrence IDs back to the owner-visible base event.

`src/email_calendar_import.py` owns invitation revision state through `EmailCalendarInvitation`: identity hashes owner, normalized sender, ICS UID, and optional recurrence id instead of letting untrusted ICS UIDs address arbitrary local events. Sequence/DTSTAMP ordering suppresses stale replays, cancellation wins equal-version ties and leaves tombstones, and detached occurrences exclude their original series instance. Fixed striped file locks serialize imports across processes. Unsupported reply methods are ignored, and `THISANDFUTURE` revisions require a replacement series.

Task create/update persist sealed `request_authority_json`; execution binds that authority to LLM/research work rather than deriving permission from task output. Task endpoint resolution uses owner-visible registered endpoint bases or their exact derived chat URL before runtime credentials are attached; scheme, host, effective port and full trimmed path must match, with no userinfo, parameters, query or fragment. Legacy shared endpoint visibility is retained. The scheduler helper catches credential resolution exceptions and can continue with empty headers, so this is not a universal no-network-on-resolution-failure guarantee. Authority/effect contracts are owned by `src/agent_runtime`, not the scheduling layer.

## Scheduler Cache And Native Task Details

Scheduler `_cached` requests share one pending future per key with a TTL. Waiters shield the shared work from their own cancellation; completion/failure removes only the matching pending future, and failures can retry rather than being cached as success. Endpoint discovery separately caches successful empty Tailscale results, while discovery failures do not become successful empty cache entries.

Task/assistant endpoint updates accept registered owner-visible routes, canonicalize empty values to no override and reject invalid routes. Local shell/script/Cookbook scheduled actions require their sealed native backend capability; missing capabilities fail closed and raw external/SSH execution cannot borrow it. Headless task/background continuations close their streams in the originating task, preserve authority, honor replacement final responses and distinguish provider failure from successful completion.

`NotificationLog` GET is owner-scoped and non-consuming, defaults to 200 rows and clamps to 1000. Notification insert title/body limits are 200/2000 characters; notification text does not grant task authority. Calendar reminder matching is legacy title/source/owner-based rather than UID-linked: nearest eligible due time within zero to seven days of the base event start can match, while deletion removes all same-title matches rather than discriminating by exact alarm time. Negative offsets clamp to zero, passed events do not create reminders, passed alarm time clamps to now, explicit null clears and omission preserves.

## Notes Frontend State And Lifetimes

The canonical Notes composition entry owns one `createNotesState()` instance, injected HTTP/storage/notification ports, application workflows and UI factories. Domain modules own note rules and local-time recurrence. Application modules own query/order, optimistic card/checklist/deletion, selection, the note edit session (`noteSession.js`) and this tab's own writes (`noteVersions.js`), agent actions and reminders; UI modules delegate mutations and transport through those operations. The legacy facade preserves default/named open/close/toggle/new/openNote/badge methods and aliases, including named-only reminder-dot dismissal, without exposing mutable state.

Background startup is idempotent: after three seconds it loads active notes (unless the panel is already open on active notes: that list is the panel's, and swapping it without a redraw left stale cards and hid changes from the 20 s re-read), then starts a 30-second reminder interval and immediate check. Close/minimize/force-close dispose panel resources but leave that page-owned interval running. Scans use the current shared note collection, skip archived/date-only/fired notes, fire within the previous minute, and advance or mark older reminders; archived-only/error-cleared lists can still defer active reminders. Server synthesis has a 1.5-second local-notification fallback, with toast/highlight persistence. This browser path remains separate from backend delivery receipts.

The note window (`ui/noteWindow.js`) is the only editor, on desktop and phones. Clicking a card opens it; one is open at a time, opening another flushes the current one, it minimises to a `Note: <title>` dock chip, and the legacy facade's `openNote(id)` opens it from chat links. On phones it is a full-height sheet with 16 px text so iOS does not zoom on focus. The sheet has no close or minimise button there: a swipe down (the shared gesture in `static/js/ui.js`, which hides the modal and fires `modal-dismissed`) closes the window through `close()`, the path the close button and Escape take, so pending edits are flushed and the window's state is cleared; minimising is a desktop action. While the phone keyboard is up (`keyboard-open` on the root, with the visual viewport's pan in `--keyboard-viewport-top`) the sheet is placed and sized to the visible area, the window opts out of `ui.js`'s focus scroll (`data-keyboard-scroll="off"`) and scrolls its own text box to keep the caret between the title bar and the keyboard (`watchCaret`). Its controls have hit areas of at least 44 px on touch screens; the panel's search and quick-add inputs are 16 px on phones too. A card's title is a button (`role="button"`, `tabindex="0"`) that opens the note on Enter or Space. Closing the window or deleting the note puts focus back on the element that opened it, or on the note's card. The in-grid form, the mobile fullscreen editor, the form draft autosave and the form checklist rows are gone, along with the Note/Todo/Draw switch and `#tag` typed into the title; the tag field still accepts `#tag`. The quick-add bar (`ui/quickAdd.js`) has one entry, a note, and no Note/To-do toggle: a click or the first keystroke opens a new note window with what was typed as its title, Enter in the title moves to the body, and a to-do list is made there with the toolbar's Checklist (`note_type` follows the content). Its photo button opens a new note on the image picker.

The body (`ui/richBody.js`) shows the rendered note and is edited in place; Markdown source mode swaps it for a textarea with the raw content. Content is split into markdown blocks (`splitMarkdownBlocks()` in `domain/markdownSerializer.js`). A block that was not touched is written back byte for byte, so editing one paragraph never reformats the rest; an edited block is serialized from the DOM in the notes dialect, keeping code languages, tables, nested and numbered lists, blank lines and multi-line quotes, and escaping typed markdown characters. A block is locked, shown with a dashed outline (`.note-md-locked`) and the hint "This part can only be edited in Markdown source", when its rendering contains something the dialect has no syntax for (math, `<details>`, `<kbd>`, `<sup>`, media and other raw HTML), when it holds an aligned table or a markdown image the notes image policy would not load, or when serializing and re-rendering it would not reproduce what is shown. A locked block is always written back verbatim. Paste is plain text only, and links refuse `javascript:`.

`application/noteSession.js` owns saving. Nothing is written on open, blur or close unless the note was edited. An edit is backed up to localStorage at once and saved 600 ms later (`AUTOSAVE_DELAY_MS`) as the whole document (`replace_content: true`) with the `body_revision` the window last saw, and all writes of one session go through one queue. A new note is created by its first non-empty save, so an untouched new note creates nothing. A 409 keeps the local text in the draft, the session reads the current note, and the window shows "This note changed elsewhere." with Reload (adopt the server copy) or Keep mine (write the local text over it, against the new revision). A successful save clears only the draft it wrote. A draft records the `body_revision` it was typed against: the next time the note opens, a draft typed against the current revision is offered back with Restore / Discard, while one typed against an older revision, or left by another tab (kept in `odysseus-note-recovery-<id>`), is only offered to copy from, so it can never be written over a newer note. Checkbox flips go through the toggle route with the window's `body_revision` after pending text is saved, and a version this tab wrote itself (`noteVersions.js`, e.g. a card checkbox ticked behind the open window) is followed rather than reported as a conflict. The toolbar's Image and Sketch buttons insert an uploaded picture or a canvas PNG into the note; an existing drawing opens as a note with its image, and re-drawing it is not supported.

Selection dispatches `odysseus:notes-context-selected` synchronously and exits only when the consumer sets `accepted`; chat retains context/session authority. Closing releases owned portals/listeners/gestures/observers, and late fetch/masonry work checks panel connectivity/open state. It does not add universal async generation guards: fetch remains last-completion-wins.

## Current Gaps

- `do_manage_notes()` should match HTTP note-route owner behavior for legacy null-owner notes.
- Auth-disabled agent tools can produce or read broader owner scopes than route handlers because they receive `owner=None`; tasks, notes, and calendar need aligned policy/tests.
- Task webhook tests should keep exercising live route token behavior and
  admin-only action blocking, not only middleware/source strings.
- Reminder delivery needs tests across frontend `/fire-reminder`, backend `dispatch_reminder()`, scheduler note pings, channel degradation, and dedupe.
- Codex todo/calendar scope and owner mapping needs dedicated regression coverage.
- Direct DB CLIs need either documented route-bypassing support status or shared helpers to avoid owner/timezone/writeback drift.
- `scripts/odysseus-webhook` builds the live `/api/tasks/{task_id}/webhook/{token}` path with percent-encoded path segments; its direct DB token rotation/revocation behavior remains a local compatibility surface.
- Assistant default documentation/code comments still mention check-ins that are no longer auto-seeded.
- App backup import/export does not cover the calendar/task/note rows described by this spec.
- The legacy `Note.items` and `Note.image_url` columns stay in the schema: the rollback restores into them and readers still fold an unmigrated row's `items`. Dropping them needs its own migration once rollback is no longer wanted.
- `goal` and `draw` are stored editing modes, not derived from content, so a drawing note keeps `note_type = "draw"` even when its content is ordinary markdown.
- An existing drawing cannot be re-edited on its canvas: it opens in the note window as a note with its image.
