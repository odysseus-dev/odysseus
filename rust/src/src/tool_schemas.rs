// src/tool_schemas.rs  <- src/tool_schemas.py
//! tool_schemas.py
//!
//! OpenAI-compatible function tool schemas and the converter that turns
//! native function calls back into ToolBlocks for the execution pipeline.
//!
//! Extracted from agent_tools.py to keep schema definitions separate from
//! tool parsing / execution logic.
//!
//! `_TOOL_NAME_MAP` is imported from `tool_parsing` (its canonical owner),
//! exactly as the Python `from src.tool_parsing import _TOOL_NAME_MAP`. In Rust
//! there is no module-load cycle even though `tool_parsing` also calls back into
//! `function_call_to_tool_block` here.

use once_cell::sync::Lazy;
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::io;

use crate::pylog as logger;
use crate::src::agent_tools::{ToolBlock, TOOL_TAGS};
use crate::src::tool_parsing::_TOOL_NAME_MAP;

// ---------------------------------------------------------------------------
// json.dumps(...) parity
// ---------------------------------------------------------------------------
//
// `function_call_to_tool_block` builds tool content with bare `json.dumps(...)`
// (no kwargs). CPython's default separators are `", "` (after items) and `": "`
// (after keys) and `ensure_ascii=True`. `serde_json::to_string` is compact (no
// spaces) and writes raw UTF-8, so it would diverge from the Python content
// strings (which flow back into the execution pipeline verbatim). This helper
// reproduces the Python bytes exactly. (Mirrors core/atomic_io.rs's private
// PyCompactFormatter + ensure_ascii, kept local here to avoid widening that
// module's API.)
struct PyCompactFormatter;

impl serde_json::ser::Formatter for PyCompactFormatter {
    fn begin_array_value<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }
    fn begin_object_key<W>(&mut self, writer: &mut W, first: bool) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }
    fn begin_object_value<W>(&mut self, writer: &mut W) -> io::Result<()>
    where
        W: ?Sized + io::Write,
    {
        writer.write_all(b": ")
    }
}

/// CPython `json.dumps` defaults to `ensure_ascii=True`: every non-ASCII code
/// point is escaped as `\uXXXX` (astral chars as a UTF-16 surrogate pair),
/// lowercase hex.
fn ensure_ascii(s: &str) -> String {
    if s.is_ascii() {
        return s.to_string();
    }
    let mut out = String::with_capacity(s.len());
    let mut buf = [0u16; 2];
    for ch in s.chars() {
        if (ch as u32) < 0x80 {
            out.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut buf) {
                out.push_str(&format!("\\u{:04x}", unit));
            }
        }
    }
    out
}

/// `json.dumps(value)` with CPython's default separators + `ensure_ascii`.
fn py_json_dumps<T: Serialize>(value: &T) -> String {
    let mut buf = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, PyCompactFormatter);
    // Serialization of a serde_json::Value / Map never fails on a Write to Vec.
    value.serialize(&mut ser).expect("json.dumps serialize");
    let s = String::from_utf8(buf).expect("json.dumps utf8");
    ensure_ascii(&s)
}

