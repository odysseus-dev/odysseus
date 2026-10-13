# Calendar, Tasks, And Notes

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers calendar, reminders, tasks, assistant runs, and notes in:

- app route wiring, auth exemptions, and scheduler startup in `app.py`;
- canonical database models in `core/database.py`, with `src/database.py` as a compatibility re-export;
- `routes/calendar_routes.py`, `src/caldav_sync.py`, `src/caldav_writeback.py`, and `src/msgraph_calendar.py`;
- `src/msgraph_todo.py`, which syncs todo notes with Microsoft To Do;
- canonical `routes/task/task_routes.py`, compatibility shim `routes/task_routes.py`, `src/task_scheduler.py`, `src/task_endpoint.py`, `src/event_bus.py`, and `src/interactive_gate.py`;
- shared privileged task-action policy in `src/task_action_policy.py`;
- `routes/assistant_routes.py`;
- canonical `routes/note/note_routes.py`, compatibility shim
  `routes/note_routes.py`, `src/builtin_actions.py`, and `src/action_intents.py`;
- agent/tool call sites in `src/tool_index.py` and `src/tool_implementations.py`;
- scoped Codex wrappers in `routes/codex_routes.py`;
- database models `CalendarCal`, `CalendarEvent`, `CalendarDeletedEvent`, `ScheduledTask`, `TaskRun`, `Note`, `MsTodoDeletedNote`, and `CrewMember`;
- direct DB CLIs `scripts/odysseus-calendar`, `scripts/odysseus-notes`, and `scripts/odysseus-tasks`;
- frontend modules `static/js/calendar.js`, `static/js/calendar/*`, `static/js/tasks.js`, `static/js/notes.js`, and `static/js/assistant.js`;
- tests covering calendar routes/utilities, CalDAV, recurrence, timezone handling, scheduler behavior, task webhooks, notes CLI/tool behavior, and task CLI behavior.

## Calendar

`routes/calendar_routes.py` owns `/api/calendar` behavior: config, multi-account CalDAV CRUD, connection test, sync, local calendar CRUD, event CRUD, recurrence expansion, ICS import/export, quick parse, and user timezone offset handling.

`src.caldav_sync` owns CalDAV fetch/sync. `src.caldav_writeback` owns pushing local changes back to remote calendars. `src.msgraph_calendar` owns the Microsoft Graph equivalent for Office 365 / Outlook.com, which cannot use CalDAV because Exchange Online does not implement it. Calendar routes request those behaviors; they do not own remote protocol details.

`REMOTE_CALENDAR_SOURCES` names the calendar sources that write back (`caldav`, `msgraph`). Event CRUD marks `caldav_sync_pending` inside the same transaction as the change and calls `_push_remote_event_after_commit` afterwards, dispatching on `CalendarCal.source`; the HTTP routes and the agent tools in `src/tools/calendar.py` share that path. `caldav_sync_pending` is the generic unpushed-edit marker for both backends despite its CalDAV-era name.

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
- writeback is best-effort and local SQLite remains source of truth when remote writes fail;
- Microsoft Graph sync reuses the same schema: `CalendarCal.source`/`CalendarEvent.origin` carry `msgraph`, `remote_href` holds the Graph event id, `remote_etag` its `changeKey`, and `caldav_base_url` the remote calendar id;
- Graph pull uses `calendarView` over a bounded window, so a recurring series arrives as concrete occurrences with an empty local `rrule` and the local expander does not re-expand it; a series created locally translates its RRULE into a Graph recurrence pattern, and an untranslatable rule is pushed as a single event rather than as a wrong series;
- Graph pull prunes only in-window rows it owns (`origin == "msgraph"`, a `remote_href`, no pending marker) and never after a failed fetch, where an empty result means the calendar could not be read rather than that it is empty;
- `direction=both` pushes before pulling so a local edit is not overwritten by the copy still upstream, and `/api/calendar/sync` merges the CalDAV and Graph results into one payload;
- Graph calendar ids are UUID5-scoped by owner and account, so two users syncing the same shared calendar do not collide.

Microsoft calendar access is a separate OAuth connection from the mail one: Microsoft issues access tokens per resource and rejects an authorization request mixing Exchange and Graph scopes, so `Calendars.ReadWrite` is consented on its own against the same app registration, with its own callback. Graph tokens are stored encrypted in per-user prefs as `msgraph_accounts`, Microsoft's rotated refresh token is persisted on every refresh, and every Graph call is pinned to `https://graph.microsoft.com` — a server-supplied `@odata.nextLink` pointing elsewhere is refused rather than followed.

Calendar credentials are encrypted at rest and are not returned to clients. CalDAV URL validation rejects unsafe schemes, credentials, fragments, localhost names, bad ports, unsafe IP literals, and hostnames resolving to disallowed addresses, with `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` as the explicit private-IP escape hatch. CalDAV sync/writeback clients disable redirects so credentials are not followed to another origin. The connection-test client keeps proxy/environment trust disabled but explicitly loads an operator `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` when the file exists so private/self-signed deployments use the same CA trust intent as real sync.

