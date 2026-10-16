"""
RAG-based tool selection for agent mode.

Instead of injecting all tool descriptions into the system prompt,
embed them in a ChromaDB collection and retrieve only the top-K
relevant ones per user message.
"""

import logging
import hashlib
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from src.embedding_lanes import (
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
    dedupe_results,
    migrate_legacy_collection,
)

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

logger = logging.getLogger(__name__)

# Tools that are ALWAYS included regardless of retrieval results.
# Keep this to the small set of tools that agents should be able to reach on
# any turn. Specialist tools (documents, email, cookbook/model serving,
# settings, etc.) are still injected by retrieval or keyword intent.
ALWAYS_AVAILABLE = frozenset({
    # Core execution, inspection, and external lookup capabilities. These are
    # available to every model; the runtime tool policy still governs whether
    # a particular call may execute.
    "bash",
    "python",
    "read_file",
    "grep",
    "glob",
    "ls",
    "web_search",
    "web_fetch",
    "pdf_extract",
    "inspect_media",
    "extract_text",
    "transcribe_media",
    "youtube_tool",
    # Memory is ambient — "remember this" can follow any message regardless
    # of topic. Without this, RAG drops it and the agent falls back to
    # app_api /api/memory/add which fails with 422 on first attempt.
    "manage_memory",
    # Ask the user a multiple-choice question for a decision/clarification.
    # Always reachable so the agent can pause and ask at any point.
    "ask_user",
    # Write back to the active plan (tick steps done / revise) during execution.
    "update_plan",
})

# Literal intent hints for scripts where ``\b`` is not a useful token
# boundary.  Embedding retrieval is multilingual but approximate; a request
# must not lose the only tool capable of its explicit operation merely because
# another domain (for example email) ranks higher.  Keep these phrases about
# product concepts, not particular prompts or entities.
NON_LATIN_LITERAL_TOOL_HINTS = {
    "manage_tasks": (
        "定时任务", "计划任务", "排程任务", "定時任務", "計劃任務", "排程任務",
        "スケジュール済みタスク", "定期タスク", "予約タスク",
        "예약 작업", "예약된 작업", "정기 작업",
    ),
    "manage_calendar": (
        "日历事件", "日曆事件", "行事曆", "カレンダー予定", "캘린더 일정",
    ),
    "manage_notes": (
        "待办清单", "待辦清單", "チェックリスト", "할 일 목록",
    ),
}

# Tools that the Personal Assistant always has access to during scheduled
# check-ins and proactive tasks, in addition to RAG-selected tools.
ASSISTANT_ALWAYS_AVAILABLE = frozenset({
    "list_email_accounts", "list_emails", "search_emails", "read_email", "scan_email_unsubscribes", "scan_spam", "unsubscribe_email", "send_email", "reply_to_email", "draft_email", "draft_email_reply", "ai_draft_email_reply",
    "bulk_email", "archive_email", "delete_email", "mark_email_read", "download_attachment", "block_sender", "manage_email_state",
    "manage_calendar", "manage_notes", "manage_tasks",
    "manage_memory", "web_search", "read_file",
    "create_document", "update_document",
    "resolve_contact", "search_chats",
    "api_call",  # For Miniflux/Gitea/Linkding/etc. integrations
    # Core UI control (toggles, open panels, switch model/mode, themes).
    # Always available so vague follow-ups ("now make it playful", "make it
    # darker") that don't repeat a theme/UI keyword still keep the tool in
    # reach — without it the model narrates instead of acting.
    "ui_control",
})

COLLECTION_NAME = "odysseus_tool_index"