// ---------------------------------------------------------------------------
// OpenAI-compatible function tool schemas
// ---------------------------------------------------------------------------
//
// Built with `serde_json::json!`. `serde_json`'s `preserve_order` feature is ON
// (see Cargo.toml), so object key order is preserved => byte-identical to the
// Python `json.dumps` of the same literal. The Vec order is observable
// (callers iterate the schema list), so it mirrors the Python list exactly.
pub static FUNCTION_TOOL_SCHEMAS: Lazy<Vec<Value>> = Lazy::new(|| {
    vec![
        json!({
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a shell command (full access)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute"}
                    },
                    "required": ["command"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "python",
                "description": "Execute Python code to compute a result or test something",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Python code to execute"}
                    },
                    "required": ["code"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Quick single web lookup for a fact or current event mid-task. NOT for 'research X' / 'do research on X' — those are deep-research jobs; use trigger_research instead.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "time_filter": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "Optional freshness filter for news/latest/today queries"}
                    },
                    "required": ["query"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch and read the text content of a specific URL the user names (e.g. 'check example.com', 'what's on this page <url>'). Use when you already have a concrete URL/domain. NOT for open-ended searches (use web_search) or 'research X' jobs (use trigger_research).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL or domain to fetch (http/https; a bare domain like example.com is fine)"}
                    },
                    "required": ["url"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read"}
                    },
                    "required": ["path"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write/save a file to disk",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to write to"},
                        "content": {"type": "string", "description": "File content to write"}
                    },
                    "required": ["path", "content"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "create_document",
                "description": "Create a new document in the editor panel. Use this when the user asks to write, create, build, or generate code, scripts, programs, games, apps, or any substantial content (>15 lines) AND there is no already-open document/email draft that the request refers to. If an email compose draft is open, edit that draft instead of creating another document. NEVER put large code blocks directly in chat — use this tool instead.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Document title"},
                        "language": {"type": "string", "description": "Programming language or format (e.g. python, javascript, markdown, text)"},
                        "content": {"type": "string", "description": "The document content"}
                    },
                    "required": ["title", "content"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "edit_document",
                "description": "PREFERRED way to change an existing document. Targeted find-and-replace with multiple FIND/REPLACE pairs per call. Use this for any edit smaller than a full rewrite: adding a function, fixing a bug, tweaking a section, renaming things. Do NOT send the whole file back via update_document for small edits — it wastes tokens and is hard to review.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "edits": {
                            "type": "array",
                            "description": "List of find/replace edits (first match only per edit)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "find": {"type": "string", "description": "Exact text to find in the document"},
                                    "replace": {"type": "string", "description": "Text to replace it with"}
                                },
                                "required": ["find", "replace"]
                            }
                        }
                    },
                    "required": ["edits"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "suggest_document",
                "description": "Suggest improvements to the active document WITHOUT editing it. Creates inline comment bubbles the user can accept or reject. Use when the user asks for suggestions, review, improvements, or feedback.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "suggestions": {
                            "type": "array",
                            "description": "List of suggested changes with reasons",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "find": {"type": "string", "description": "Exact text in the document to suggest changing"},
                                    "replace": {"type": "string", "description": "Suggested replacement text"},
                                    "reason": {"type": "string", "description": "Brief explanation of why this change helps"}
                                },
                                "required": ["find", "replace", "reason"]
                            }
                        }
                    },
                    "required": ["suggestions"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "update_document",
                "description": "Replace the ENTIRE active document. ONLY use for genuine full rewrites (>50% of lines changed). For any smaller change, use edit_document — echoing back the whole file for small edits is wasteful.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Complete new document content"}
                    },
                    "required": ["content"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "search_chats",
                "description": "Search the user's past chat conversations by keyword. Use when the user asks about previous chats, past conversations, or wants to find a discussion they had before. Returns matching sessions with clickable links.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword(s) to find in past conversations"}
                    },
                    "required": ["query"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "chat_with_model",
                "description": "Send a message to another AI model and get its response. Use for getting a second opinion, delegating subtasks, or AI-to-AI communication.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "description": "Model name (e.g. 'qwen3-32b') or model@endpoint_name"},
                        "message": {"type": "string", "description": "The message to send to the model"}
                    },
                    "required": ["model", "message"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "create_session",
                "description": "Create a new chat for ongoing conversations with a specific model. (The UI calls these 'chats'; 'session' is the internal term.)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name for the new chat"},
                        "model": {"type": "string", "description": "Model name or model@endpoint_name"}
                    },
                    "required": ["name", "model"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_sessions",
                "description": "List the user's chats (the UI calls them 'chats') as clickable markdown links. Use this to enumerate chats before opening, renaming, archiving, or deleting them. When replying to the user, preserve the returned [title](#session-id) links; do not strip them into plain text. Optionally filter by keyword.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Optional keyword to filter chats by name"}
                    },
                    "required": []
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "send_to_session",
                "description": "Send a message to an existing chat and get the model's response. The chat keeps its conversation history.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "The id of the chat to send the message to"},
                        "message": {"type": "string", "description": "The message to send"}
                    },
                    "required": ["session_id", "message"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "pipeline",
                "description": "Run a multi-step AI pipeline where each model's output feeds the next. Example: Draft -> Critique -> Revise.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "Pipeline steps in order",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "model": {"type": "string", "description": "Model name for this step"},
                                    "instruction": {"type": "string", "description": "What this step should do"}
                                },
                                "required": ["model", "instruction"]
                            }
                        }
                    },
                    "required": ["steps"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_session",
                "description": "Manage a chat: rename, archive, unarchive, delete, mark important, truncate history, or fork it. (The UI calls these 'chats'; 'session' is the internal term.) For destructive actions like delete, call list_sessions first and pass the exact id returned there; never invent ids.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["rename", "archive", "unarchive", "delete", "important", "unimportant", "truncate", "fork"],
                                   "description": "The action to perform"},
                        "session_id": {"type": "string", "description": "Exact target chat id from list_sessions, or 'current' for the active chat where supported"},
                        "value": {"type": "string", "description": "Action parameter: new name (rename), keep_count (truncate/fork)"}
                    },
                    "required": ["action", "session_id"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_memory",
                "description": "Manage the user's memory system: list, add, edit, delete, or search memories. Memories persist across sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "add", "edit", "delete", "search"],
                                   "description": "The action to perform"},
                        "text": {"type": "string", "description": "Memory text (for add/edit) or search query (for search)"},
                        "memory_id": {"type": "string", "description": "Memory ID (for edit/delete)"},
                        "category": {"type": "string", "enum": ["fact", "event", "contact", "preference"],
                                     "description": "Memory category (for add/list filter)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_models",
                "description": "List all available AI models across configured endpoints. Optionally filter by keyword.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": {"type": "string", "description": "Optional keyword to filter models"}
                    },
                    "required": []
                }
            }
        }),
        // `ui_control` is built via a small helper instead of one giant nested
        // `json!{}`: the `colors.properties` object has 21 sibling entries which
        // pushes `json!`'s macro recursion past the default `recursion_limit`.
        // The helper produces the byte-identical object (key order preserved by
        // `serde_json`'s `preserve_order`), so the schema is unchanged.
        _ui_control_schema(),
        json!({
            "type": "function",
            "function": {
                "name": "manage_tasks",
                "description": "Manage scheduled/automated tasks: list, create, edit, delete, pause, resume, or run tasks. Use this for ANY recurring/scheduled request ('every morning…', 'each day at 7:30', 'daily summarize…') — create a task rather than doing it once. Task types: llm (AI runs a prompt), research (runs the deep-research pipeline on a question), or action (built-in automation). Triggers can be time-based or event-based.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "create", "edit", "delete", "pause", "resume", "run"],
                                   "description": "The action to perform"},
                        "task_id": {"type": "string", "description": "Task ID (for edit/delete/pause/resume/run)"},
                        "name": {"type": "string", "description": "Task name"},
                        "prompt": {"type": "string", "description": "The instruction (for task_type=llm) or the research question (for task_type=research). Required for both."},
                        "task_type": {"type": "string", "enum": ["llm", "research", "action"],
                                      "description": "llm = AI runs your prompt; research = runs the deep-research pipeline on the prompt as a question; action = direct built-in function"},
                        "action_name": {"type": "string", "enum": [
                            "tidy_sessions", "tidy_documents", "consolidate_memory", "tidy_research",
                            "summarize_emails", "draft_email_replies", "extract_email_events",
                            "classify_events", "learn_sender_signatures",
                            "test_skills", "audit_skills", "check_email_urgency"
                        ],
                                        "description": "Built-in action (for task_type=action)"},
                        "trigger_type": {"type": "string", "enum": ["schedule", "event"],
                                         "description": "schedule = time-based, event = count-based"},
                        "schedule": {"type": "string", "enum": ["once", "daily", "weekly", "monthly"],
                                     "description": "Schedule frequency (for trigger_type=schedule)"},
                        "scheduled_time": {"type": "string", "description": "HH:MM in UTC (for schedule triggers). Convert the user's stated local time using the UTC offset given in the 'Current date and time' context."},
                        "scheduled_day": {"type": "integer", "description": "Day of week 0=Mon (weekly) or day of month (monthly)"},
                        "trigger_event": {"type": "string", "enum": ["session_created", "message_sent", "document_created", "memory_added", "research_completed", "email_received", "skill_added"],
                                          "description": "Event name (for trigger_type=event)"},
                        "trigger_count": {"type": "integer", "description": "Fire every N events (for trigger_type=event)"},
                        "output_target": {"type": "string", "description": "Where results go. Defaults to 'session' (results land in a dedicated chat session the user reads) — this is the right choice for 'summarize for me' / 'send to me'. Do NOT go hunting for the user's email address; only use an email MCP tool name here if the user explicitly asked to be emailed AND an address is already known."}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_calendar",
                "description": "Manage calendar events: list events in a date range, create, update, delete. Each event can carry a tag/category (event_type) and importance level. Use ISO 8601 datetimes; for all-day events set all_day=true and pass YYYY-MM-DD. For event reminders/alarms, pass reminder_minutes; the tool creates the Odysseus note reminder, so do not also call manage_notes for the same reminder.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string",
                                   "enum": ["list_events", "create_event", "update_event", "delete_event", "list_calendars"],
                                   "description": "Action to perform"},
                        "summary": {"type": "string", "description": "Event title (for create/update)"},
                        "dtstart": {"type": "string", "description": "Start ISO datetime, or YYYY-MM-DD if all_day"},
                        "dtend": {"type": "string", "description": "End ISO datetime; defaults to +1h (or +1 day for all_day)"},
                        "all_day": {"type": "boolean", "description": "Whether this is an all-day event"},
                        "description": {"type": "string", "description": "Event description / notes"},
                        "location": {"type": "string", "description": "Event location"},
                        "uid": {"type": "string", "description": "Event UID (for update/delete)"},
                        "calendar_href": {"type": "string", "description": "Specific calendar URL (optional; defaults to first calendar)"},
                        "calendar": {"type": "string", "description": "Filter list_events by calendar name or href"},
                        "start": {"type": "string", "description": "list_events range start (ISO datetime); defaults to today"},
                        "end": {"type": "string", "description": "list_events range end (ISO datetime); defaults to +14 days"},
                        "event_type": {"type": "string", "description": "Tag / category for the event. Common values: work, personal, health, travel, meal, social, admin, other. Aliases accepted: tag, category, type."},
                        "importance": {"type": "string", "enum": ["low", "normal", "high", "critical"], "description": "Priority level (defaults to 'normal')"},
                        "reminder_minutes": {"type": "integer", "description": "For create_event: create an Odysseus reminder this many minutes before the event, e.g. 5 for 'reminder 5 min before'."},
                        "rrule": {"type": "string", "description": "Recurrence rule in iCalendar RRULE format, e.g. 'FREQ=WEEKLY;BYDAY=MO' for weekly on Monday. Use with create_event or update_event."}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_notes",
                "description": "Manage notes and checklists (Google Keep-style): list, add, update, delete, toggle_item. IMPORTANT: For to-do lists / checklists, set note_type='checklist' and pass the items as the `checklist_items` array — do NOT serialize them into `content` as plain text. For freeform notes, use note_type='note' and put the body in `content`. `due_date` accepts natural language like 'tomorrow at 9am' (parsed in the user's timezone) and fires a notification — do not also create a calendar event for the same reminder.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string",
                                   "enum": ["list", "add", "update", "delete", "toggle_item"],
                                   "description": "The action to perform"},
                        "id": {"type": "string", "description": "Note id (for update/delete/toggle_item); 8-char prefix is fine"},
                        "title": {"type": "string", "description": "Note title (for add/update)"},
                        "content": {"type": "string", "description": "Freeform body text. Use this for note_type='note'. Do NOT use this for checklists — pass `checklist_items` instead."},
                        "note_type": {"type": "string", "enum": ["note", "checklist"],
                                      "description": "'note' = freeform text in `content`. 'checklist' = structured to-do items in `checklist_items`. Defaults to 'checklist' if checklist_items is supplied, else 'note'."},
                        "checklist_items": {"type": "array",
                                            "items": {"type": "object",
                                                      "properties": {
                                                          "text": {"type": "string", "description": "The to-do item text"},
                                                          "done": {"type": "boolean", "description": "Whether the item is checked off"}
                                                      },
                                                      "required": ["text"]},
                                            "description": "Checklist items for note_type='checklist'. Each item is {text, done}. REQUIRED for checklists — leaving this empty produces a blank note."},
                        "color": {"type": "string", "description": "Optional color label (e.g. 'yellow', 'blue', 'green')"},
                        "label": {"type": "string", "description": "Optional category label (also used as a list filter)"},
                        "pinned": {"type": "boolean", "description": "Pin the note to the top"},
                        "archived": {"type": "boolean", "description": "For update: archive/unarchive. For list: show archived notes when true."},
                        "due_date": {"type": "string", "description": "Reminder time. Accepts natural language ('tomorrow at 9am', '11pm today') or ISO 8601. Fires a notification at that time."},
                        "index": {"type": "integer", "description": "Checklist item index (for toggle_item, 0-based)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "api_call",
                "description": "Call a registered API integration (RSS reader, git forge, bookmark manager, smart home, etc.). Check the system context for available integrations and their endpoints.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "integration": {"type": "string", "description": "Integration name or ID (e.g. 'Miniflux', 'Gitea')"},
                        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method"},
                        "path": {"type": "string", "description": "API endpoint path (e.g. '/v1/entries?status=unread&limit=20')"},
                        "body": {"type": "object", "description": "JSON request body (for POST/PUT/PATCH)"}
                    },
                    "required": ["integration", "method", "path"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "ask_teacher",
                "description": "Ask a more capable AI model for help when stuck on a difficult problem. The teacher provides guidance that can be saved as a learned skill.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "description": "Teacher model name (e.g. 'claude-sonnet-4') or 'auto' for configured default"},
                        "problem": {"type": "string", "description": "Describe the problem or question you need help with"}
                    },
                    "required": ["problem"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_skills",
                "description": "Read or modify the user's skill library. Skills are SKILL.md files (YAML frontmatter + structured body: When to Use / Procedure / Pitfalls / Verification) and follow a draft → published lifecycle. Use progressive disclosure: 'list' to see what exists, 'view' to load full content for a single skill, 'view_ref' for sub-files. Use 'patch' for surgical text edits and 'edit' for full rewrites. 'publish' once you've verified the procedure works. For add, always provide an explicit name slug and only tell the user the exact name returned by the tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "view", "view_ref", "add", "edit", "patch", "publish", "delete", "search"], "description": "list = name+description summary; view = full SKILL.md; view_ref = sub-file under the skill dir; add = create; edit = full rewrite (content); patch = old_string→new_string; publish = flip status; delete; search = relevance match on published skills."},
                        "name": {"type": "string", "description": "Slug/name of the skill. Required for add/view/view_ref/edit/patch/publish/delete. For add, choose the exact kebab-case name the user should see and report only the returned name."},
                        "path": {"type": "string", "description": "Sub-path under the skill directory for view_ref (e.g. 'references/example.md')."},
                        "description": {"type": "string", "description": "One-line summary surfaced in the skills index (for add)."},
                        "category": {"type": "string", "description": "Organizational grouping like 'dev', 'email', 'system' (for add)."},
                        "when_to_use": {"type": "string", "description": "Trigger conditions in plain English (for add)."},
                        "procedure": {"type": "array", "items": {"type": "string"}, "description": "Numbered steps (for add)."},
                        "pitfalls": {"type": "array", "items": {"type": "string"}, "description": "Known failure modes + recovery (for add)."},
                        "verification": {"type": "array", "items": {"type": "string"}, "description": "How to confirm the procedure succeeded (for add)."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Keyword tags (for add)."},
                        "platforms": {"type": "array", "items": {"type": "string"}, "description": "Restrict to OSes (for add)."},
                        "requires_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide unless these toolsets are active (for add)."},
                        "fallback_for_toolsets": {"type": "array", "items": {"type": "string"}, "description": "Hide when these toolsets are active (for add)."},
                        "status": {"type": "string", "enum": ["draft", "published"], "description": "Defaults to 'draft' on add."},
                        "version": {"type": "string", "description": "Semver-ish, e.g. '1.0.0' (for add)."},
                        "confidence": {"type": "number", "description": "0-1 (for add/publish)."},
                        "content": {"type": "string", "description": "Full SKILL.md text (for edit)."},
                        "old_string": {"type": "string", "description": "Exact substring to replace (for patch). Must appear exactly once."},
                        "new_string": {"type": "string", "description": "Replacement text (for patch)."},
                        "query": {"type": "string", "description": "Search query (for search)."}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_endpoints",
                "description": "Manage model API endpoints: list configured endpoints, add new ones, delete, enable or disable them.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable"]},
                        "endpoint_id": {"type": "string", "description": "Endpoint ID (for delete/enable/disable)"},
                        "name": {"type": "string", "description": "Display name (for add)"},
                        "base_url": {"type": "string", "description": "API base URL e.g. https://api.openai.com/v1 (for add)"},
                        "api_key": {"type": "string", "description": "API key (for add)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_mcp",
                "description": "Manage MCP (Model Context Protocol) tool servers: list servers and their tools, add new servers, delete, enable/disable, reconnect, or list all available tools.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable", "reconnect", "list_tools"]},
                        "server_id": {"type": "string", "description": "Server ID (for delete/enable/disable/reconnect)"},
                        "name": {"type": "string", "description": "Server name (for add)"},
                        "command": {"type": "string", "description": "Command to run e.g. npx (for add)"},
                        "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments (for add)"},
                        "env": {"type": "object", "description": "Environment variables (for add)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_webhooks",
                "description": "Manage webhooks: list, add, delete, enable or disable webhook endpoints.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "add", "delete", "enable", "disable"]},
                        "webhook_id": {"type": "string", "description": "Webhook ID (for delete/enable/disable)"},
                        "name": {"type": "string", "description": "Webhook name (for add)"},
                        "url": {"type": "string", "description": "Webhook URL (for add)"},
                        "events": {"type": "string", "description": "Comma-separated event names (for add)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_tokens",
                "description": "Manage API access tokens: list existing tokens, create new ones, or delete them.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "create", "delete"]},
                        "token_id": {"type": "string", "description": "Token ID (for delete)"},
                        "name": {"type": "string", "description": "Token name (for create)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_documents",
                "description": "Manage documents: list all documents (with optional search/language filter), delete documents, or run tidy cleanup.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "delete", "tidy"]},
                        "document_id": {"type": "string", "description": "Document ID (for delete)"},
                        "search": {"type": "string", "description": "Search query (for list)"},
                        "language": {"type": "string", "description": "Filter by language (for list)"},
                        "limit": {"type": "integer", "description": "Max results (for list, default 50)"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_settings",
                "description": "Manage user preferences and settings. Use `disable_tool`/`enable_tool`/`list_tools` to turn individual tools on or off globally (e.g. shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email). Use list/get/set/delete for free-form preferences.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "get", "set", "delete", "disable_tool", "enable_tool", "list_tools"]},
                        "key": {"type": "string", "description": "Setting key (for get/set/delete)"},
                        "value": {"description": "Setting value (for set) — can be string, number, boolean, or object"},
                        "tool": {"type": "string", "description": "Tool name to disable/enable (for disable_tool/enable_tool). Accepts aliases: shell, search, browser, documents, memory, skills, images, tasks, notes, calendar, email — or a raw tool name like 'bash' or 'web_search'."}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "download_model",
                "description": "Download a HuggingFace model to a server. If `host` is omitted, defaults to the cookbook's currently-selected server (NOT localhost) — call list_cookbook_servers first if you're unsure where it should go.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string", "description": "HuggingFace repo (e.g. 'Qwen/Qwen3-8B')"},
                        "host": {"type": "string", "description": "Target server — use the friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or a raw user@host. Omit to use the cookbook's selected default server."},
                        "local": {"type": "boolean", "description": "Force download to THIS machine (localhost) instead of the default remote server."},
                        "include": {"type": "string", "description": "Glob filter for specific files (e.g. '*Q4_K_M*')"}
                    },
                    "required": ["repo_id"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "serve_model",
                "description": "Start serving a model with vLLM, SGLang, llama.cpp, Ollama, or Diffusers. If `host` is omitted, defaults to the cookbook's selected server (not localhost). For image/inpainting/diffusion models use the built-in command `python3 scripts/diffusion_server.py --model <repo> --port 8100` rather than inventing a custom diffusers API server. After launching, call list_served_models to check readiness/errors; if it reports a diagnosis with retry suggestions, retry via serve_model using the suggested adjusted cmd.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_id": {"type": "string", "description": "Model repo (e.g. 'Qwen/Qwen3-8B')"},
                        "cmd": {"type": "string", "description": "Full serve command (e.g. 'vllm serve Qwen/Qwen3-8B --port 8000 --tp 2', 'python3 -m sglang.launch_server --model-path Qwen/Qwen3-8B --port 30000', or for inpainting/image models: 'python3 scripts/diffusion_server.py --model diffusers/stable-diffusion-xl-1.0-inpainting-0.1 --port 8100')"},
                        "host": {"type": "string", "description": "Target server — friendly NAME from list_cookbook_servers (e.g. 'gpu-box', 'workstation') or raw user@host. Omit to use the cookbook's selected default."},
                        "local": {"type": "boolean", "description": "Force serve on THIS machine instead of the default remote server."}
                    },
                    "required": ["repo_id", "cmd"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_served_models",
                "description": "List currently running model servers with status, model name, port, throughput, and structured Cookbook diagnoses. If a serve failed, this includes recent logs plus retry suggestions/adjusted commands the agent can use with serve_model.",
                "parameters": {"type": "object", "properties": {}}
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "stop_served_model",
                "description": "Stop a running model server.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Tmux session ID of the server to stop"}
                    },
                    "required": ["session_id"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_downloads",
                "description": "List in-progress model downloads in the Cookbook. Shows each download's model name, phase, percent (if available), session ID, and remote host.",
                "parameters": {"type": "object", "properties": {}}
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "cancel_download",
                "description": "Cancel an in-progress model download by killing its tmux session. Use list_downloads first to get the session_id.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Tmux session ID from list_downloads (e.g. 'cookbook-a1b2c3d4')"}
                    },
                    "required": ["session_id"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "search_hf_models",
                "description": "Search HuggingFace for models matching a query. Returns a ranked list of repo IDs, sizes (when available), and download counts. Use this when the user wants to find a model to download.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search terms (e.g. 'Qwen 8B', 'flux', 'llama-3 instruct')"},
                        "limit": {"type": "integer", "description": "Max results (default 10)"}
                    },
                    "required": []
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_cookbook_servers",
                "description": "List the cookbook's configured servers (remote GPU boxes + local) and the current default host. Call this before download_model/serve_model when the user didn't specify a host, so models go to the right machine (where the GPUs and model cache are) instead of localhost. If multiple servers and intent is ambiguous, show them and ask the user which.",
                "parameters": {"type": "object", "properties": {}}
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_serve_presets",
                "description": "List saved Cookbook serve presets. Each preset is a launch template (name, model, host, port, tmux cmd) the user previously saved from the UI. Call this BEFORE serve_model when the user asks to launch a model by name — there's almost always a working preset for it.",
                "parameters": {"type": "object", "properties": {}}
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "adopt_served_model",
                "description": "Register an existing tmux model server (started manually or outside the cookbook flow) into Cookbook tracking, AND add it as a chat endpoint. Use when the user (or you) launched something via ssh+tmux and now want it visible in the UI / stoppable via stop_served_model / usable in the model picker. Verifies the tmux session + port respond before adding.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Remote host in user@host form (e.g. 'user@192.0.2.10'). Omit for localhost."},
                        "tmux_session": {"type": "string", "description": "Existing tmux session name (e.g. 'minimax-m27')"},
                        "model": {"type": "string", "description": "Model repo_id or display name (e.g. 'cyankiwi/MiniMax-M2.7-AWQ-4bit')"},
                        "port": {"type": "integer", "description": "Port the server is listening on (default 8000)"},
                        "name": {"type": "string", "description": "Optional display name (defaults to model basename)"},
                        "add_endpoint": {"type": "boolean", "description": "Also register as a chat endpoint (default true)"}
                    },
                    "required": ["tmux_session", "model"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "serve_preset",
                "description": "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user saved before. This is the preferred way to start a known model (SD3.5, vLLM presets, etc.) — don't fabricate launch commands when a preset exists.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Preset name (exact or case-insensitive substring of one returned by list_serve_presets)"}
                    },
                    "required": ["name"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_cached_models",
                "description": "List models already cached on disk locally or on a remote server. `host` accepts friendly Cookbook server names from list_cookbook_servers (for example ajax) or raw user@host. Also reports completed Cookbook download tasks when the filesystem cache scan cannot locate the HF cache path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Friendly Cookbook server name (e.g. 'ajax', 'gpu-box') or raw remote host (e.g. 'user@gpu-box'). Omit for local."},
                        "model_dir": {"type": "string", "description": "Comma-separated additional model directories to scan beyond ~/.cache/huggingface/hub"},
                        "ssh_port": {"type": "string", "description": "SSH port for remote host (default 22)"},
                        "platform": {"type": "string", "enum": ["linux", "windows"], "description": "Remote platform"}
                    },
                    "required": []
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "app_api",
                "description": "Generic loopback to ANY internal Odysseus endpoint. Use this when there's no named tool for what the user wants. Hits the same routes the UI buttons hit (cookbook, gallery, library/documents, memory, notes, calendar, tasks, settings, themes, research, compare, etc.). action='endpoints' returns the OpenAPI surface (use `filter` to narrow). action='call' (default) takes method+path+body. Auth/user/admin paths are blocked for safety. Do not use for email account discovery; use list_email_accounts instead because /api/email/accounts is owner-filtered in tool context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["call", "endpoints"], "description": "'call' to hit an endpoint, 'endpoints' to list what's available"},
                        "path": {"type": "string", "description": "Endpoint path starting with /api/ (e.g. '/api/cookbook/gpus', '/api/gallery/list', '/api/calendar/events')"},
                        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "description": "HTTP method (default GET)"},
                        "body": {"type": "object", "description": "JSON request body for POST/PUT/PATCH"},
                        "query": {"type": "object", "description": "Querystring params as a key-value object"},
                        "filter": {"type": "string", "description": "For action=endpoints: substring to filter paths/summaries (e.g. 'cookbook', 'gallery')"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "edit_image",
                "description": "Edit a gallery image: upscale, remove background, inpaint, or harmonize.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "image_id": {"type": "string", "description": "Gallery image ID"},
                        "action": {"type": "string", "enum": ["upscale", "rembg", "inpaint", "harmonize"], "description": "Edit action"},
                        "prompt": {"type": "string", "description": "For inpaint: what to fill the masked area with"},
                        "scale": {"type": "number", "description": "For upscale: scale factor (default 2)"}
                    },
                    "required": ["image_id", "action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "trigger_research",
                "description": "Start a deep research task on a topic. Returns a task ID for tracking.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Research question or topic"}
                    },
                    "required": ["topic"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "resolve_contact",
                "description": "Look up a contact's email address by name. Searches CardDAV address book and sent email history. Use when the user says 'message [name]' or 'email [name]' without an email address.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Person's name to search for"}
                    },
                    "required": ["name"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "manage_contact",
                "description": "Create, update, delete, or list the user's CardDAV contacts. Use to save a new contact ('save Jonathan's email jon@x.com'), update an existing one ('change Maria's number'), or remove one. For update/delete you need the contact's uid — call action='list' first to find it. Writes go through the same dedupe + validation as the Contacts UI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "add", "update", "delete"],
                                   "description": "list = show all contacts (with uids); add = create; update = edit by uid; delete = remove by uid."},
                        "uid": {"type": "string", "description": "Contact UID (required for update/delete; get it from action=list)."},
                        "name": {"type": "string", "description": "Contact's display name (for add/update)."},
                        "email": {"type": "string", "description": "Single email address (convenience for add, or the primary email for update)."},
                        "emails": {"type": "array", "items": {"type": "string"}, "description": "Full list of email addresses (for update; first is primary)."},
                        "phones": {"type": "array", "items": {"type": "string"}, "description": "Full list of phone numbers (for update)."}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_email_accounts",
                "description": "List configured email accounts. Use this before checking mail when the user names a mailbox/account such as Gmail, work, or a custom domain, then pass the returned account name/email/id to the other email tools.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "Send a new email. Use resolve_contact first if you only have a name and need to find the email address. If multiple accounts exist, pass account from list_email_accounts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject line"},
                        "body": {"type": "string", "description": "Email body text"},
                        "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"}
                    },
                    "required": ["to", "subject", "body"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "list_emails",
                "description": "List emails from an account/folder, newest first. Returns subject, sender, date, UID, and account for each email. Use list_email_accounts first when the user mentions Gmail/work/a custom mailbox. For last/latest/newest email requests, use max_results=1 and unread_only=false.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "max_results": {"type": "integer", "description": "Max emails to return (default: 20)"},
                        "limit": {"type": "integer", "description": "Backward-compatible alias for max_results"},
                        "unread_only": {"type": "boolean", "description": "Only show unread emails. Default false; set true only when the user asks for unread emails."},
                        "unresponded_only": {"type": "boolean", "description": "Only show unanswered emails. Default false."},
                        "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"}
                    }
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "read_email",
                "description": "Read the full content of a specific email by UID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Email UID to read"},
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"}
                    },
                    "required": ["uid"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "reply_to_email",
                "description": "SEND a reply email immediately by UID. Do not use this when the user asks to open/start a reply window or draft; use ui_control action=open_email_reply instead. For follow-up 'reply ...' requests where the user clearly wants to send now, use the exact UID from the latest read_email/list_emails result; never invent UID 1. Automatically threads with In-Reply-To/References headers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Exact UID of the email to reply to from list_emails/read_email; never invent UID 1"},
                        "body": {"type": "string", "description": "Reply body text"},
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"}
                    },
                    "required": ["uid", "body"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "bulk_email",
                "description": "Perform one action on many emails at once. Use this for 'delete all those', 'archive these', 'mark all read', or any bulk operation after list_emails. Always pass account when the listed emails came from a named account such as Gmail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["mark_read", "mark_unread", "archive", "delete", "junk"], "description": "Bulk action to perform"},
                        "uids": {"type": "array", "items": {"type": "string"}, "description": "UIDs from the latest list_emails result"},
                        "all_unread": {"type": "boolean", "description": "Operate on all unread messages in folder instead of explicit UIDs"},
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "permanent": {"type": "boolean", "description": "For delete: hard-delete instead of moving to Trash"},
                        "account": {"type": "string", "description": "Account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"}
                    },
                    "required": ["action"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "delete_email",
                "description": "Delete one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "permanent": {"type": "boolean", "description": "Hard-delete instead of moving to Trash"},
                        "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"}
                    },
                    "required": ["uid"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "archive_email",
                "description": "Archive one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"}
                    },
                    "required": ["uid"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "mark_email_read",
                "description": "Mark one email as read or unread by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                        "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                        "read": {"type": "boolean", "description": "True marks read; false marks unread"},
                        "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"}
                    },
                    "required": ["uid"]
                }
            }
        }),
    ]
});