## Tasks And Assistant Runs

`src.task_scheduler.TaskScheduler` owns scheduled task execution, next-run computation, strict single-slot execution, queued/running cleanup at startup, overdue next-run advancement, webhook-triggered tasks, notifications, run records, chained tasks, and event-triggered actions.

Cookbook serve scheduling crosses this domain. The Cookbook UI creates `cookbook_serve` scheduled tasks, can mirror them as Cookbook calendar events with `cookbook_event_uid`, and task deletion cleans up the linked event when present, falling back to exact-summary matching for legacy events without a stored UID. Cookbook command execution/lifecycle details stay in `cookbook-hwfit.md`.

`routes.task.task_routes` owns task CRUD, status, manual run/stop/cancel, pause/resume, owner-scoped run/activity history, metadata, onboarding defaults, cache clearing, parse endpoints, and webhook-token regeneration. `app.py` imports the canonical package path; `routes/task_routes.py` replaces its module entry with the canonical module for legacy import and monkeypatch compatibility. Chained-task `then_task_id` values are validated as same-owner relationships on create/update, and scheduler execution also rejects cross-owner or cyclic chains.

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
  `cookbook_serve` are admin-only. `routes.task_routes` enforces this on
  create/update/manual run and hides those actions from `/meta/actions` for
  non-admin owners; webhook and scheduler execution pause the task and clear
  `next_run` if an admin-only action belongs to a non-admin owner.
- background LLM task execution uses the background workload path, and the
  scheduler can abort/cancel active in-process task runs when foreground browser
  activity appears.
- `tidy_research` scans all persisted research files because broken JSON has no trustworthy owner stamp, so it runs only for admins or the explicit auth-disabled single-user operator and refuses regular/pre-setup callers before enumeration.

`routes.assistant_routes.py` owns crew/assistant settings and run-status surfaces that use the scheduler. `TaskScheduler.ensure_assistant_defaults()` currently seeds the personal assistant crew member and pinned assistant session, but no longer auto-creates Morning/Midday/Evening check-in tasks. Existing crew-linked check-in tasks are still rendered and managed when present.

## Notes And Reminders

`routes.note.note_routes` owns notes/todos/reminders, and `app.py` imports that
canonical path. `routes.note_routes` replaces its module entry with the
canonical module for legacy import and monkeypatch compatibility. Notes are
SQLAlchemy `Note` rows and can include due dates, ordering, images, repeat
state, AI classification, source/session provenance, and agent session
linkage.

Notes CRUD/reorder/reminder routes resolve the acting owner through `require_user()`: auth-enabled anonymous requests fail closed before hitting owner-scoped queries, while documented no-login/single-user modes still resolve to the compatibility owner path.

### Microsoft To Do Sync

`src.msgraph_todo` owns two-way task sync with Microsoft To Do over Graph. It maps a `todoTask` onto the existing `Note` schema rather than adding a model: a note whose `note_type` is in `SYNCED_NOTE_TYPES` (`todo`, `checklist`) is the local half of a task, `checklistItems` are the note's `items`, `dueDateTime`/`reminderDateTime` are `due_date` (a bare date stays a date; a reminder carries the time of day), `recurrence` is `repeat`, completed status is `archived`, high importance is `pinned`, and the To Do list is a tag in `label`. `Note.origin` carries `mstodo`, `remote_id` the task id, `remote_etag` its `@odata.etag`, `remote_list_id` the list id, `todo_account_id` the connected account, and `todo_sync_pending` the retry marker. `MsTodoDeletedNote` is the delete tombstone, since the note row is gone by the time the push runs.

Odysseus's Tasks tool (`ScheduledTask`) is deliberately not the synced surface: it schedules automation, which has no counterpart in To Do, and importing a task into it would produce rows the scheduler tries to execute.

Runtime behavior:

- only `SYNCED_NOTE_TYPES` is pushed; a plain note, drawing or goal stays local, and a pulled task always lands as `todo`;
- note CRUD, pin, archive and item-toggle routes mark `todo_sync_pending` inside the same transaction as the change and hand the push to `BackgroundTasks`, so a Graph round trip never blocks the response and a crash between commit and push still leaves the retry marker; `src/tools/notes.py` awaits the same write-back directly, since the agent has no response to hang one off;
- changing a synced note's type away from a task tombstones it and deletes it upstream, then clears its remote ids;
- the pull writes only fields that actually changed: the active notes view is ordered by `updated_at`, so rewriting an unchanged row would reshuffle the board on every sync. The `@odata.etag` moves whenever the server touches a task, so it rides along with a real change rather than counting as one;
- the pull preserves tags added locally and replaces only the tag naming another list, so `merge_label` keeps exactly one list tag;
- a task cannot move between lists (Graph has no move operation), so `choose_list_for_note` picks the list once — first tag naming an existing list, else the default list — and a tag matching no list never creates one;
- pruning only touches rows with `origin == "mstodo"`, this account and list, a `remote_id` and no pending marker, and never runs after a failed fetch or a walk truncated at the page cap; a 200 with no tasks is a real answer, so emptying a list upstream does clear its notes;
- tasks completed more than `_COMPLETED_LOOKBACK_DAYS` ago are left upstream rather than imported, but are still counted as seen so the prune does not delete their notes;
- `direction=both` pushes before pulling, and a task note with no `remote_id` is queued for push — which is how the first sync uploads the to-dos that already existed;
- disconnecting an account unlinks its notes but keeps them: they are the user's tasks, not a cache;
- checklist items are reconciled by position through their own Graph collection, because Graph does not accept `checklistItems` on a task PATCH.