# ── Tool description registry ──
# Each tool gets a searchable description that helps retrieval.
# These are richer than the system prompt one-liners — they're for embedding.
BUILTIN_TOOL_DESCRIPTIONS: Dict[str, str] = {
    "bash": "Run shell commands on the server. Install packages, git operations, builds, system info, process management. Prefer a dedicated tool whenever one fits the job (file read/write/edit, search, listing); use bash only for what no dedicated tool covers. Do not use for web lookup/search; use web_search or web_fetch when web tools are available.",
    "host_shell": "Run shell commands on the TUI host through an explicitly advertised host bridge, not in the backend Docker container. Use for LAN, local IP, subnet, mDNS, Tailscale fallback, SSH target discovery, arp/nmap/ip route diagnostics when backend runtime is container-limited.",
    "python": "Execute Python code for computation, data processing, math, scripting, and parsing. Not for writing code for the user. Prefer a dedicated tool for reading, writing, or searching files; use python only for what no dedicated tool covers. Do not use for web lookup/search; use web_search or web_fetch when web tools are available.",
    "web_search": "Private quick web lookup through Odysseus' configured search backend, normally SearXNG. Use for facts, current events, latest/current information, and ordinary 'search the web/look up/find online' requests. Use this instead of browser navigation to Google/DuckDuckGo/Bing or bash/curl/python/requests scraping. NOT for 'research X' / 'do research on X' requests — those are deep-research jobs (use trigger_research). web_search = one query; trigger_research = a full researched report in the sidebar.",
    "get_weather": "Get current weather and a three-day forecast for a city or place from Open-Meteo without an API key. Use for weather lookups before web_search.",
    "web_fetch": "Fetch and read the text content of a specific URL/website the user names (e.g. 'check example.com', 'open this link'). Use when you have a concrete URL; for open-ended lookups use web_search instead.",
    "pdf_extract": "Extract focused, source-attributed passages and exact table values from an online PDF or task-local /workspace/*.pdf. Use for arXiv papers, reports, manuals, PDF tables, evaluation metrics, and multi-document PDF extraction. Prefer this over Python requests, curl, downloading, pdftotext, or guessing. Include target model names, metrics, and table headings in query.",
    "youtube_tool": "Read YouTube-specific data without fighting the JS page: video comments, transcripts, metadata, or latest video from a channel. Use for YouTube comments/transcript/channel latest-video tasks.",
    "private_browser": "Trusted metadata for an existing server-registered browser session only. Page/document reads and interactions are unavailable because the configured producer cannot guarantee exact target binding. No model batch or raw browser commands. Use web_search or web_fetch for supported web access.",
    "inspect_media": "Inspect local workspace images, SVGs, videos, and PDF pages with the current multimodal model. Samples bounded timestamped video frames uniformly, at scene cuts, or from temporally diverse motion peaks; renders SVG to PNG; exports stills or clips; concatenates ranges; changes clip speed while preserving audio pitch; and renders query-relevant PDF pages. Prefer these native operations over raw ffmpeg. Increase max_dimension only for small visual details; saved exports keep source quality.",
    "extract_text": "Extract exact visible text, confidence, and pixel centers from a local workspace image with Odysseus local OCR. Use for screenshots, scans, labels, numbers, receipts, and text-location tasks; use inspect_media for general visual understanding.",
    "transcribe_media": "Transcribe dialogue, narration, names, and spoken timing from a local audio or video file with Odysseus local Whisper. Returns [START --> END] TEXT segments and always persists them to a workspace text file. For a named chapter, question, scene, or topic, locate its boundaries and restrict filtering to that interval. This handles audio speech; combine with inspect_media for audiovisual tasks or visually burned-in subtitles.",
    "read_file": "Read a file from disk and return its contents. View source code, config files, logs. Supports an optional line range (offset/limit) for large files.",
    "grep": "Search file CONTENTS for a regex across a directory tree (ripgrep-backed, honours .gitignore). Returns file:line:match. Use to find where code/symbols/strings live — prefer over bash grep.",
    "glob": "Find FILES by glob pattern (e.g. '**/*.py'), newest first. Use to locate files by name/extension — prefer over bash find/ls.",
    "ls": "List a directory's entries (folders then files with sizes). Use to see what's in a folder — prefer over bash ls.",
    "get_workspace": "Return the absolute path of the active workspace folder the user is working in. File tools are confined to it; the shell starts there but is not sandboxed. Call this first when the user refers to 'the project'/'the code'/'this folder' without giving a path, instead of asking them.",
    "write_file": "Write/create or fully rewrite a file ON DISK (source code, configs, project files). Use for new files or full rewrites — NOT create_document (editor panel) and NOT a bash heredoc. For SVG content, write a .svg first and use inspect_media to render .png/.jpg; do not put SVG XML in a raster-named file.",
    "edit_file": "Edit an existing file ON DISK by exact string replacement (fix a bug, change a function). Shows a diff. The tool for changing files on disk — NOT edit_document (editor panel) and NOT bash sed/heredoc.",
    "apply_patch": "Apply a multi-file patch to source files ON DISK. Use for implementation, refactors, and bug fixes where several edits belong together. Workspace-confined and returns a diff. Prefer over bash redirects/heredocs/sed.",
    "todowrite": "Maintain a structured task list for the current coding session. Use for multi-step code work: inspect, edit, test, and mark statuses current.",
    "create_document": "Create a new document in the editor panel. For code, articles, text content longer than 15 lines, unless an already-open document/email draft is the obvious target. If an email compose draft is open, edit that draft instead of creating another document.",
    "edit_document": "Preferred tool for editing an existing document — targeted find-and-replace. Use for any small change: add a function, fix a bug, tweak a section, rename things.",
    "update_document": "Replace the entire active document content. ONLY for full rewrites (>50% changed). Do not use for small edits — use edit_document instead.",
    "suggest_document": "Suggest changes to the active document with explanations. For code review, proofreading, feedback requests.",
    "generate_image": "Generate an AI image from a text prompt. Specify model, size, and quality. Art, illustrations, photos.",
    "chat_with_model": "Send a message to a different AI model. Compare responses, get specialized help, delegate tasks.",
    "ask_teacher": "Ask a more capable model for help with a difficult problem. Escalate complex tasks.",
    "pipeline": "Run a multi-step AI pipeline with multiple models. Chain tasks together in sequence.",
    "list_models": "List all available AI models and their endpoints.",
    "manage_session": "Chat management: rename, archive, delete, or fork chats (the UI calls these 'chats'; internally 'sessions'). Use for 'rename my chats', 'rename this chat', 'archive/delete a chat'.",
    "manage_memory": "Memory management: list, add, edit, delete, or search persistent memories. For facts about the USER (their name, preferences, where they live). NOT for info about ANOTHER person — addresses, phones, emails belonging to a contact go in manage_contact, not memory.",
    "manage_skills": "Skill management: add, update, publish, or search reusable skills/presets.",
    "manage_tasks": "Scheduled task management: list, create, edit, delete, pause, resume, or run recurring and one-off future tasks.",
    "manage_endpoints": "Endpoint management: list, add, delete, enable, or disable model API endpoints.",
    "manage_mcp": "MCP server management: list, add, delete, reconnect servers, or list available tools.",
    "manage_webhooks": "Webhook management: list, add, delete, enable, or disable webhooks.",
    "api_call": "Call a configured API integration by name (Home Assistant, Miniflux, Gitea, Linkding, Jellyfin, RSS reader, git forge, bookmark manager, smart home, or any other registered service). Make a GET/POST/PUT/PATCH/DELETE request to the integration's endpoint path, with an optional JSON body. Use whenever the user asks to query or control one of their connected integrations/services.",
    "manage_tokens": "API token management: list, create, or delete API access tokens.",
    "manage_documents": "List, read, delete, or tidy documents in the editor panel. action='list' returns clickable rows (most-recent first) so the user can open any doc by clicking. action='read' (aka view/open/get) with document_id returns the content; supports offset=<N> + limit=<N> to page through large docs (response includes next_offset when more remains, so you can keep calling with offset=next_offset). action='delete' with document_id removes a doc (only way to delete). Use this for ANY 'show/read/list/open my documents/docs/files/notes' request — never shell or curl.",
    "manage_research": "List, read/open, or delete saved DEEP RESEARCH results from the Library. action='list' returns clickable [query](#research-<id>) rows (most-recent first). action='read' (aka open/view/get) with id returns the report + sources. action='delete' with id removes it. Use this for ANY 'open/read/find/delete my research / that report / the research on X' request. NOTE: this is for EXISTING research; to START new research use trigger_research.",
    "manage_settings": "Change ANY real app setting (the ones the Settings panel writes) so the user never has to open it: TTS voice/provider/speed, STT, search engine + result count, default/teacher/task/utility/vision/image/research models, image quality, reminder channel (browser/email/ntfy), agent timeout/tool-call budget, and more. action=set with key (friendly aliases ok: voice, 'search engine', 'default model', 'teacher model', 'image quality', 'reminder channel'...) + value; get/list/reset too. Also turns tools off globally (disable_tool) and lists disabled ones (list_tools); re-enabling is only done by the user in Settings > Agent Tools. Secrets/API keys are read-only. Use for any 'change my…/set my…/use X for…/turn on…' preference request.",
    "create_session": "Create a new chat with a name and model.",
    "list_sessions": "List all chats with their metadata (the UI calls these 'chats'). Use for 'list my chats', 'rename all my chats' (list first, then manage_session to rename each).",
    "send_to_session": "Send a message to another chat. Cross-chat communication.",
    "search_chats": "Search past session transcripts across chats.",
    "ask_user": "Ask the user a question to get a decision or clarification. Use this when the task is genuinely ambiguous and the answer changes what you do next — pick between approaches, confirm an assumption, choose among options, or request required missing data — instead of guessing. Provide a clear `question` and 2-6 `options` (each with a short `label`, optional `description`). For open-ended missing data such as an exact calendar date, include an `Exact date` option and ask the user to type it; do not invent arbitrary dates. Omit `multi`/keep it false unless the question explicitly permits choosing multiple options. Calling this ENDS your turn: the user sees clickable buttons and their choice arrives as your next message. Don't use it for things you can decide from context or sensible defaults, or for irreversible-action confirmation if a dedicated flow exists.",
    "update_plan": "Write back to the ACTIVE PLAN while executing an approved plan: mark steps done or revise them. After finishing a step call this with the full checklist and that step marked done; when the user asks to change the plan call it with the revised checklist. Always pass the COMPLETE markdown checklist (`- [ ]` / `- [x]`), not a diff. The user's docked plan window updates live. No effect when there is no active plan.",
    "ui_control": "Control the UI and toggle tools on/off. Use this to turn off / turn on / disable / enable individual tools and features: shell (bash), search (web), research, browser, documents, incognito. Open panels (documents library, gallery, calendar/schedule, email inbox, sessions, notes, memories/brain, skills, settings, theme, cookbook) via `open_panel <name>`. For calendar views use `open_panel calendar month|week|year|agenda [YYYY-MM or YYYY-MM-DD]`; if the user says 'that month/week' after a calendar listing, carry over the listed range, e.g. `open_panel calendar month 2026-09`. Also switches between chat/agent modes, changes the current model, and applies/creates themes.",
    "list_email_accounts": "List configured email accounts and default status. Use before reading or sending mail when the user mentions Gmail, work mail, custom domain mail, another mailbox, or asks to compare/check multiple inboxes.",
    "list_emails": "List emails for a folder/account, newest first, including read messages by default. Shows subject, sender, date, UID, account, and AI summary. Check inbox, find emails needing replies. Supports account from list_email_accounts for Gmail/work/custom mailboxes. For last/latest/newest email, use max_results=1 and unread_only=false.",
    "search_emails": "Search email subjects, senders, and message bodies by topic or person across the configured mailbox folders. Use for a named topic, then pass the returned UID to read_email when the user asks for the full message.",
    "read_email": "Read the full content of a specific email by UID or Message-ID. View email body, check details. Supports account from list_email_accounts when the UID belongs to a non-default mailbox.",
    "download_attachment": "Open/download an attachment from an email by UID and attachment index. Use after read_email when the user asks to open, read, inspect, summarize, or answer questions about an attached PDF/text/CSV.",
    "scan_email_unsubscribes": "Scan recent email headers for spam/newsletter unsubscribe candidates. Review-only; returns UIDs, reasons, and mailto/web unsubscribe methods.",
    "scan_spam": "Review recent inbox messages for likely spam or phishing. Returns candidates with UID, sender, subject, score, and reasons. Does not move/delete/block; ask for confirmation before bulk actions.",
    "unsubscribe_email": "Execute an approved unsubscribe action by UID. Mailto methods are sent/staged; web URL methods return exact browser/web instructions.",
    "send_email": "Send a new email immediately via SMTP or approval staging. Use only when the user explicitly says to send now, deliver now, approve/send, or otherwise skip review. For ordinary 'send/write/email someone saying X' requests, use draft_email so Odysseus opens a reviewable email document. Provide recipient, subject, body, and optional account from list_email_accounts. For replying to a thread use reply_to_email only for explicit send-now replies.",
    "draft_email": "Create a new Odysseus email draft document for review. Does not send. Use for normal 'write/email/send a message saying X' requests unless the user explicitly says to send now.",
    "draft_email_reply": "Create a threaded Odysseus reply draft document by email UID. Does not send. Use for normal 'reply/write back/send an email back saying X' requests so the user can review in the document editor.",
    "ai_draft_email_reply": "Generate an AI reply and create an Odysseus email draft document. Does not send. Use when the user asks to draft a reply but does not dictate the exact body.",
    "reply_to_email": "SEND a reply email immediately by UID. Do not use for write/draft/open/start reply requests; use draft_email_reply so the user can review in the document editor. Only use when the user explicitly says to send now, deliver now, approve/send, or otherwise skip review. For send requests, use the exact UID and account from latest read_email/list_emails output; never invent UID 1. Threads automatically with In-Reply-To/References, prefixes Re:, marks original as Answered.",
    "archive_email": "Move an email out of the inbox into the Archive folder. Use after handling messages you want to keep but get out of the way.",
    "delete_email": "Delete an email — moves to Trash by default, or expunges permanently with permanent=true.",
    "mark_email_read": "Mark an email as read or unread by toggling the \\Seen flag.",
    "bulk_email": "Perform one action on many emails at once. Use for delete all those, archive these, mark all read, move spam to junk. Takes explicit UIDs from list_emails or all_unread=true. Always pass account for Gmail/work/custom mailbox results.",
    "block_sender": "Block an email sender after user approval and optionally move matching current messages to Junk/Spam. Use after listing/scanning suspected spam and confirming with the user.",
    "manage_email_state": "Compact reversible email state manager: favorite/unfavorite, done/undone, unarchive, list blocked senders, unblock sender. Use mark_email_read for read/unread.",
    "resolve_contact": "Look up a contact's email address by name. Searches CardDAV address book and sent email history. Use when the user says 'message [name]', 'email [name]', or 'send to [name]' without an email address.",
    "manage_contact": "Save / update / delete / list / search address-book contacts (CardDAV). Use for info about ANOTHER person — name, email, phone, postal address. Args: action=list|search|find|add|update|delete, query, name, email, phones, address, uid (from list/search). For 'save this for <person>' / address pastes / phone numbers next to a name, this is the right tool — NOT manage_memory. Do NOT use for facts about the USER ('my name is X'); those are manage_memory.",
    "manage_notes": "Create and manage notes and checklists (Google Keep-style). ALWAYS use this for note/todo/checklist/reminder creation — NEVER hit /api/notes via app_api. Accepts natural-language `due_date` like 'tomorrow at 9am' or '11pm today' (parsed in the USER'S timezone). The due_date IS the reminder — it fires a notification at that time, so do NOT also create a calendar event for the same reminder. Set colors, labels, pin, archive. Do NOT use manage_memory for note content.",
    "manage_calendar": "Calendar event management: preserve titles exactly, resolve relative dates from current local date, use local ISO wall time, and ask_user if date/time/target is missing. List, create, update, delete. Each event can carry a tag/category (event_type — work/personal/health/travel/meal/social/admin/other) and importance (low/normal/high/critical). Resolve today/tomorrow using the Current date and time context, then use ISO datetimes in the user's local wall time; supports all-day events. If create/update lacks a required date, time, or target event, call ask_user once instead of guessing. For update_event, only pass event_type/tag/category/type when the user explicitly asks to tag, retag, categorize, or clear the tag; otherwise omit it so manually tagged events keep their existing tag. Use rrule only for explicit recurrence; examples: every Monday = FREQ=WEEKLY;BYDAY=MO, first and last Monday of each month = FREQ=MONTHLY;BYDAY=1MO,-1MO, second Thursday of each month = FREQ=MONTHLY;BYDAY=2TH, last Sunday of each month = FREQ=MONTHLY;BYDAY=-1SU. For update_event pass rrule='' to remove repeats. For event reminders/alarms, pass reminder_minutes; this creates the Notes reminder, so do not also call manage_notes for the same reminder.",
    "download_model": "Download a HuggingFace model to a local or remote server. Specify repo_id (e.g. 'Qwen/Qwen3-8B'), optional server host, and optional include filter for specific files.",
    "serve_model": "Start serving a model with vLLM, SGLang, llama.cpp, Ollama, or Diffusers. cmd MUST start with the binary directly — e.g. `vllm serve /mnt/HADES/models/Qwen3.5-397B-A17B-AWQ --port 8003 --tensor-parallel-size 8 …`. NEVER prefix with `cd …`, `source …`, or chain with `&&`/`||` — those get rejected by the validator. The venv activation (env_prefix) and CUDA env are added automatically from the target host's saved settings. For image/inpainting/diffusion use python3 scripts/diffusion_server.py --model <repo> --port 8100. After launch, call list_served_models for readiness/errors and retry suggestions. If serve_model fails with 'Invalid characters in cmd', simplify to the bare binary + args.",
    "list_served_models": "List currently running model servers in the Cookbook — shows status (loading, ready, idle, error), model name, port, throughput, and serve failure diagnosis/retry suggestions. Use when the user asks 'what's running', 'show my cookbook', 'which models are up', 'what's serving'.",
    "stop_served_model": "Stop a running model server in the Cookbook by session ID or model name. Use when the user says 'kill my cookbook', 'stop the model', 'kill the serve', 'shut down vLLM', 'cancel the running model'.",
    "tail_serve_output": "Read the actual tmux stderr/traceback of a cookbook serve/download task. Use to debug WHY a task is `crashed`/`error` (compute_89 nvcc mismatch, OOM, missing kernels, wrong attention backend, etc.) so you can call serve_model with adjusted flags. Pass session_id from list_served_models; tail defaults to 300, bump if the error references 'see root cause above'.",
    "list_downloads": "List in-progress HuggingFace model downloads in the Cookbook. Shows model name, phase, percent, session ID. Use for 'what's downloading', 'show my downloads', 'check download progress'.",
    "cancel_download": "Cancel an in-progress model download by tmux session ID. Use for 'cancel the download', 'stop downloading X', 'kill the download'. Call list_downloads first to get the session_id.",
    "search_hf_models": "Search Hugging Face Hub models through the official HF API (e.g. 'qwen 8B', 'latest official Qwen', 'llama-3 instruct', 'Qwen AWQ'). Returns repo IDs, HF URLs, update times, likes, and download counts. Use for 'find a model', 'link me the latest model', 'search huggingface for X', 'what models are there for Y'. For official/provider models use official_only=true or author=<namespace>. Do not include quant/community variants unless the user asks for AWQ/GGUF/GPTQ/FP8/Q4/etc.",
    "list_cached_models": "List models already cached on disk locally or on a remote host. Accepts friendly Cookbook server names like workstation. Use for 'what models do I have', 'show cached models', 'is X downloaded', 'list my models'. Avoids re-downloading.",
    "list_serve_presets": "List saved Cookbook serve presets (templates with model+host+port+cmd). Call this BEFORE raw serve_model when the user asks to launch a known model manually.",
    "serve_preset": "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user already saved. Use for 'run stable diffusion 3.5', 'serve vllm-qwen', 'start the inpaint model' — preset-name matches the user's UI labels.",
    "adopt_served_model": "Register an existing tmux model server (one started manually or outside the cookbook flow) into Cookbook tracking AND add it as a chat endpoint. Use when the user (or a previous turn) launched something via ssh+tmux and now wants it visible in the UI, stoppable via stop_served_model, and usable in the model picker.",
    "list_cookbook_servers": "List the cookbook's configured servers (remote GPU boxes + local) and which is the current default. Use this BEFORE download_model/serve_model when the user didn't name a host — to decide where to run, or to ask the user which server when ambiguous. Downloads/serves default to the cookbook's selected server, NOT localhost.",
    "app_api": "Generic loopback to allowed Odysseus internal endpoints. Use this when the user wants something the UI can do but there's no named tool for it. Covers calendar, gallery, library/documents, memory, notes, tasks, settings, research, compare, cookbook GPUs/state — allowed UI buttons hit /api/* endpoints and you can hit them too. Sensitive auth/user/admin/shell paths and host-control Cookbook mutation routes are blocked; do NOT use app_api for shell commands, package installs, engine rebuilds, or PID signalling. Use named command tooling for shell commands. action='endpoints' with filter=<keyword> lists available endpoints. action='call' takes method+path+body. Hits same routes the UI uses — auth flows free. NOTE: themes are NOT an API endpoint — use the ui_control tool (create_theme / set_theme), not app_api. SESSIONS/CHATS: do NOT use app_api for these — GET /api/sessions returns EMPTY for tool calls (it's owner-filtered and tool calls authenticate as a different identity). EMAIL ACCOUNTS: do NOT use /api/email/accounts via app_api; use list_email_accounts, list_emails, and read_email instead. To list/rename/archive/delete/fork chats use the list_sessions and manage_session tools instead.",
    "edit_image": "Edit an image in the gallery: upscale (increase resolution), remove background (rembg), inpaint (fill selected area), or harmonize (blend edits). Specify image ID and action.",
    "trigger_research": "Start a deep research job on any topic — appears in the Deep Research sidebar, streams progress, produces a detailed report. Use for 'research X', 'look into Y', 'do deep research on Z', 'investigate'. NOT a scheduled task — it runs now and surfaces in the sidebar.",
    "manage_bg_jobs": "Inspect and control detached background `bash` jobs (the ones started with a `#!bg` marker). action='list' shows this chat's jobs (id/status/age/command); action='output' returns a job's captured output so far (check on a long-running job, or re-read a finished one); action='kill' stops a runaway job by id. Use for 'is the background job done', 'check on that job', 'show the build output', 'kill the background job', 'stop the bg task'. output/kill need a job_id from list.",
}