/// Build the `ui_control` tool schema (split out of `FUNCTION_TOOL_SCHEMAS` to
/// stay under `json!`'s macro recursion limit; the `colors.properties` block has
/// 21 sibling entries). Produces the byte-identical object the Python literal
/// does, with key order preserved by `serde_json`'s `preserve_order`.
fn _ui_control_schema() -> Value {
    // colors.properties — a flat list of {type:string, description:...} entries
    // in the SAME order as the Python literal.
    let color_props: [(&str, &str); 21] = [
        ("bg", "Background color (hex, e.g. #1a1a2e)"),
        ("fg", "Foreground/text color (hex)"),
        ("panel", "Panel/sidebar background color (hex)"),
        ("border", "Border/divider color (hex)"),
        ("accent", "Accent color for buttons, brand, highlights (hex)"),
        ("userBubbleBg", "User chat bubble background (hex, optional)"),
        ("aiBubbleBg", "AI chat bubble background (hex, optional)"),
        ("bubbleBorder", "Chat bubble border color (hex, optional)"),
        ("sidebarBg", "Sidebar background override (hex, optional)"),
        ("sectionAccent", "Section header accent color (hex, optional)"),
        ("brandColor", "Brand/logo color (hex, optional)"),
        ("inputBg", "Chat input background (hex, optional)"),
        ("inputBorder", "Chat input border (hex, optional)"),
        ("sendBtnBg", "Send button background (hex, optional)"),
        ("sendBtnHover", "Send button hover color (hex, optional)"),
        ("codeBg", "Code block background (hex, optional)"),
        ("codeFg", "Code block text color (hex, optional)"),
        ("toggleBg", "Toggle switch off background (hex, optional)"),
        ("toggleActive", "Toggle switch on color (hex, optional)"),
        ("accentPrimary", "Primary accent override (hex, optional)"),
        ("accentError", "Error/danger color (hex, optional)"),
    ];
    let mut props_map = Map::new();
    for (k, desc) in color_props {
        props_map.insert(
            k.to_string(),
            json!({"type": "string", "description": desc}),
        );
    }

    let colors = json!({
        "type": "object",
        "description": "For create_theme: the theme colors",
        "properties": Value::Object(props_map),
        "required": ["bg", "fg", "panel", "border", "accent"]
    });

    let mut properties = Map::new();
    properties.insert(
        "action".to_string(),
        json!({"type": "string", "enum": ["toggle", "open_panel", "open_email_reply", "set_mode", "switch_model", "set_theme", "create_theme", "get_toggles"],
               "description": "The UI action. Use set_theme for presets, create_theme to build a custom theme with any hex colors"}),
    );
    properties.insert(
        "name".to_string(),
        json!({"type": "string", "description": "For toggle: web, bash, research, incognito, document_editor (aliases: shell, search, deepresearch, documents). For open_panel: documents, gallery, email, sessions, notes, brain/memories, skills, settings, cookbook. For open_email_reply: email UID. For set_theme: a preset theme name. For create_theme: the custom theme name."}),
    );
    properties.insert(
        "value".to_string(),
        json!({"type": "string", "description": "Value: on/off for toggle, agent/chat for set_mode, model name for switch_model, theme name for set_theme, or folder for open_email_reply"}),
    );
    properties.insert(
        "uid".to_string(),
        json!({"type": "string", "description": "Email UID for open_email_reply"}),
    );
    properties.insert(
        "folder".to_string(),
        json!({"type": "string", "description": "Email folder for open_email_reply (default INBOX)"}),
    );
    properties.insert(
        "mode".to_string(),
        json!({"type": "string", "description": "Reply draft mode for open_email_reply: reply, reply-all, or ai-reply"}),
    );
    properties.insert("colors".to_string(), colors);

    json!({
        "type": "function",
        "function": {
            "name": "ui_control",
            "description": "Control the user interface. Actions: toggle (turn tools on/off), open_panel (open a modal: documents/library, gallery, email, sessions, notes, memories/brain, skills, settings, cookbook), open_email_reply (open an email reply draft document; does NOT send), set_mode, switch_model, set_theme (presets: dark, light, midnight, paper, nord, monokai, gruvbox, dracula, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, vaporwave, lavender, gpt, coffee, claude), create_theme (CREATE any custom theme with a name + colors object — pick distinctive, evocative hex colors that match the requested aesthetic, NOT generic defaults. The theme auto-applies after creation). When a user asks for ANY theme not in the preset list, ALWAYS use create_theme.",
            "parameters": {
                "type": "object",
                "properties": Value::Object(properties),
                "required": ["action"]
            }
        }
    })
}

