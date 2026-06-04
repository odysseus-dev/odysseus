---
name: odysseus
description: Use when the user asks Codex to read or write Odysseus data from a terminal Codex session through the scoped Codex Agent API. Requires ODYSSEUS_URL and ODYSSEUS_API_TOKEN.
---

# Odysseus

Use this skill when a user asks to interact with Odysseus from Codex.

## Configuration

Expect these environment variables:

- `ODYSSEUS_URL`: Base URL for the user's Odysseus instance, for example `http://127.0.0.1:7000`.
- `ODYSSEUS_API_TOKEN`: Scoped API token created in Odysseus Settings > Integrations > Add Integration > Codex Agent.

If either value is missing, do not guess credentials. Tell the user to create a Codex Agent token in Odysseus Settings and expose both values to the terminal session.

## When to use what

- **Reminder ("remind me at 5pm to do X")** → TODO with `due_date`. The due_date IS the reminder — it fires a notification automatically via the user's configured channel (browser/email/ntfy). **Do NOT create a calendar event for a reminder.** Creating a calendar event named "Reminder" does NOT trigger a notification — it's just a time block on the calendar.
- **Calendar event ("meeting at 3pm", "dentist Tuesday 10am")** → calendar event. Use for scheduled time blocks, meetings, appointments, recurring schedules. These show up on the calendar grid; reminders for them are configured separately in Odysseus settings.
- **Note / freeform info ("note that the wifi password is ...")** → memory or todo without a due_date (depending on whether it's a fact about the user or an action item).
- **Persistent fact / preference about the user** → memory.

If the user says "reminder" + a time, default to TODO with due_date. Only switch to calendar if the user explicitly says "calendar", "event", "meeting", "appointment", or describes a time *range*.

## Safety

- All Odysseus data access MUST go through the scoped HTTP API under `/api/codex/*`.
- Check `/api/codex/capabilities` before using a tool surface.
- Treat `403` as an intentional Settings restriction. Do not work around it.
- Do not use SSH, Docker, direct Python imports, SQLite queries, MCP internals, browser cookies, or local files to read/write Odysseus user data.
- Do not call helpers like `do_manage_notes`, email MCP internals, or database sessions directly for user data, even if shell access exists.
- Never send email directly unless the user explicitly asks to send and the token has a send-capable scope.
- Keep actions scoped to the token owner.

## Todos

The Codex API supports todos/checklists:

- `GET /api/codex/todos`
- `POST /api/codex/todos`

Use the bundled helper script when available:

```bash
python3 integrations/codex/scripts/odysseus_api.py capabilities
python3 integrations/codex/scripts/odysseus_api.py todos list
python3 integrations/codex/scripts/odysseus_api.py todos add "Follow up"
```

Supported todo actions are `list`, `add`, `update`, `delete`, and `toggle_item`.

**Reminders (todos with a due date)** — the backend parses natural language. Send `due_date` in the body via the generic POST so the time becomes a structured reminder, NOT a literal substring inside the title. The `todos add TITLE` shortcut only sets the title, so use the POST form for anything with a time:

```bash
python3 integrations/codex/scripts/odysseus_api.py POST /api/codex/todos '{"action":"add","title":"Call dentist","due_date":"tomorrow at 5pm"}'
```

The backend accepts both ISO timestamps and natural language like `"tomorrow 5pm"`, `"next Monday 9am"`, `"in 2 hours"`. It anchors to the user's timezone.

## Email

The Codex API supports scoped email reads:

- `GET /api/codex/emails?folder=INBOX&limit=10&offset=0&filter=all`
- `GET /api/codex/emails/{uid}?folder=INBOX`

Use the bundled helper script when available:

```bash
python3 integrations/codex/scripts/odysseus_api.py emails list 5
python3 integrations/codex/scripts/odysseus_api.py emails read UID
```

If `/api/codex/capabilities` does not show `email.read: true`, do not inspect email. Ask the user to enable Email read in the Codex Agent settings.

## Memory

- `GET /api/codex/memory` — list memories for the token owner.
- `POST /api/codex/memory` — body `{"text": "...", "category": "fact", "source": "user", "session_id": null}`. Requires `memory:write`.
- `DELETE /api/codex/memory/{memory_id}` — remove a memory entry. Requires `memory:write`.

```bash
python3 integrations/codex/scripts/odysseus_api.py GET /api/codex/memory
python3 integrations/codex/scripts/odysseus_api.py POST /api/codex/memory '{"text":"User prefers SI units","category":"preference"}'
```

## Calendar

- `GET /api/codex/calendar/events?start=ISO&end=ISO` — list events in window.
- `POST /api/codex/calendar/events` — body matches `EventCreate` (`summary`, `dtstart`, `dtend`, `all_day`, `description`, `location`, `calendar_href`, `rrule`, `color`). Requires `calendar:write`.
- `DELETE /api/codex/calendar/events/{uid}` — delete event by uid (the value returned in the POST response). Requires `calendar:write`.

## Documents

- `GET /api/codex/documents?search=...&limit=50` — paginated library.
- `GET /api/codex/documents/{doc_id}` — fetch one document.
- `POST /api/codex/documents` — body `{"session_id": "...", "title": "...", "content": "...", "language": "markdown"}`. Requires `documents:write`.
- `DELETE /api/codex/documents/{doc_id}` — delete a document. Requires `documents:write`.

## Email draft + send

- `POST /api/codex/emails/draft` — body matches `SendEmailRequest` (`to`, `cc`, `bcc`, `subject`, `body`, `body_html`, `attachments`, `account_id`, `in_reply_to`, `references`). Requires `email:draft` (or `email:send`).
- `POST /api/codex/emails/send` — same body. Requires `email:send`. Never send without explicit user instruction.

## Forbidden Bypass Pattern

If you are about to reach the Odysseus host/container, import app internals, query the database, or call MCP helper modules directly, stop. Those paths bypass Odysseus Settings and token scopes. Ask the user to enable the relevant Codex Agent tool toggle instead.
