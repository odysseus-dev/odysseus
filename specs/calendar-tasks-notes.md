# Calendar, Tasks, And Notes

Last updated: dev@d88c8cb | 2026-07-09

## Scope

This spec covers calendar, reminders, tasks, assistant runs, and notes in:

- app route wiring, auth exemptions, and scheduler startup in `app.py`;
- canonical database models in `core/database.py`, with `src/database.py` as a compatibility re-export;
- `routes/calendar_routes.py`, `src/caldav_sync.py`, and `src/caldav_writeback.py`;
- `routes/task_routes.py`, `src/task_scheduler.py`, `src/task_endpoint.py`, `src/event_bus.py`, and `src/interactive_gate.py`;
- shared privileged task-action policy in `src/task_action_policy.py`;
- `routes/assistant_routes.py`;
- `routes/note_routes.py`, `src/builtin_actions.py`, and `src/action_intents.py`;
- agent/tool call sites in `src/tool_index.py` and `src/tool_implementations.py`;
- scoped Codex wrappers in `routes/codex_routes.py`;
- database models `CalendarCal`, `CalendarEvent`, `ScheduledTask`, `TaskRun`, `Note`, and `CrewMember`;
- direct DB CLIs `scripts/odysseus-calendar`, `scripts/odysseus-notes`, and `scripts/odysseus-tasks`;
- frontend modules `static/js/calendar.js`, `static/js/calendar/*`, `static/js/tasks.js`, `static/js/notes.js`, and `static/js/assistant.js`;
- tests covering calendar routes/utilities, CalDAV, recurrence, timezone handling, scheduler behavior, task webhooks, notes CLI/tool behavior, and task CLI behavior.

## Calendar

`routes/calendar_routes.py` owns `/api/calendar` behavior: config, multi-account CalDAV CRUD, connection test, sync, local calendar CRUD, event CRUD, recurrence expansion, ICS import/export, quick parse, and user timezone offset handling.

`src.caldav_sync` owns CalDAV fetch/sync. `src.caldav_writeback` owns pushing local changes back to remote calendars. Calendar routes request those behaviors; they do not own CalDAV protocol details.

Runtime behavior:

- local default calendars are created per owner as needed;
- route-level no-login calendar access normalizes empty owner values to `ODYSSEUS_FALLBACK_OWNER` or `owner@localhost`, so route-created calendar rows do not use the empty string as their storage owner;
- CalDAV account config lives in per-user prefs as `caldav_accounts`, with the legacy `/api/calendar/config` route reading/upserting the first account;
- recurring rules are expanded server-side, including compound recurrence IDs;
- RRULE expansion is capped and marks truncated responses;
- event datetimes preserve UTC/local metadata through `CalendarEvent.is_utc` where supported;
- CalDAV pull uses a bounded sync window, scopes existing UID lookups to the synced calendar, stamps account ids and remote metadata on local calendars, maps Google principal URLs to event collections, preserves locally-created or writeback-pending events that are not yet remote-owned, and deletes stale in-window remote events only when remote object parsing did not fail;
- CalDAV writeback stores `remote_href`/`remote_etag`, clears `caldav_sync_pending` only after successful remote writes, and leaves create/update/delete pending markers for retry on failure;
- sync direction can be pull, push, or both, and pending local writeback rows are included even before remote href metadata exists;
- ICS import is per-owner, capped, creates fresh local IDs in the target import calendar, and preserves zero-duration events as visible imported rows rather than dropping them as empty ranges;
- writeback is best-effort and local SQLite remains source of truth when remote writes fail.

Calendar credentials are encrypted at rest and are not returned to clients. CalDAV URL validation rejects unsafe schemes, credentials, fragments, localhost names, bad ports, unsafe IP literals, and hostnames resolving to disallowed addresses, with `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` as the explicit private-IP escape hatch. CalDAV sync/writeback clients disable redirects so credentials are not followed to another origin.

## Tasks And Assistant Runs

`src.task_scheduler.TaskScheduler` owns scheduled task execution, next-run computation, strict single-slot execution, queued/running cleanup at startup, overdue next-run advancement, webhook-triggered tasks, notifications, run records, chained tasks, and event-triggered actions.