// ---------------------------------------------------------------------------
// Converter: native function call -> ToolBlock
// ---------------------------------------------------------------------------

/// `args.get(key, "")` where the value is treated as a string.
///
/// Python's `args.get(k, "")` returns the value if the key is present (else the
/// "" default). All call sites here use the result in string concatenation /
/// `"\n".join`, so we coerce a present-but-non-string JSON value the way the
/// Python would observe it: a JSON string yields its inner text; absent yields
/// "". (A present non-string value would raise a `TypeError` in the Python `+`;
/// none of the schemas declare such a field, so this never fires in practice —
/// we fall back to "" rather than fabricate.)
fn arg_str(args: &Map<String, Value>, key: &str) -> String {
    match args.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

/// `args.get(key, default)` returning the JSON string value or `default`.
///
/// DEVIATION (deliberate, robustness): Python `dict.get(key, default)` returns
/// `default` ONLY when the key is ABSENT — a key present as JSON `null` returns
/// `None`, and the subsequent string op raises an uncaught `TypeError`, failing
/// the whole tool-call conversion. Here a present-but-non-string value (incl.
/// `null`) falls back to `default`, so a model emitting `"session_id": null` for
/// an optional field degrades gracefully instead of crashing the turn. (Reach
/// for this only where the Python default is a benign string; never to fabricate
/// a value the model didn't supply.)
fn arg_str_or(args: &Map<String, Value>, key: &str, default: &str) -> String {
    match args.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => default.to_string(),
    }
}

