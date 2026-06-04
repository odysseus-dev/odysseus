// Included by agent_loop.rs — `TOOL_SECTIONS` (verbatim from agent_loop.py).
//
// Each tool section is keyed by the tool name it covers. Values are the shipped
// default description strings. Insertion order is observable in the assembled
// prompt, so this is an `IndexMap` (mirrors the Python dict order exactly).
// Stored as a `Lazy<IndexMap<&'static str, &'static str>>`. Raw string literals
// (`r#"..."#`) preserve the embedded backticks / quotes / newlines verbatim.

/// `TOOL_SECTIONS`.
static TOOL_SECTIONS: Lazy<indexmap::IndexMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = indexmap::IndexMap::new();
    m.insert("bash", r#"```bash
<shell command>
```
Run any shell command. Output is returned to you. Use for: installing packages, checking files, git, curl, system info, etc.
For LONG-running commands (package installs, pip/npm, ffmpeg, model downloads, training, builds — anything that may take more than ~20s), make the FIRST line `#!bg` to run it in the BACKGROUND. You get a job id back immediately and are automatically re-invoked with the full output when it finishes — so you never block the chat waiting. Example:
```bash
#!bg
pip install openai-whisper
```
SANDBOX LIMITS: stdin/stdout are pipes, so there is NO interactive terminal — `input()`, `curses`, `termios`, `pygame`, and `tkinter` will all fail. Don't try to RUN interactive terminal games or GUI apps here — verify syntax (`python -c "import py_compile; py_compile.compile('x.py')"`) and tell the user to run it themselves in their own terminal. For anything the USER should play/use interactively (games, UIs, demos), prefer a single self-contained HTML file with `<canvas>` + inline JS — save it via `create_document` with language="html" and tell the user to hit the Run / Preview button (▶) in the document editor toolbar; it renders inline in a sandboxed iframe so the game is playable right there. Works from any machine that can reach the Odysseus UI — no need to copy files out.
NEVER pipe multi-line Python through `python -c "..."` — shell quoting eats real newlines and `\n` arrives as literal backslash-n, which Python parses as a line-continuation error on line 1. To run multi-line code, either use the dedicated `python` tool block above, or save to a file first with a quoted HEREDOC (`cat > /tmp/x.py << 'EOF' ... EOF`) and then `python /tmp/x.py`."#);

    m.insert("python", r#"```python
<python code>
```
Execute Python code. Use for computation, data processing, scripting. NOT for writing code for the user (use create_document for that). Same sandbox limits as bash — no TTY, no GUI, no `input()`; for anything the user should interact with, generate a single HTML file with inline JS instead."#);

    m.insert("web_search", r#"```web_search
<search query>
```
Or with JSON for fresh news:
```web_search
{"query": "<your query>", "time_filter": "day"}
```
Search the web for a SINGLE quick fact/lookup mid-task. For news / "today" / "latest" queries, pass `time_filter` ("day", "week", "month", or "year"). NOT for "research X" / "do research on X" / "look into X" requests — those mean a multi-source DEEP RESEARCH job: use `trigger_research` instead (it runs in the Deep Research sidebar and produces a full report). web_search = one quick query; trigger_research = a researched report."#);

    m.insert("read_file", r#"```read_file
<file path>
```
Read a file and return its contents."#);

    m.insert("write_file", r#"```write_file
<file path>
<file contents>
```
Write content to a file. First line is the path, rest is the content."#);

    m.insert("create_document", r#"```create_document
<title>
<language>
<content>
```
Create a NEW document in the editor panel. Only use when the user explicitly asks for a new file/document. If a document is already open in the editor, the user's request "fix this", "add X", "change Y", etc. refers to THAT document — use edit_document, never create_document."#);

    m.insert("edit_document", r#"```edit_document
<<<FIND>>>
old text to find
<<<REPLACE>>>
new replacement text
<<<END>>>
```
PREFERRED way to change an existing document. Find exact text and replace it. Multiple FIND/REPLACE blocks per call OK. Use this for any edit smaller than a full rewrite — adding a function, fixing a bug, tweaking a section, renaming things. **If a document is open in the editor, treat it as the user's current context: don't ask which file they mean, and don't create a new one — just edit_document the active one.** Do NOT re-send the whole file with update_document for small changes."#);

    m.insert("update_document", r#"```update_document
<entire new content>
```
Replace the ENTIRE active document. ONLY use when you're genuinely rewriting more than half of it from scratch. For any smaller change, use edit_document — echoing back the whole file for a two-line edit wastes tokens and is hard to review."#);

    m.insert("suggest_document", r#"```suggest_document
<<<FIND>>>
text to comment on
<<<SUGGEST>>>
suggested replacement
<<<REASON>>>
why this change improves the code
<<<END>>>
```
Suggest changes with explanations (for review/feedback requests)."#);

    m.insert("generate_image", r#"```generate_image
<prompt>
<model>
<size>
<quality>
```
Generate an image. Line 1 = description, line 2 = model name, line 3 = WxH (e.g. 1024x1024), line 4 = quality."#);

    m.insert("chat_with_model", r#"- ```chat_with_model``` — Ask a DIFFERENT AI model and relay its answer. Line 1 = model name (or 'model@endpoint'), rest = your message. Use when the user says 'ask <model>', 'what does <model> think', or wants to compare/their answer from another model."#);
    m.insert("ask_teacher", r#"- ```ask_teacher``` — Escalate a hard question to a more capable model. Line 1 = model name or 'auto', rest = the question. Use when stuck or need expert knowledge."#);
    m.insert("list_models", r#"- ```list_models``` — Show all available AI models across all endpoints. Use when user asks what models are available."#);
    m.insert("manage_session", r#"- ```manage_session``` — Rename, archive, delete, fork, switch, or `list` chats (the UI calls them 'chats'; 'session' is internal). Line 1 = action (list/switch/rename/archive/unarchive/delete/important/unimportant/truncate/fork), Line 2 = exact chat id from `list_sessions` (or `current` where supported). For delete/archive/truncate, always list first and reuse the exact id; never invent placeholder ids. `switch`/`open` returns a clickable anchor link the user can tap to open the chat — use for "open my X chat"."#);
    m.insert("manage_memory", r#"- ```manage_memory``` — Manage the user's persistent memory (facts, identity, preferences, context that persists across chats). Line 1 = action (list/add/edit/delete/search), rest = content. Use when user says 'remember this', states identity facts like 'my name is <name>' / 'call me <name>' / 'I live in <place>', or asks about stored memories."#);
    m.insert("manage_skills", r#"- ```manage_skills``` — Skill registry (SKILL.md format). Args (JSON): {"action": "list|view|view_ref|search|add|edit|patch|publish|delete", ...}. `list` returns the index of available skills (published + teacher-escalation drafts); `view name=foo` fetches the full SKILL.md; `view_ref name=foo path=...` loads a reference file under the skill directory. For `add`, provide an explicit kebab-case `name` and only report the exact returned name, because storage may normalize or dedupe it. Use this BEFORE doing domain work — there may already be a procedure (published or draft) that prescribes the correct steps. Drafts written by the teacher loop are authoritative guidance even though they're not yet published."#);
    m.insert("manage_tasks", r#"- ```manage_tasks``` — Create and manage scheduled background tasks (recurring AI jobs). Args (JSON): {"action": "list|create|edit|delete|pause|resume|run", ...}"#);
    m.insert("manage_endpoints", r#"- ```manage_endpoints``` — Add, remove, or configure AI model API endpoints. Args (JSON): {"action": "list|add|delete|enable|disable", ...}. Use when user wants to add a new AI provider."#);
    m.insert("manage_mcp", r#"- ```manage_mcp``` — Manage MCP (Model Context Protocol) tool servers — external tools that extend your capabilities. Args (JSON): {"action": "list|add|delete|reconnect|list_tools", ...}"#);
    m.insert("manage_webhooks", r#"- ```manage_webhooks``` — Configure outgoing webhooks (HTTP notifications on events like chat completion). Args (JSON): {"action": "list|add|delete|enable|disable", ...}"#);
    m.insert("manage_tokens", r#"- ```manage_tokens``` — Generate or revoke API access tokens for external integrations. Args (JSON): {"action": "list|create|delete", ...}"#);
    m.insert("manage_documents", r#"- ```manage_documents``` — List, read/open, delete, or tidy documents in the editor panel. Args (JSON): {"action": "list|read|delete|tidy", ...}. `list` returns rows like `[Title](#document-<id>) — lang, size, updated 5m ago` sorted MOST-RECENT FIRST; the user clicks the anchor to open. `read` (aliases: view/open/get) takes `document_id` and returns the content. When the user asks "open/show/read my notes" or "what documents do I have", use this — do NOT shell out, do NOT curl."#);
    m.insert("manage_research", r#"- ```manage_research``` — List, read/open, or delete saved DEEP RESEARCH results from the Library. Args (JSON): {"action": "list|read|delete", "id": "<id>", "search": "..."}. `list` returns rows like `[query](#research-<id>) — N sources` MOST-RECENT FIRST; the user clicks to open. `read` (aliases: open/view/get) takes `id` and returns the report + sources. Use when the user says "open/read/find/delete my research" or "that report". To START new research, use trigger_research instead."#);
    m.insert("manage_settings", r#"- ```manage_settings``` — View/change the REAL app settings (same ones the Settings panel writes) AND turn tools on/off. Change a setting: `{"action":"set","key":"...","value":"..."}` — keys accept friendly aliases, e.g. voice→tts_voice, "search engine"→search_provider, "default model"→default_model, "teacher model"→teacher_model, "task/background model"→task_model, "image quality"→image_quality, "reminder channel"→reminder_channel (browser|email|ntfy), "agent timeout"/"max tool calls"/"token budget". Read: `{"action":"get","key":"..."}`; see all: `{"action":"list"}`; reset one: `{"action":"reset","key":"..."}`. Use this when the user asks to change ANY preference instead of making them open Settings. Secrets/API keys are read-only (tell them to set those in the panel). Tool toggles: `{"action":"disable_tool|enable_tool","tool":"shell"}` (aliases: shell/search/browser/documents/memory/skills/images/tasks/notes/calendar/email), list disabled: `{"action":"list_tools"}`."#);
    m.insert("manage_notes", r#"```manage_notes
{"action": "add", "title": "<short todo>", "due_date": "<natural language or ISO datetime>"}
```
Notes, checklists, AND user reminders. Use this for "create/add/write a note", todos, checklists, and "remind me to X at <time>" — never use memory for note content. For reminders, pair a short `title` (what to do) with a `due_date` (when). `due_date` accepts natural language ("tomorrow at 1pm", "in 2 hours", "next monday 9am") or ISO ("2026-05-12T13:00:00"). Actions: `list`, `add` (title, content OR items:[{text,done}], note_type, color, label, due_date), `update`, `delete`, `toggle_item`."#);
    m.insert("list_email_accounts", r#"- ```list_email_accounts``` — List configured email accounts. Use this before reading/sending when the user says Gmail, work mail, custom domain mail, or any non-default mailbox; pass the returned account name/email/id as `account` to email tools."#);
    m.insert("send_email", r#"```send_email
{"to": "recipient@example.com", "subject": "Re: Your question", "body": "Hi, ...", "account": "gmail"}
```
Send a new email via SMTP. Use `resolve_contact` first if you only have a name. If multiple email accounts exist, call `list_email_accounts` first and pass the chosen `account`."#);
    m.insert("list_emails", r#"```list_emails
{"folder": "INBOX", "max_results": 20, "unread_only": false, "account": "gmail"}
```
List recent emails from a folder, newest first, including read messages by default. Use `list_email_accounts` first when the user names a mailbox/account, then pass `account`. For "last/latest/newest email", call with `max_results: 1` and `unread_only: false`."#);
    m.insert("read_email", r#"- ```read_email``` — Read a specific email by UID. Args (JSON): {"uid": "...", "folder": "INBOX", "account": "gmail"}. Include `account` when the UID came from a named/non-default mailbox."#);
    m.insert("reply_to_email", r#"```reply_to_email
{"uid": "1234", "body": "Sounds good — talk Friday.", "account": "gmail"}
```
SEND a reply email immediately by UID. Do not use this for "open a reply" or "start a reply" — those should use `ui_control` with `open_email_reply <uid> <folder> reply` to open the email draft document. For follow-up requests like "reply ..." after reading/listing email where the user clearly wants to send now, use the exact UID and account from the latest `read_email`/`list_emails` result. Never invent UID `1`. Threads automatically (In-Reply-To/References handled)."#);
    m.insert("bulk_email", r#"```bulk_email
{"action": "delete", "uids": ["10997", "10998"], "folder": "INBOX", "account": "Gmail"}
```
Bulk delete/archive/mark emails. Use this for "delete all those" after listing emails. Pass the exact UIDs and the same account from the list result, then report only the tool result."#);
    m.insert("delete_email", r#"- ```delete_email``` — Delete one email by UID. Args (JSON): {"uid":"...", "folder":"INBOX", "account":"Gmail"}. For multiple messages use bulk_email."#);
    m.insert("archive_email", r#"- ```archive_email``` — Archive one email by UID. Args (JSON): {"uid":"...", "folder":"INBOX", "account":"Gmail"}. For multiple messages use bulk_email."#);
    m.insert("mark_email_read", r#"- ```mark_email_read``` — Mark one email read/unread. Args (JSON): {"uid":"...", "read":true, "folder":"INBOX", "account":"Gmail"}. For multiple messages use bulk_email."#);
    m.insert("resolve_contact", r#"- ```resolve_contact``` — Look up a contact's email by name. Searches CardDAV address book + sent email history. Args (JSON): {"name": "..."}. Use BEFORE send_email when the user gives only a name."#);
    m.insert("manage_contact", r#"- ```manage_contact``` — Create/update/delete/list CardDAV contacts. Args (JSON): {"action": "list|add|update|delete", "name": "...", "email": "...", "uid": "..."}. Use only for explicit address-book/contact requests with contact details. Do NOT use for user identity facts like 'my name is <name>'; save those with manage_memory. For update/delete, call action=list first to get the uid."#);
    m.insert("manage_calendar", r#"```manage_calendar
{"action": "create_event", "summary": "<event title>", "dtstart": "<natural language or ISO datetime>"}
```
Calendar event management (CalDAV). Actions: `list_events`, `create_event`, `update_event`, `delete_event`, `list_calendars`. For `create_event`: {summary, dtstart, dtend?, duration?, calendar?, location?, description?, reminder_minutes?}. `dtstart` accepts natural language ("tomorrow at 1pm", "in 2 hours", "next monday 9am") or ISO ("2026-05-12T13:00:00"). If `dtend` omitted, defaults to dtstart+1h (or +1d when `all_day: true`). If the user asks for a reminder/alarm before the event, pass `reminder_minutes` as an integer; do not write reminder text into the event description and do NOT also call `manage_notes` for the same reminder because calendar reminders are routed through Notes automatically. `calendar` accepts a name ("Main") or short-id prefix."#);
    m.insert("create_session", r#"- ```create_session``` — Create a new chat. Line 1 = chat name, line 2 = model name. Use for background/parallel work."#);
    m.insert("list_sessions", r#"- ```list_sessions``` — List chats sorted MOST-RECENT FIRST (the UI calls them 'chats') with clickable chat-title links. Output includes a relative "last active" timestamp per row, so the first row is the user's most recent chat. Content = optional filter keyword (matches chat name). When answering, preserve the `[title](#session-id)` links exactly; do not convert them into plain text."#);
    m.insert("send_to_session", r#"- ```send_to_session``` — Send a message to another session. Line 1 = session_id, rest = message. Use for orchestrating work across sessions."#);
    m.insert("search_chats", r#"- ```search_chats``` — Search across all chat history. Use when user asks 'did we discuss X?' or 'find the conversation about Y'."#);
    m.insert("pipeline", r#"- ```pipeline``` — Run a multi-step AI pipeline. Args (JSON) with ordered steps, each specifying a model and prompt. Use for complex workflows."#);
    m.insert("ui_control", r#"- ```ui_control``` — Control the UI: toggle tools on/off, OPEN PANELS, open email reply drafts, switch models, change themes. Commands: `toggle <name> on/off` (names: bash/shell, web/search, research, incognito, document_editor/documents), `open_panel <name>` (panels: documents, gallery, email, sessions, notes, memories/brain, skills, settings, cookbook), `open_email_reply <uid> <folder> <reply|reply-all|ai-reply>` (opens an email compose document, does NOT send), `set_mode agent/chat`, `switch_model <name>`, `set_theme <preset>`, `create_theme <name> <bg> <fg> <panel> <border> <accent>` (optional key=val for advanced colors AND background effects: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num>, bgEffectSize=<num>, frosted=true|false). "open documents" / "open library" / "show gallery" / "open inbox" / "open notes" / "open cookbook" all map to `open_panel <name>`. Theme presets: dark, light, midnight, paper, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, organs, lavender, gpt, claude, cute."#);
    m.insert("list_served_models", r#"- ```list_served_models``` — Show what the Cookbook (LLM-serving subsystem) is currently running. NO args. Use this for ANY 'what's running' / 'what's serving' / 'show my cookbook' / 'is anything up' query. DO NOT shell out (`ps aux`, `docker ps`, etc.) — this tool is the source of truth. Failed serve tasks include recent logs plus diagnosis/retry suggestions; use those suggestions to call `serve_model` again with an adjusted command when appropriate."#);
    m.insert("stop_served_model", r#"- ```stop_served_model``` — Stop a running model server. Args (JSON): {"session_id": "<from list_served_models>"}. Use for 'kill my cookbook' / 'stop the model' / 'shut down vLLM'."#);
    m.insert("download_model", r#"- ```download_model``` — Download a HuggingFace model. Args (JSON): {"repo_id": "Qwen/Qwen3-8B", "host": "user@gpu-box"?, "include": "*Q4_K_M*"?}."#);
    m.insert("serve_model", r#"- ```serve_model``` — Start serving a model with vLLM / SGLang / llama.cpp / Ollama / Diffusers. Args (JSON): {"repo_id": "...", "cmd": "vllm serve ... --port 8000" or "python3 -m sglang.launch_server ... --port 30000" or "python3 scripts/diffusion_server.py --model diffusers/stable-diffusion-xl-1.0-inpainting-0.1 --port 8100", "host": "user@gpu-box"?}. For image/inpaint/diffusion models, use the `scripts/diffusion_server.py` command exactly. After launch, call `list_served_models`; if it returns a diagnosis with an adjusted command, retry with that command."#);
    m.insert("list_downloads", r#"- ```list_downloads``` — Show in-progress HuggingFace model downloads (filters Cookbook tasks/status to downloads only). NO args. Use for 'what's downloading' / 'show my downloads' / 'check download progress'."#);
    m.insert("cancel_download", r#"- ```cancel_download``` — Cancel an in-progress download. Args (JSON): {"session_id": "<from list_downloads>"}. Use for 'cancel the download' / 'kill the download'."#);
    m.insert("search_hf_models", r#"- ```search_hf_models``` — Search HuggingFace for models. Args (JSON): {"query": "qwen 8b", "limit": 10?}. Use for 'find a model for X' / 'search huggingface' / 'what models are there for Y'."#);
    m.insert("list_cached_models", r#"- ```list_cached_models``` — List models already on disk. Args (JSON, all optional): {"host": "ajax or user@gpu-box"?, "model_dir": "/data/models,/extra"?}. Friendly Cookbook server names work. Use for 'what models do I have' / 'show cached models' / 'is X downloaded'."#);
    m.insert("app_api", r#"```app_api
{"action": "call", "method": "GET", "path": "/api/cookbook/gpus"}
```
GENERIC LOOPBACK to ANY Odysseus internal endpoint. Use this whenever the user wants something the UI can do but there's NO named tool for it. Every UI button hits some /api/* endpoint — you can hit the same one. Auth is handled automatically.

**Discovery first.** If you're not sure of the path, call `{"action":"endpoints","filter":"<keyword>"}` (e.g. filter='calendar' or 'gallery' or 'theme') to list available endpoints with their methods + summaries. Then call with action='call'.

**Common surfaces (use `endpoints` with filter to discover the full set per domain):**
- Calendar: `/api/calendar/events`, `/api/calendar/calendars`, `/api/calendar/events/{uid}`
- Cookbook: `/api/cookbook/gpus`, `/api/cookbook/state`, `/api/cookbook/setup`, `/api/cookbook/kill-pid`, `/api/cookbook/packages`, `/api/cookbook/hf-latest`, `/api/model/cached`
- Gallery: `/api/gallery/list`, `/api/gallery/delete`, `/api/gallery/{id}`, `/api/gallery/albums`
- Library / Documents: list all via `/api/documents/library`; docs in a session via `/api/documents/{session_id}`; a single doc via `/api/document/{id}` (singular) and its history via `/api/document/{id}/versions` (singular). Note the plural `/api/documents/...` vs singular `/api/document/{id}` split.
- Memory: `/api/memory`, `/api/memory/{id}`, `/api/memory/search`
- Notes: `/api/notes`, `/api/notes/{id}`
- Tasks: `/api/tasks`, `/api/tasks/{id}/run`, `/api/tasks/notifications`
- Sessions: `/api/sessions`, `/api/session/{id}`, `/api/session/{id}/truncate`
- Themes: `/api/prefs/themes`, `/api/prefs/custom-themes`
- Settings: `/api/settings`, `/api/prefs/{key}`
- Research: `/api/research/start`, `/api/research/tasks`, `/api/research/report/{id}`
- Compare: `/api/compare/sessions`, `/api/compare/start`
- Email: use named email tools (`list_email_accounts`, `list_emails`, `read_email`, `send_email`, `reply_to_email`). Do NOT use `/api/email/accounts`; it is owner-filtered in tool context and may falsely return empty.
- Endpoints (model providers): `/api/endpoints`, `/api/endpoints/{id}`

Body for POST/PUT/PATCH goes in `body` (object). Query params in `query` (object). Returns the parsed JSON of the response.

**When to prefer named tools over app_api:** if a named wrapper exists (list_email_accounts, list_emails, read_email, manage_calendar, manage_notes, list_served_models, etc.) USE IT — it has nicer output formatting and clearer schema. Reach for `app_api` only when there's no wrapper for what you need.

Blocked paths (refused for safety): /api/auth/, /api/users/, /api/tokens/, /api/admin/, /api/backup/restore, /api/email/accounts."#);

    m
});