class ToolIndex:
    """ChromaDB-backed tool index for RAG-based tool selection."""

    def __init__(self):
        self._lanes = build_embedding_lanes(COLLECTION_NAME)
        if not self._lanes:
            raise RuntimeError("No embedding lanes available")
        self._embedder = self._lanes[0].client
        self._collection = next(
            (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
            self._lanes[0].collection,
        )
        migrate_legacy_collection(COLLECTION_NAME, self._lanes)
        self._fingerprint = ""
        self._mcp_generation = -1
        self._healthy = True
        logger.info("ToolIndex initialized (lanes=%s)", [lane.name for lane in self._lanes])

    @property
    def healthy(self):
        return self._healthy

    @property
    def embedding_lanes(self):
        """Read-only lane view for native semantic indexes such as skills."""
        return tuple(self._lanes)

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if not self._lanes:
            return []
        vecs = self._lanes[0].encode(texts)
        if np is not None:
            return np.array(vecs, dtype=np.float32).tolist()
        return [list(v) for v in vecs]

    def index_builtin_tools(self):
        """Index all built-in tool descriptions."""
        docs = []
        ids = []
        metadatas = []
        for name, desc in BUILTIN_TOOL_DESCRIPTIONS.items():
            doc_text = f"Tool: {name}\n{desc}"
            docs.append(doc_text)
            ids.append(f"builtin_{name}")
            metadatas.append({"tool_name": name, "tool_type": "builtin"})

        if not docs:
            return

        # Drop any stale builtin_* entries that aren't in the current
        # registry (e.g. removed tools like the old vault_* set).
        # Without this, upsert leaves them in place and RAG keeps
        # surfacing tools that no longer exist.
        indexed = False
        for lane in self._lanes:
            try:
                existing = lane.collection.get(where={"tool_type": "builtin"})
                existing_ids = (existing or {}).get("ids") or []
                stale = [i for i in existing_ids if i not in set(ids)]
                if stale:
                    lane.collection.delete(ids=stale)
                    logger.info(f"Pruned {len(stale)} stale builtin tool entries from {lane.name} index")
            except Exception as e:
                logger.debug(f"Stale-pruning skipped for {lane.name}: {e}")

            try:
                lane.collection.upsert(
                    ids=ids,
                    documents=docs,
                    embeddings=lane.encode(docs),
                    metadatas=metadatas,
                )
                indexed = True
            except Exception as e:
                logger.warning("Builtin tool indexing failed in %s lane: %s", lane.name, e)
        if not indexed:
            self._healthy = False
            raise RuntimeError("Builtin tool indexing failed in all embedding lanes")
        self._fingerprint = hashlib.sha256(
            ",".join(sorted(BUILTIN_TOOL_DESCRIPTIONS.keys())).encode()
        ).hexdigest()
        logger.info(f"Indexed {len(docs)} built-in tools")

    def index_mcp_tools(self, mcp_mgr, disabled_map: Optional[Dict] = None):
        """Index MCP tool descriptions. Call after MCP servers connect/disconnect."""
        if not mcp_mgr:
            return

        # Get current MCP generation to avoid redundant reindexing
        gen = getattr(mcp_mgr, '_generation', 0)
        if gen == self._mcp_generation:
            return

        # Remove old MCP entries
        for lane in self._lanes:
            try:
                existing = lane.collection.get(where={"tool_type": "mcp"})
                if existing and existing["ids"]:
                    lane.collection.delete(ids=existing["ids"])
            except Exception:
                pass

        # Get current MCP tools
        try:
            all_tools = mcp_mgr.get_tool_descriptions_for_prompt(disabled_map or {})
        except Exception:
            all_tools = ""

        if not all_tools:
            self._mcp_generation = gen
            return

        # Parse MCP tool descriptions from the prompt text
        docs = []
        ids = []
        metadatas = []
        current_server = ""
        for line in all_tools.strip().split("\n"):
            line = line.strip()
            # Track which server section we're in (for context in descriptions)
            if line.startswith("**") and line.endswith(":**"):
                current_server = line.strip("*: ")
            elif line.startswith("- ") and ":" in line:
                # Format: "- tool_name: description"
                name_desc = line[2:].split(":", 1)
                if len(name_desc) == 2:
                    name = name_desc[0].strip()
                    desc = name_desc[1].strip()
                    # Include server identity in the indexed text so RAG can
                    # distinguish "list_emails for server-a" from "list_emails for server-b"
                    server_ctx = f" (server: {current_server})" if current_server else ""
                    doc_text = f"Tool: {name}{server_ctx}\n{desc}"
                    docs.append(doc_text)
                    ids.append(f"mcp_{name}")
                    metadatas.append({"tool_name": name, "tool_type": "mcp"})

        if not docs:
            self._mcp_generation = gen
            return

        indexed = False
        for lane in self._lanes:
            try:
                lane.collection.upsert(
                    ids=ids,
                    documents=docs,
                    embeddings=lane.encode(docs),
                    metadatas=metadatas,
                )
                indexed = True
            except Exception as e:
                logger.warning("MCP tool indexing failed in %s lane: %s", lane.name, e)
        if not indexed:
            logger.warning("MCP tool indexing failed in all embedding lanes")
            return
        self._mcp_generation = gen
        logger.info(f"Indexed {len(docs)} MCP tools")

    def retrieve(self, query: str, k: int = 8) -> List[str]:
        """Retrieve the top-K most relevant tool names for a query."""
        rows = []
        lane_priority = {LANE_CUSTOM: 0, LANE_FASTEMBED: 1}
        for lane in self._lanes:
            try:
                count = lane.count()
                if count == 0:
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([query]),
                    n_results=min(k, count),
                    include=["metadatas", "distances"],
                )
                if not results or not results.get("metadatas"):
                    continue
                distances = results.get("distances") or []
                for list_idx, meta_list in enumerate(results["metadatas"]):
                    distance_list = distances[list_idx] if list_idx < len(distances) else []
                    for idx, meta in enumerate(meta_list):
                        name = meta.get("tool_name", "")
                        if name:
                            distance = distance_list[idx] if idx < len(distance_list) else 1.0
                            rows.append({
                                "tool_name": name,
                                "score": round(1.0 - distance, 4),
                                "embedding_lane": lane.name,
                            })
            except Exception as e:
                logger.warning("Tool retrieval failed in %s lane: %s", lane.name, e)
        rows.sort(key=lambda row: (-row["score"], lane_priority.get(row["embedding_lane"], 99)))
        return [row["tool_name"] for row in dedupe_results(rows, id_key="tool_name", limit=k)]

    # Structural recurring-schedule intent. Typo-resilient (matches "every dya"
    # via "every <word>"), and catches bare clock times ("at 7:30 am", "7am").
    # Used in addition to the literal keyword hints below.
    _SCHEDULE_RE = re.compile(
        r"\bevery\s+\w+"                                       # every day / dya / morning / monday / 2 hours
        r"|\b(?:daily|nightly|hourly|weekly|monthly)\b"
        r"|\beach\s+(?:day|morning|night|week|hour|evening)\b"
        r"|\bat\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b",  # at 7:30 am / at 7am
        re.I,
    )
    _CALENDAR_EVENT_RE = re.compile(
        r"\b(?:calendar|event|meeting|appointment|reservation|dinner|lunch|breakfast|pickup|pick\s+up)\b",
        re.I,
    )
    _WEB_RE = re.compile(
        r"https?://|www\.|\b(?:visit|open|fetch|check|read)\s+(?:this\s+)?(?:url|link|site|website|page)\b",
        re.I,
    )

    # Keyword hints: if the query mentions these words, force-include the tools.
    _KEYWORD_HINTS = {
        # NOTE: "tell" was removed from this set. It fired on any "tell me ..."
        # request (e.g. "visit <url> and tell me the title"), force-including the
        # whole email toolset and crowding out the relevant tools — the model then
        # believed it had only email tools and refused web/other tasks (#1707).
        frozenset({"email", "emails", "mail", "mails", "gmail", "googlemail", "message", "messages", "send", "reply", "replies", "inbox", "unread"}):
            {"list_email_accounts", "list_emails", "search_emails", "read_email", "download_attachment", "scan_email_unsubscribes", "scan_spam", "unsubscribe_email", "send_email", "reply_to_email", "bulk_email", "block_sender", "manage_email_state", "delete_email", "archive_email", "mark_email_read", "resolve_contact", "ui_control"},
        frozenset({"calendar", "event", "meeting", "schedule", "appointment"}):
            {"manage_calendar"},
        # Detached background `bash` jobs (#!bg): check on / read output / kill.
        frozenset({"background job", "background jobs", "bg job", "bg jobs",
                   "background task", "is the job done", "check the job",
                   "check on that job", "job output", "kill the job",
                   "kill the background", "stop the background", "running job"}):
            {"manage_bg_jobs"},
        frozenset({"lan", "local network", "local ip", "subnet", "router",
                   "tailscale", "ssh", "arp", "nmap", "mdns", "avahi"}):
            {"host_shell"},
        frozenset({"youtube", "youtu.be", "yt", "video comments", "youtube comments", "transcript"}):
            {"youtube_tool", "web_search", "web_fetch", "private_browser"},
        frozenset({"local video", "video file", "watch this video", "movie clip", "video.mp4", "video.webm", "inspect image"}):
            {"inspect_media"},
        frozenset({"note", "todo", "reminder", "remind", "checklist", "remember to"}):
            {"manage_notes"},
        frozenset({"remember this", "remember that", "save this as memory", "store this in memory"}):
            {"manage_memory"},
        # Skill-library requests must survive a retrieval miss.  The skill
        # index is injected into context, but that context is not permission
        # to call the registry; keep the named registry tool in the schema.
        frozenset({"skill", "skills", "tdd", "skill library", "skill index"}):
            {"manage_skills"},
        # Chat/session management. "rename" alone maps to documents below, so a
        # request like "rename the last 12 sessions/chats" needs these session
        # keywords to surface the right tools (NOT app_api — /api/sessions is
        # owner-filtered and returns empty for tool calls).
        frozenset({"sessions", "my chats", "these chats", "those chats",
                   "chat history", "rename chat", "rename session",
                   "rename the chat", "rename my chat", "rename the session",
                   "archive chat", "archive session", "delete chat",
                   "delete session", "fork chat", "fork session",
                   "name the chats", "name my chats", "rename them"}):
            {"list_sessions", "manage_session"},
        frozenset({"recurring", "every day", "every hour", "every morning",
                   "every evening", "every night", "every week", "each morning",
                   "daily task", "background task", "scheduled task", "schedule a",
                   "automatically", "auto-summarize", "auto summarize",
                   "cron", "periodically", "on a schedule", "set up a task",
                   "create a task", "summarize my inbox every", "remind me every"}):
            {"manage_tasks"},
        frozenset({"contact", "address", "phone", "who is"}):
            {"resolve_contact", "manage_contact"},
        frozenset({"save contact", "add contact", "new contact", "update contact",
                   "edit contact", "delete contact", "remove contact",
                   "save this person", "add to contacts", "save to contacts",
                   # "add <name> to (my) contacts" — words between 'add' and
                   # 'contacts' break the literal phrase match above, so anchor
                   # on the tail.
                   "to my contacts", "to contacts", "to address book",
                   # "save this for <person>" / "save it for <person>" — the user
                   # is storing info on a known person without using the literal
                   # word 'contact'. Catches the address/phone-paste pattern.
                   "save this for", "save it for", "save for",
                   "save this one for", "save that for",
                   # Postal-address-like signals
                   "postal code", "zip code", "street address",
                   "mailing address", "their address"}):
            {"manage_contact"},
        # "Ask another model" intent → chat_with_model relays to a
        # different model and returns its answer. ask_teacher escalates
        # to the configured teacher. (second_opinion was removed.)
        frozenset({"ask gpt", "ask claude", "ask gemini", "ask deepseek",
                   "ask minimax", "ask qwen", "ask the", "ask another model",
                   "what does", "what would", "second opinion", "other model",
                   "different model", "compare answers", "compare models",
                   "delegate to", "have model"}):
            {"chat_with_model", "ask_teacher", "list_models"},
        # Deep research intent (incl. common typo "reserach")
        frozenset({"web search", "search the web", "search online", "look up",
                   "find info online", "find information online",
                   "find info", "find information", "online about",
                   "on the internet", "google", "latest", "current", "news",
                   "weather", "forecast", "stock price", "price of"}):
            {"web_search", "web_fetch", "get_weather"},
        frozenset({"research", "reserach", "reasearch", "look into", "investigate",
                   "deep dive", "deep research", "find out about", "study up on",
                   "report on", "do research", "look up everything"}):
            {"trigger_research"},
        # Settings-change intent — "change my…/set my…/use X for…/turn on…".
        frozenset({"change my", "set my", "use the voice", "change the voice",
                   "my voice", "tts voice", "search engine", "default model",
                   "teacher model", "task model", "background model", "image quality",
                   "reminder channel", "send reminders to", "remind me by",
                   "speak faster", "speak slower", "agent timeout", "token budget",
                   "max tool calls", "use this model for", "use that model for",
                   "my settings", "change setting", "change a setting", "set setting",
                   "writing style", "reply style", "email writing style",
                   "preference", "preferences", "configure"}):
            {"manage_settings", "ui_control"},
        # API-integration intent → the api_call tool. Mirrors the agent-loop
        # "integrations" domain so api_call still surfaces on the retrieval and
        # keyword-fallback paths (not just the deterministic domain seed) when a
        # user names a connected service.
        frozenset({"api_call", "api call", "integration", "integrations",
                   "home assistant", "homeassistant", "miniflux", "gitea",
                   "linkding", "jellyfin"}):
            {"api_call"},
        # Managing EXISTING research in the Library — open/read/find/delete.
        frozenset({"my research", "the research", "research on", "open research",
                   "read research", "find research", "delete research",
                   "remove research", "list research", "my reports", "the report",
                   "saved research", "research library", "past research",
                   "research i did", "research about"}):
            {"manage_research", "trigger_research"},
        # Document edit/update intent
        frozenset({"edit", "change", "fix", "rewrite", "update",
                   "replace", "add a", "tweak", "modify", "rename", "paragraph",
                   "section", "line", "the doc", "the docs", "the document", "the documents", "in the doc", "in the docs", "in document"}):
            {"edit_document", "update_document", "create_document", "suggest_document"},
        # Document deletion / management — include generic open/find/read/show
        # verbs + file/doc synonyms so "open my <X>", "find the <X>", "delete
        # <X>" reach manage_documents even without the literal word "document".
        frozenset({"delete this doc", "delete the doc", "delete document",
                   "remove document", "remove the doc", "trash", "list document", "list documents",
                   "list doc", "list docs", "all my docs", "my document", "my documents", "my doc", "my docs", "my files",
                   "open the", "open my", "open document", "open doc", "find the",
                   "find my", "find document", "read the", "read my", "show me the",
                   "show my", "the file", "my file", "the report", "the write-up",
                   "the writeup", "saved document", "in my library", "in the library"}):
            {"manage_documents", "edit_document"},
        # Theme / UI control intent
        frozenset({"theme", "color scheme", "colors of the ui", "make it dark",
                   "make it light", "make the ui", "switch theme", "change theme",
                   "dark mode", "light mode", "toggle"}):
            {"ui_control"},
        # Cookbook / model serving intent — user says "kill cookbook",
        # "stop the model", "what's running", etc.
        frozenset({"cookbook", "kill cookbook", "stop cookbook",
                   "stop the model", "kill the model", "kill my model",
                   "what's running", "what is running", "whats running",
                   "running models", "running model", "running server",
                   "shut down vllm", "shutdown vllm", "stop vllm",
                   "stop serving", "kill serve", "cancel serve"}):
            {"list_served_models", "stop_served_model"},
        # Cookbook serve / launch / preset / server selection
        frozenset({"serve", "launch", "spin up", "start the model", "run the model",
                   "debug launch", "launch command", "drivers", "driver",
                   "preset", "presets", "which server", "what servers",
                   "gpu box", "cookbook server", "vllm", "sglang", "mlx", "llama.cpp",
                   "on the server", "on the gpu"}):
            {"serve_preset", "serve_model", "list_serve_presets",
             "list_cookbook_servers", "list_cached_models"},
        # Cookbook/model downloads. Avoid bare "download" here: requests like
        # "download receipts from email" should route to email tools, not model
        # download/cookbook tools.
        frozenset({"download model", "download a model", "download the model",
                   "downloading model", "model download", "model downloads",
                   "cancel download", "stop download", "kill download",
                   "what's downloading", "download progress", "pull model", "grab model"}):
            {"list_downloads", "cancel_download", "download_model",
             "list_cookbook_servers"},
        # HuggingFace search + cached model browse
        frozenset({"huggingface", "hugging face", "hf search",
                   "find a model", "search models", "search for a model",
                   "models for", "best model for"}):
            {"search_hf_models", "list_cached_models", "app_api"},
        frozenset({"cached models", "list models", "my models",
                   "what models do i have", "is it downloaded",
                   "do i have", "already downloaded", "on disk"}):
            {"list_cached_models", "search_hf_models"},
        # Tool on/off / panel open intent — user says "turn off shell",
        # "disable search", "open library", "show gallery", etc.
        frozenset({"turn off", "turn on", "disable", "enable",
                   "shell off", "shell on", "search off", "search on",
                   "research off", "research on", "incognito",
                   "switch model", "change model", "set mode", "agent mode", "chat mode",
                   "open library", "open documents", "open gallery", "open calendar",
                   "open schedule", "open email",
                   "open inbox", "open settings", "open memories", "open memory",
                   "open skills", "open notes", "open chats", "open sessions",
                   "show library", "show gallery", "show calendar", "show schedule",
                   "show inbox", "show settings",
                   "show memory", "show memories", "show skills", "show notes",
                   "show chats", "show sessions", "show documents"}):
            {"ui_control"},
        # Document creation intent
        frozenset({"write a", "create a doc", "draft", "compose", "poem", "story",
                   "essay", "outline", "letter"}):
            {"create_document", "edit_document", "update_document"},
    }

    def get_tools_for_query(
        self, query: str, k: int = 8, always_include: Optional[Set[str]] = None
    ) -> Set[str]:
        """Get the set of tool names to include for a given user query."""
        base = set(always_include or ALWAYS_AVAILABLE)
        retrieved = self.retrieve(query, k=k)
        base.update(retrieved)
        # Keyword-based force-include for common intents. Match on word
        # boundaries, not raw substrings, so short hints like "fix", "line",
        # "serve", "reply" or "unread" don't fire inside unrelated words
        # ("prefix", "deadline"/"online", "observe"/"reserve", "replying",
        # "unreadable"). Same word-boundary matching used in topic_analyzer.
        ql = query.lower()
        for keywords, tools in self._KEYWORD_HINTS.items():
            if any(re.search(rf"\b{re.escape(kw)}\b", ql) for kw in keywords):
                base.update(tools)
        for tool, phrases in NON_LATIN_LITERAL_TOOL_HINTS.items():
            if any(phrase in query for phrase in phrases):
                base.add(tool)
        # Structural scheduling-intent detection — typo-resilient (the literal
        # keyword "every day" misses "every dya"). Catches "every <word>",
        # daily/nightly/etc., or a clock time like "at 7:30 am" / "7am", which
        # all signal a recurring/scheduled task. Force-include manage_tasks so
        # the agent can actually create the cron job instead of fumbling.
        if self._SCHEDULE_RE.search(ql) and not self._CALENDAR_EVENT_RE.search(ql):
            base.add("manage_tasks")
        # URL/site requests need web tools even when embedding retrieval is
        # stubbed/unavailable. Keep this structural, not always-on, so trivial
        # prompts do not drag web schemas into the agent context.
        if self._WEB_RE.search(query):
            base.update({"web_search", "web_fetch"})
        # Hardware-aware model recommendations are fulfilled by the Cookbook
        # hwfit API, not by the generic endpoint/model catalog. Keep app_api in
        # the caller-selected surface for natural variants such as "best model
        # to run on my hardware", which do not contain the literal keyword
        # phrase "best model for" above.
        if (
            re.search(r"\b(?:best|recommend(?:ed)?|suitable|compatible|fit)\b", ql)
            and re.search(r"\bmodels?\b", ql)
            and re.search(
                r"\b(?:my|this|the|current)\s+(?:hardware|machine|computer|pc|server|system)\b"
                r"|\b(?:gpu|vram|ram)\b",
                ql,
            )
        ):
            base.add("app_api")
        if re.search(r"https?://\S+(?:\.pdf\b|/pdf/)|\bPDFs?\b", query, re.I):
            base.add("pdf_extract")
        # Hard steering: when the query is a clear "save info about a specific
        # person" pattern (address paste + name, phone next to a name, etc.),
        # the model has been observed defaulting to manage_memory even with
        # manage_contact in the toolset. Pull memory out for these queries so
        # the model literally cannot pick it. ALWAYS_AVAILABLE includes
        # manage_memory by default; we override that here.
        # The "for/to <word>" check needs to allow lowercase names (users
        # don't always capitalize) but filter out timing/pronoun stopwords
        # so "save this for later" / "save for tomorrow" don't trigger.
        _CONTACT_STOPWORDS_AFTER_FOR = {
            "later", "tomorrow", "yesterday", "now", "then", "today",
            "tonight", "me", "us", "you", "him", "her", "them", "myself",
            "yourself", "next", "this", "that", "the", "a", "an", "future",
            "real", "use", "uses", "another", "future", "reference",
        }
        # Regex catches "save (this|it|the|her|...|<noun>) for <name>" / "to my
        # contacts" patterns. More forgiving than literal-keyword matching —
        # 'save this address for Alex' uses one extra word between 'save' and
        # 'for' that breaks the contiguous 'save this for' phrase.
        save_for_match = re.search(
            r"\bsave\b(?:\s+\w+){0,3}\s+(?:for|to)\s+([A-Za-z]+)",
            ql,
        )
        # "to my contacts", "into my contacts", "in my address book", etc.
        to_contacts = re.search(r"\b(?:to|in|into)\s+(?:my\s+)?(?:contacts|address\s+book)\b", ql)
        # Possessive: "save (his|her|their) (address|phone|email|number) ..."
        # — strong contact signal even without "for <name>". Force-include
        # manage_contact here too since the keyword fallback misses this
        # construction.
        possessive_contact = re.search(
            r"\bsave\b(?:\s+\w+){0,2}\s+(?:his|her|their)\s+(?:address|phone|number|email|contact|details)",
            ql,
        )
        word_after = (
            save_for_match.group(1).lower() if save_for_match else None
        )
        contact_only_signal = (
            (save_for_match is not None
             and word_after is not None
             and word_after not in _CONTACT_STOPWORDS_AFTER_FOR)
            or to_contacts is not None
            or possessive_contact is not None
        )
        if possessive_contact is not None:
            base.add("manage_contact")
        if contact_only_signal and "manage_contact" in base:
            base.discard("manage_memory")
        return base