/// Python truthiness of `args.get(key)`: present, non-null, and not an empty
/// string / empty container / zero. For these schemas the relevant fields are
/// strings, so "truthy" means a present non-empty string.
fn arg_truthy(args: &Map<String, Value>, key: &str) -> bool {
    match args.get(key) {
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Null) | None => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Convert a native function call into a ToolBlock for the existing execution
/// pipeline.
///
/// Drift vs Python: the Python accepts `arguments` as either a JSON string OR an
/// already-parsed object (the `isinstance(arguments, str)` branch). Per the
/// design contract, native calls always carry `arguments` as a JSON string in
/// the Rust port, so this takes `&str` and always parses. Empty/blank yields an
/// empty arg map. A JSON parse error logs (does NOT raise) and returns `None`,
/// matching the Python `except (json.JSONDecodeError, TypeError)` arm.
pub fn function_call_to_tool_block(name: &str, arguments: &str) -> Option<ToolBlock> {
    // try: ... except (json.JSONDecodeError, TypeError): logger.error(...); return None
    let args: Map<String, Value> = if arguments.is_empty() || arguments.trim().is_empty() {
        Map::new()
    } else {
        match serde_json::from_str::<Value>(arguments) {
            Ok(Value::Object(m)) => m,
            // A JSON scalar/array parses fine but isn't a dict; Python's
            // `args.get(...)` would then raise AttributeError -> not one of the
            // caught exceptions. In practice models always send an object; we
            // treat a non-object parse as an empty arg map so `.get` semantics
            // hold without fabricating values.
            Ok(_) => Map::new(),
            Err(_) => {
                logger::error(&format!(
                    "Failed to parse function call arguments for {name}: {arguments}"
                ));
                return None;
            }
        }
    };

    // tool_type = _TOOL_NAME_MAP.get(name, name)
    let tool_type: String = match _TOOL_NAME_MAP.get(name) {
        Some(t) => (*t).to_string(),
        None => name.to_string(),
    };

    // Allow MCP tools through (namespaced as mcp__serverid__toolname)
    if tool_type.starts_with("mcp__") {
        let content = if !args.is_empty() {
            py_json_dumps(&Value::Object(args.clone()))
        } else {
            "{}".to_string()
        };
        return Some(ToolBlock::new(&tool_type, &content));
    }

    // Email tools are implemented as MCP — route them to email. Keyed on the
    // ORIGINAL `name` (not the mapped tool_type).
    static _BUILTIN_EMAIL_TOOLS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
        HashSet::from([
            "list_email_accounts",
            "send_email",
            "list_emails",
            "read_email",
            "reply_to_email",
            "archive_email",
            "delete_email",
            "mark_email_read",
            "bulk_email",
            "download_attachment",
        ])
    });
    if _BUILTIN_EMAIL_TOOLS.contains(name) {
        let content = if !args.is_empty() {
            py_json_dumps(&Value::Object(args.clone()))
        } else {
            "{}".to_string()
        };
        return Some(ToolBlock::new(&format!("mcp__email__{name}"), &content));
    }

    if !TOOL_TAGS.contains(tool_type.as_str()) {
        logger::warning(&format!("Unknown function call: {name}"));
        return None;
    }

    // `json.dumps(args)` of the whole arg map (used by several fall-through
    // tools). Reused below.
    let dumps_args = || py_json_dumps(&Value::Object(args.clone()));

    // Convert structured args back to the text format each tool expects
    let content: String = match tool_type.as_str() {
        "bash" => arg_str(&args, "command"),
        "python" => arg_str(&args, "code"),
        "web_search" => {
            // queries = args.get("queries")
            // if isinstance(queries, list) and queries: content = str(queries[0])
            // elif queries:                              content = str(queries)
            // else:                                      content = args.get("query", "")
            match args.get("queries") {
                Some(Value::Array(a)) if !a.is_empty() => py_str(&a[0]),
                Some(v) if py_value_truthy(v) => py_str(v),
                _ => arg_str(&args, "query"),
            }
        }
        "read_file" => arg_str(&args, "path"),
        "write_file" => format!("{}\n{}", arg_str(&args, "path"), arg_str(&args, "content")),
        "create_document" => {
            // parts = [args.get("title", "Untitled")]
            let mut parts: Vec<String> = vec![arg_str_or(&args, "title", "Untitled")];
            // if args.get("language"): parts.append(args["language"])
            if arg_truthy(&args, "language") {
                parts.push(arg_str(&args, "language"));
            }
            // parts.append(args.get("content", ""))
            parts.push(arg_str(&args, "content"));
            parts.join("\n")
        }
        "edit_document" => {
            // for edit in args.get("edits", []): ...
            let mut blocks: Vec<String> = Vec::new();
            if let Some(Value::Array(edits)) = args.get("edits") {
                for edit in edits {
                    let find = edit_get_str(edit, "find");
                    let replace = edit_get_str(edit, "replace");
                    blocks.push(format!(
                        "<<<FIND>>>\n{find}\n<<<REPLACE>>>\n{replace}\n<<<END>>>"
                    ));
                }
            }
            blocks.join("\n")
        }
        "suggest_document" => {
            let mut blocks: Vec<String> = Vec::new();
            if let Some(Value::Array(sugs)) = args.get("suggestions") {
                for s in sugs {
                    let find = edit_get_str(s, "find");
                    let replace = edit_get_str(s, "replace");
                    let reason = edit_get_str(s, "reason");
                    blocks.push(format!(
                        "<<<FIND>>>\n{find}\n<<<SUGGEST>>>\n{replace}\n<<<REASON>>>\n{reason}\n<<<END>>>"
                    ));
                }
            }
            blocks.join("\n")
        }
        "update_document" => arg_str(&args, "content"),
        "search_chats" => arg_str(&args, "query"),
        "chat_with_model" => format!("{}\n{}", arg_str(&args, "model"), arg_str(&args, "message")),
        "create_session" => format!(
            "{}\n{}",
            arg_str_or(&args, "name", "Untitled"),
            arg_str(&args, "model")
        ),
        "list_sessions" => arg_str(&args, "filter"),
        "send_to_session" => format!(
            "{}\n{}",
            arg_str(&args, "session_id"),
            arg_str(&args, "message")
        ),
        "pipeline" => {
            // json.dumps({"steps": args.get("steps", [])})
            let steps = args.get("steps").cloned().unwrap_or(Value::Array(vec![]));
            py_json_dumps(&json!({ "steps": steps }))
        }
        "manage_session" => {
            let action = arg_str(&args, "action");
            let value = arg_str(&args, "value");
            if action == "list" {
                // keyword = args.get("session_id","") or args.get("keyword","") or value
                let mut keyword = arg_str(&args, "session_id");
                if keyword.is_empty() {
                    keyword = arg_str(&args, "keyword");
                }
                if keyword.is_empty() {
                    keyword = value.clone();
                }
                if !keyword.is_empty() && keyword.to_lowercase() != "current" {
                    format!("list\n{keyword}")
                } else {
                    "list".to_string()
                }
            } else {
                let sid = arg_str_or(&args, "session_id", "current");
                let mut c = format!("{action}\n{sid}");
                if !value.is_empty() {
                    c.push('\n');
                    c.push_str(&value);
                }
                c
            }
        }
        "manage_memory" => {
            let action = arg_str(&args, "action");
            match action.as_str() {
                "add" => {
                    let mut c = format!("add\n{}", arg_str(&args, "text"));
                    if arg_truthy(&args, "category") {
                        c.push('\n');
                        c.push_str(&arg_str(&args, "category"));
                    }
                    c
                }
                "edit" => format!(
                    "edit\n{}\n{}",
                    arg_str(&args, "memory_id"),
                    arg_str(&args, "text")
                ),
                "delete" => format!("delete\n{}", arg_str(&args, "memory_id")),
                "search" => format!("search\n{}", arg_str(&args, "text")),
                "list" => {
                    let mut c = "list".to_string();
                    if arg_truthy(&args, "category") {
                        c.push('\n');
                        c.push_str(&arg_str(&args, "category"));
                    }
                    c
                }
                _ => action,
            }
        }
        "list_models" => arg_str(&args, "filter"),
        "ui_control" => {
            let action = arg_str(&args, "action");
            // Python rebinds `name` here (shadowing the function arg). We use a
            // local `ui_name` since the original `name` param is no longer
            // needed past the email check above.
            let ui_name = arg_str(&args, "name");
            let value = arg_str(&args, "value");
            match action.as_str() {
                "toggle" => format!("toggle {ui_name} {value}"),
                "open_panel" => {
                    // f"open_panel {name or value}"
                    let n = if !ui_name.is_empty() { &ui_name } else { &value };
                    format!("open_panel {n}")
                }
                "open_email_reply" => {
                    // uid = args.get("uid") or name
                    let mut uid = arg_str(&args, "uid");
                    if uid.is_empty() {
                        uid = ui_name.clone();
                    }
                    // folder = args.get("folder") or value or "INBOX"
                    let mut folder = arg_str(&args, "folder");
                    if folder.is_empty() {
                        folder = value.clone();
                    }
                    if folder.is_empty() {
                        folder = "INBOX".to_string();
                    }
                    // mode = args.get("mode") or "reply"
                    let mut mode = arg_str(&args, "mode");
                    if mode.is_empty() {
                        mode = "reply".to_string();
                    }
                    format!("open_email_reply {uid} {folder} {mode}")
                }
                "set_mode" => {
                    let v = if !value.is_empty() { &value } else { &ui_name };
                    format!("set_mode {v}")
                }
                "switch_model" => {
                    let v = if !value.is_empty() { &value } else { &ui_name };
                    format!("switch_model {v}")
                }
                "set_theme" => {
                    let v = if !value.is_empty() { &value } else { &ui_name };
                    format!("set_theme {v}")
                }
                "create_theme" => {
                    // colors = args.get("colors", {})
                    let empty = Map::new();
                    let colors: &Map<String, Value> = match args.get("colors") {
                        Some(Value::Object(m)) => m,
                        _ => &empty,
                    };
                    // theme_name = name or value or "custom"
                    let theme_name = if !ui_name.is_empty() {
                        ui_name.clone()
                    } else if !value.is_empty() {
                        value.clone()
                    } else {
                        "custom".to_string()
                    };
                    let bg = color_or(colors, "bg", "#282c34");
                    let fg = color_or(colors, "fg", "#9cdef2");
                    let panel = color_or(colors, "panel", "#111111");
                    let border = color_or(colors, "border", "#355a66");
                    let accent = color_or(colors, "accent", "#e06c75");
                    let mut c = format!("create_theme {theme_name} {bg} {fg} {panel} {border} {accent}");
                    // Append advanced overrides as key=value
                    let adv_keys = [
                        "userBubbleBg",
                        "aiBubbleBg",
                        "bubbleBorder",
                        "sidebarBg",
                        "sectionAccent",
                        "brandColor",
                        "inputBg",
                        "inputBorder",
                        "sendBtnBg",
                        "sendBtnHover",
                        "codeBg",
                        "codeFg",
                        "toggleBg",
                        "toggleActive",
                        "accentPrimary",
                        "accentError",
                    ];
                    for ak in adv_keys {
                        // if colors.get(ak): content += f" {ak}={colors[ak]}"
                        if color_truthy(colors, ak) {
                            c.push_str(&format!(" {ak}={}", color_get(colors, ak)));
                        }
                    }
                    c
                }
                _ => action,
            }
        }
        "manage_tasks" | "manage_skills" | "api_call" | "manage_endpoints" | "manage_mcp"
        | "manage_webhooks" | "manage_tokens" | "manage_documents" | "manage_settings" => {
            dumps_args()
        }
        "ask_teacher" => format!(
            "{}\n{}",
            arg_str_or(&args, "model", "auto"),
            arg_str(&args, "problem")
        ),
        _ => dumps_args(),
    };

    Some(ToolBlock::new(&tool_type, &content))
}