Cookbook serve scheduling crosses this domain. The Cookbook UI creates `cookbook_serve` scheduled tasks, can mirror them as Cookbook calendar events with `cookbook_event_uid`, and task deletion cleans up the linked event when present, falling back to exact-summary matching for legacy events without a stored UID. Cookbook command execution/lifecycle details stay in `cookbook-hwfit.md`.

`routes/task_routes.py` owns task CRUD, status, manual run/stop/cancel, pause/resume, owner-scoped run/activity history, metadata, onboarding defaults, cache clearing, parse endpoints, and webhook-token regeneration. Chained-task `then_task_id` values are validated as same-owner relationships on create/update, and scheduler execution also rejects cross-owner or cyclic chains.

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

`routes.assistant_routes.py` owns crew/assistant settings and run-status surfaces that use the scheduler. `TaskScheduler.ensure_assistant_defaults()` currently seeds the personal assistant crew member and pinned assistant session, but no longer auto-creates Morning/Midday/Evening check-in tasks. Existing crew-linked check-in tasks are still rendered and managed when present.

## Notes And Reminders

`routes.note_routes.py` owns notes/todos/reminders. Notes are SQLAlchemy `Note` rows and can include due dates, ordering, images, repeat state, AI classification, source/session provenance, and agent session linkage.

Notes CRUD/reorder/reminder routes resolve the acting owner through `require_user()`: auth-enabled anonymous requests fail closed before hitting owner-scoped queries, while documented no-login/single-user modes still resolve to the compatibility owner path.

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

Email/ntfy failures degrade into channel result fields rather than blocking every reminder path. ntfy and generic webhook reminder URLs run through outbound URL safety checks, with `REMINDER_WEBHOOK_BLOCK_PRIVATE_IPS` controlling whether private/LAN targets are allowed. Reminder dedupe uses owner-scoped cache files under `data/`.

## Agent, Codex, And CLI Surfaces

`do_manage_tasks`, `do_manage_notes`, and `do_manage_calendar` own agent-side writes. `do_manage_calendar` supports batch event creation plus list range aliases (`start`, `start_time`, `start_date`, `range_start`, `from`, `dtstart`, `since`, and matching end aliases), calendar name/short-id lookup, importance/tag aliases, and reminder offsets expressed as numbers, minute/hour words, or common abbreviations such as `min`/`mins`/`hr`/`hrs`. If a model supplies a loose `query`, `date_range`, or `range` without explicit start/end datetimes, `list_events` returns an error asking the caller to resolve the range and call again instead of guessing. Event classification reads `Memory.text` for personal context before LLM classification. `src.tool_index` encodes the reminder policy that notes/todos own reminders while calendar events own time blocks.

Agent native tool owner handling is not uniform today. `do_manage_tasks()` filters only when `owner` is truthy and creates tasks with the passed owner, so `owner=None` can create legacy/null-owner tasks. `do_manage_notes()` list/query behavior distinguishes `None` from `""`, with `None` acting as broader single-user compatibility while `""` filters to empty-owner rows in some paths. `do_manage_calendar()` query helpers filter only when owner is not `None`, while calendar creation routes through the calendar fallback owner for default calendars. These are compatibility behaviors, not a cross-user sharing model.

Chat forwards browser timezone offset so natural-language note/calendar tools can anchor dates to the user clock. Chat can auto-promote note/calendar/reminder intents to agent mode.

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

Natural-language date parsing and timezone behavior are compatibility-sensitive and need route/tool/frontend regression coverage when changed. Request-local timezone context is ephemeral and must not be persisted as user state.

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

Note routes store caller-provided `source`, `session_id`, `image_url`, and agent-session provenance. Upload-backed image URLs are protected when fetched through upload routes, but note image/provenance fields are not server-validated today.

## Testing Coverage

Existing coverage is strongest around CalDAV URL hardening/writeback, bidirectional/pending CalDAV sync markers, CalDAV UID calendar scoping, calendar recurrence/timezone helpers, owner-scoped calendar basics, scheduler restart/cancel/next-run behavior, webhook auth-exemption source shape, note-route unauthenticated fail-closed behavior, notes CLI/tool due-date behavior, calendar reminder abbreviation parsing, task CLI preview, task persona fields, and same-owner chained task validation.

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
- `scripts/odysseus-webhook` appears to reference an older webhook URL shape.
- Assistant default documentation/code comments still mention check-ins that are no longer auto-seeded.
- App backup import/export does not cover the calendar/task/note rows described by this spec.