# ── Singleton ──

_tool_index: Optional[ToolIndex] = None
_last_attempt = 0.0
_RETRY_INTERVAL = 30.0
_init_lock = threading.Lock()
_status_lock = threading.Lock()
_status: Dict[str, Any] = {
    "state": "idle",
    "ready": False,
    "attempts": 0,
    "started_at": None,
    "completed_at": None,
    "duration_ms": None,
    "builtin_tools": 0,
    "fingerprint": "",
    "lanes": [],
    "error_type": None,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_status(**updates: Any) -> None:
    with _status_lock:
        _status.update(updates)


def _lane_readiness(index: ToolIndex) -> List[Dict[str, Any]]:
    """Return bounded, non-secret lane facts for readiness diagnostics."""
    rows: List[Dict[str, Any]] = []
    for lane in getattr(index, "_lanes", []) or []:
        try:
            stats = lane.stats()
        except Exception:
            stats = {
                "name": getattr(lane, "name", "unknown"),
                "healthy": False,
                "count": 0,
            }
        rows.append({
            key: stats.get(key)
            for key in ("name", "model", "dimension", "fingerprint", "count", "healthy")
            if stats.get(key) is not None
        })
    return rows


def get_tool_index_status() -> Dict[str, Any]:
    """Return process-local ToolIndex lifecycle state without initializing it."""
    with _status_lock:
        status = dict(_status)
        status["lanes"] = [dict(row) for row in _status.get("lanes", [])]
    if status["state"] == "degraded":
        retry_after = max(0.0, _RETRY_INTERVAL - (time.monotonic() - _last_attempt))
        status["retry_after_seconds"] = round(retry_after, 3)
    return status


def get_ready_tool_index() -> Optional[ToolIndex]:
    """Return the initialized index without waiting or triggering cold start."""
    with _status_lock:
        ready = bool(_status.get("ready"))
    index = _tool_index
    if ready and index is not None and index.healthy:
        return index
    return None


def tool_index_prewarm_enabled(environ: Optional[Dict[str, str]] = None) -> bool:
    """Whether startup should initialize semantic tool retrieval in background."""
    source = os.environ if environ is None else environ
    value = str(source.get("ODYSSEUS_TOOL_INDEX_PREWARM", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_tool_index() -> Optional[ToolIndex]:
    """Get or create the singleton ToolIndex. Returns None if unavailable."""
    global _tool_index, _last_attempt

    if _tool_index is not None and _tool_index.healthy:
        return _tool_index

    # Startup prewarm and a first user request can arrive together. Serialize the
    # expensive model/collection initialization so both paths share one result.
    with _init_lock:
        if _tool_index is not None and _tool_index.healthy:
            return _tool_index

        now = time.monotonic()
        if now - _last_attempt < _RETRY_INTERVAL:
            return None
        _last_attempt = now
        previous = get_tool_index_status()
        started = time.monotonic()
        _set_status(
            state="warming",
            ready=False,
            attempts=int(previous.get("attempts") or 0) + 1,
            started_at=_utc_now(),
            completed_at=None,
            duration_ms=None,
            error_type=None,
        )

        try:
            candidate = ToolIndex()
            candidate.index_builtin_tools()
            _tool_index = candidate
            _set_status(
                state="ready",
                ready=True,
                completed_at=_utc_now(),
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                builtin_tools=len(BUILTIN_TOOL_DESCRIPTIONS),
                fingerprint=getattr(candidate, "_fingerprint", ""),
                lanes=_lane_readiness(candidate),
                error_type=None,
            )
            return _tool_index
        except Exception as e:
            logger.warning(f"ToolIndex init failed (will retry in {_RETRY_INTERVAL}s): {e}")
            _tool_index = None
            _set_status(
                state="degraded",
                ready=False,
                completed_at=_utc_now(),
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                builtin_tools=0,
                fingerprint="",
                lanes=[],
                error_type=type(e).__name__,
            )
            return None


def prewarm_tool_index(query: str = "run a shell command and inspect files") -> Dict[str, Any]:
    """Initialize and exercise semantic tool retrieval for startup readiness."""
    index = get_tool_index()
    if index is None:
        return get_tool_index_status()

    selected = index.retrieve(query, k=3)
    if not selected:
        _set_status(state="degraded", ready=False, error_type="RetrievalProbeEmpty")
        logger.warning("ToolIndex prewarm retrieval returned no tools")
    else:
        _set_status(
            state="ready",
            ready=True,
            completed_at=_utc_now(),
            lanes=_lane_readiness(index),
            error_type=None,
        )
    status = get_tool_index_status()
    status["probe_tools"] = selected
    return status


def reset_tool_index() -> None:
    """Clear the singleton so embedding endpoint changes rebuild tool lanes."""
    global _tool_index, _last_attempt
    with _init_lock:
        _tool_index = None
        _last_attempt = 0.0
        with _status_lock:
            _status.clear()
            _status.update({
                "state": "idle",
                "ready": False,
                "attempts": 0,
                "started_at": None,
                "completed_at": None,
                "duration_ms": None,
                "builtin_tools": 0,
                "fingerprint": "",
                "lanes": [],
                "error_type": None,
            })
    try:
        from src.skill_index import reset_skill_index_cache

        reset_skill_index_cache()
    except Exception:
        pass