/// `edit.get("find", "")` over a JSON value that should be an object.
fn edit_get_str(edit: &Value, key: &str) -> String {
    match edit.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

/// `colors.get(name, default)` returning the hex string or `default`.
fn color_or(colors: &Map<String, Value>, key: &str, default: &str) -> String {
    match colors.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => default.to_string(),
    }
}

/// Python truthiness of `colors.get(ak)`.
fn color_truthy(colors: &Map<String, Value>, key: &str) -> bool {
    match colors.get(key) {
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Null) | None => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `colors[ak]` rendered into the `f" {ak}={colors[ak]}"` string. For a JSON
/// string this is the inner text; for other JSON types it is the Python `str()`
/// of the value (the f-string would stringify it).
fn color_get(colors: &Map<String, Value>, key: &str) -> String {
    match colors.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(v) => v.to_string(),
        None => String::new(),
    }
}

/// Python `str(value)` over a JSON value. For a JSON string this is the inner
/// text (no surrounding quotes); for any other JSON type it is the value's
/// JSON serialization (matches `color_get`'s deviation — close enough for the
/// scalar / non-string `web_search` `queries` values models actually send).
fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        v => v.to_string(),
    }
}

/// Python truthiness of a JSON value: non-null, and not an empty string /
/// empty container / zero. Used for `elif queries:` on `args.get("queries")`.
fn py_value_truthy(value: &Value) -> bool {
    match value {
        Value::String(s) => !s.is_empty(),
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    #[test]
    fn schema_table_count_and_endpoints() {
        // Python: len(FUNCTION_TOOL_SCHEMAS) == 58; first 'bash', last 'mark_email_read'.
        assert_eq!(FUNCTION_TOOL_SCHEMAS.len(), 58);
        assert_eq!(
            FUNCTION_TOOL_SCHEMAS[0]["function"]["name"].as_str().unwrap(),
            "bash"
        );
        assert_eq!(
            FUNCTION_TOOL_SCHEMAS[57]["function"]["name"].as_str().unwrap(),
            "mark_email_read"
        );
    }

    #[test]
    fn schema_table_byte_identical_to_python() {
        // `json.dumps(FUNCTION_TOOL_SCHEMAS)` in CPython (default separators,
        // ensure_ascii) hashes to this. `py_json_dumps` over the Rust Vec<Value>
        // must produce the same bytes — proving the full 58-entry table, key
        // order, and the hand-rebuilt `ui_control` schema all match.
        const PY_SHA256: &str =
            "6740c33104266ec0ab8fba0da908a6a2660a1474421515d2bb653f1f971076c1";
        let dumped = py_json_dumps(&*FUNCTION_TOOL_SCHEMAS);
        let mut hasher = Sha256::new();
        hasher.update(dumped.as_bytes());
        let got = hex::encode(hasher.finalize());
        assert_eq!(got, PY_SHA256, "FUNCTION_TOOL_SCHEMAS diverged from Python");
    }

    #[test]
    fn web_fetch_and_manage_notes_schemas_present() {
        // Both new schemas were ported and appear by name in the table.
        let names: Vec<&str> = FUNCTION_TOOL_SCHEMAS
            .iter()
            .map(|s| s["function"]["name"].as_str().unwrap())
            .collect();
        assert!(names.contains(&"web_fetch"));
        assert!(names.contains(&"manage_notes"));
        // web_fetch sits immediately after web_search (Python list order).
        let ws = names.iter().position(|n| *n == "web_search").unwrap();
        assert_eq!(names[ws + 1], "web_fetch");
    }

    #[test]
    fn manage_calendar_has_rrule_param() {
        // rrule was added to the calendar schema's properties.
        let cal = FUNCTION_TOOL_SCHEMAS
            .iter()
            .find(|s| s["function"]["name"] == "manage_calendar")
            .expect("manage_calendar schema present");
        let props = &cal["function"]["parameters"]["properties"];
        assert!(props.get("rrule").is_some(), "rrule param missing");
        assert_eq!(props["rrule"]["type"].as_str().unwrap(), "string");
    }

    #[test]
    fn web_search_prefers_queries_array() {
        // queries (non-empty list): content = str(queries[0])
        let tb = function_call_to_tool_block(
            "web_search",
            r#"{"queries": ["alpha", "beta"], "query": "ignored"}"#,
        )
        .expect("tool block");
        assert_eq!(tb.tool_type, "web_search");
        assert_eq!(tb.content, "alpha");
    }

    #[test]
    fn web_search_truthy_non_list_queries() {
        // queries present + truthy but not a list: content = str(queries)
        let tb = function_call_to_tool_block(
            "web_search",
            r#"{"queries": "solo", "query": "ignored"}"#,
        )
        .expect("tool block");
        assert_eq!(tb.content, "solo");
    }

    #[test]
    fn web_search_falls_back_to_query() {
        // queries absent -> content = args.get("query", "")
        let tb = function_call_to_tool_block("web_search", r#"{"query": "fallback"}"#)
            .expect("tool block");
        assert_eq!(tb.content, "fallback");
        // queries present but empty list (falsy) -> also falls back to query
        let tb2 = function_call_to_tool_block(
            "web_search",
            r#"{"queries": [], "query": "fallback2"}"#,
        )
        .expect("tool block");
        assert_eq!(tb2.content, "fallback2");
    }
}
