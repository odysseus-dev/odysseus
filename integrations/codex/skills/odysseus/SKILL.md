---
name: odysseus
description: Use when the user asks Codex to read or write Odysseus data (todos, email, calendar, memory, documents) or to launch/monitor/stop a Cookbook model-serve task through the scoped Codex Agent API. Requires ODYSSEUS_URL and ODYSSEUS_API_TOKEN.
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

## Cookbook serve (debug a failing model launch)

The Cookbook surface lets you reproduce what a human would do in Odysseus → Cookbook: read which serves are running, tail their tmux output to see why they crashed, edit the launch command, relaunch, kill a stuck one. Use this when the user is debugging a model server that won't come up (compute-capability errors, OOM, missing kernels, wrong attention backend, etc.).

- `GET /api/codex/cookbook/tasks` — list active serve/download/install tasks (sessionId, type, status, repo_id, remoteHost, payload._cmd). Requires `cookbook:read`.
- `GET /api/codex/cookbook/servers` — list configured servers (name, host, port, env type + path, model dirs). Requires `cookbook:read`.
- `GET /api/codex/cookbook/cached?host=<NAME>` — list models already cached on the named server (HF cache + Ollama + extra modelDirs). Call BEFORE `serve` to see what's already on disk. Requires `cookbook:read`.
- `GET /api/codex/cookbook/presets` — list saved serve presets (model + host + port + cmd). The user's saved preset usually has a working cmd — try `preset NAME` before composing your own. Requires `cookbook:read`.
- `GET /api/codex/cookbook/output/{session_id}?tail=400` — read the last N lines of the task's persistent log file (preferred) or tmux pane (fallback). The log file persists across vllm crashes, so this returns the actual Python traceback even after the bash prompt + neofetch banner overwrites the pane. Default tail=400. Requires `cookbook:read`.
- `POST /api/codex/cookbook/serve` — launch a serve task. Body matches `ServeRequest`: `{ repo_id, cmd, remote_host?, ssh_port?, env_prefix?, gpus?, platform? }`. The `cmd` is validated: leading binary must be `vllm`/`python3`/`sglang`/`llama-server`/`ollama`/`node`/`npx`. NEVER prefix with `cd …`, `source …`, or chain with `&&`/`||`/`;`/`$(...)` — the validator rejects shell metacharacters. The venv activation (`env_prefix`) is added automatically from the host's saved settings, so pass the bare binary + args. Requires `cookbook:launch`.
- `POST /api/codex/cookbook/preset/{name}` — launch a saved preset by name. Reuses the working cmd + host the user already saved. Requires `cookbook:launch`.
- `POST /api/codex/cookbook/adopt` — register an externally-launched tmux session into cookbook tracking. Body: `{ tmux_session, model, host?, port? }`. Use this when serve_model rejected a cmd and you fell back to direct ssh+tmux — without adoption, the session is invisible to the UI. Requires `cookbook:launch`.
- `POST /api/codex/cookbook/stop/{session_id}` — kill the tmux session. Requires `cookbook:launch`.

```bash
python3 ~/plugins/odysseus/scripts/odysseus_api.py cookbook tasks
python3 ~/plugins/odysseus/scripts/odysseus_api.py cookbook output serve-abc12345 400
python3 ~/plugins/odysseus/scripts/odysseus_api.py cookbook stop serve-abc12345
python3 ~/plugins/odysseus/scripts/odysseus_api.py cookbook serve \
  /mnt/HADES/models/Qwen3.5-397B-A17B-AWQ \
  "vllm serve /mnt/HADES/models/Qwen3.5-397B-A17B-AWQ --host 0.0.0.0 --port 8001 --tensor-parallel-size 8 --max-model-len 262144 --gpu-memory-utilization 0.90 --dtype auto --max-num-seqs 8 --trust-remote-code --enable-expert-parallel --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3" \
  pewds@192.168.1.12
```

**Debug loop pattern:** `tasks` → `output SID 600` (find root cause; request larger `tail` if it references "above") → `stop SID` → `serve repo "new cmd"` → wait ~20s → `output` on the new sessionId.

**Hard limits this surface enforces:**
- `cookbook serve` cmd allowlist + shell-metacharacter rejection.
- `cookbook stop` requires sessionIds matching `[a-zA-Z0-9_-]+`.
- Agent CAN spawn GPU-pinning long-lived processes — always `cookbook stop` your previous attempt before relaunching.

## Forbidden Bypass Pattern

If you are about to reach the Odysseus host/container, import app internals, query the database, or call MCP helper modules directly, stop. Those paths bypass Odysseus Settings and token scopes. Ask the user to enable the relevant Codex Agent tool toggle instead.