Microsoft task access is a separate OAuth connection from mail and calendar, for the same per-resource token reason, with its own `Tasks.ReadWrite` consent and its own callback. Graph tokens are stored encrypted in per-user prefs as `mstodo_accounts`, Microsoft's rotated refresh token is persisted on every refresh, and every Graph call is pinned to `https://graph.microsoft.com`.

Both Graph modules normalize the owner before touching preferences (`_prefs_owner`). Three spellings reach them for the same person on a single-user install — `""` from the OAuth routes, `ODYSSEUS_FALLBACK_OWNER` from the calendar routes, and `None` from the preferences layer itself — and storing under one while reading under another makes `/sync` report success while doing nothing, because "no account connected" is not an error.

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

Email/ntfy failures degrade into channel result fields rather than blocking every reminder path. ntfy and generic webhook reminder URLs run through outbound URL safety checks, with `REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS` controlling whether private/LAN targets are allowed. ntfy notification titles are converted to ASCII with replacement and capped at 200 characters before entering HTTP headers. Reminder dedupe uses owner-scoped cache files under `data/`.

## Agent, Codex, And CLI Surfaces

`do_manage_tasks`, `do_manage_notes`, and `do_manage_calendar` own agent-side writes. `do_manage_calendar` supports batch event creation plus list range aliases (`start`, `start_time`, `start_date`, `range_start`, `from`, `dtstart`, `since`, and matching end aliases), calendar name/short-id lookup, importance/tag aliases, and reminder offsets expressed as numbers, minute/hour words, or common abbreviations such as `min`/`mins`/`hr`/`hrs`. If a model supplies a loose `query`, `date_range`, or `range` without explicit start/end datetimes, `list_events` returns an error asking the caller to resolve the range and call again instead of guessing. Event classification reads `Memory.text` for personal context before LLM classification. `src.tool_index` encodes the reminder policy that notes/todos own reminders while calendar events own time blocks.

Agent native tool owner handling is not uniform today. `do_manage_tasks()` filters lists only when `owner` is truthy and creates tasks with the passed owner, so `owner=None` can create legacy/null-owner tasks. For authenticated/non-empty owners, edit/delete/pause/resume/run require an exact stored owner match and reject both cross-owner and null-owner rows; `owner=None` retains single-user compatibility. `do_manage_notes()` list/query behavior distinguishes `None` from `""`, with `None` acting as broader single-user compatibility while `""` filters to empty-owner rows in some paths. `do_manage_calendar()` query helpers filter only when owner is not `None`, while calendar creation routes through the calendar fallback owner for default calendars. These are compatibility behaviors, not a cross-user sharing model.

Note and calendar route/tool writers owner-reserve any canonical internal upload
references in content, checklist/color/image fields, descriptions, and
locations before their database writes. Missing or wrong-owner uploads fail the
write instead of creating a dangling durable reference; reservations serialize
with upload cleanup.

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

Note routes store caller-provided `source`, `session_id`, `image_url`, and agent-session provenance. Canonical internal upload references in persisted note/calendar fields are owner-reserved before writes, and upload-backed bytes remain protected when fetched through upload routes. Arbitrary non-upload image/provenance URLs are not otherwise normalized or validated by note storage.

## Testing Coverage

Existing coverage is strongest around CalDAV URL hardening/writeback, client cleanup and operator CA handling, bidirectional/pending CalDAV sync markers, CalDAV UID calendar scoping, calendar recurrence/timezone helpers, owner-scoped calendar basics, exact-owner task-tool mutations, scheduler restart/cancel/next-run behavior, webhook auth-exemption source shape, canonical/legacy note-module identity, note-route unauthenticated fail-closed behavior, note/calendar attachment reservations, notes CLI/tool due-date behavior, calendar reminder abbreviation parsing, task CLI preview, task persona fields, and same-owner chained task validation.

Route-level coverage is thinner for full calendar route behavior, task CRUD/security/run controls, live webhook token dispatch, notes owner CRUD/reminder delivery, assistant defaults/run status, event-bus triggers, Codex todo/calendar scopes, and frontend panel wiring.

## Current Gaps

- CardDAV still needs URL hardening parity with CalDAV; CalDAV now resolves hostnames during validation and revalidates writeback URLs.
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
