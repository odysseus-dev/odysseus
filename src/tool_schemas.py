"""
tool_schemas.py

OpenAI-compatible function tool schemas and the converter that turns
native function calls back into ToolBlocks for the execution pipeline.

Extracted from agent_tools.py to keep schema definitions separate from
tool parsing / execution logic.
"""

import json
import logging
from typing import Optional

from src.agent_tools import ToolBlock, TOOL_TAGS
from src.tool_parsing import _TOOL_NAME_MAP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI-compatible function tool schemas
# ---------------------------------------------------------------------------
FUNCTION_TOOL_SCHEMAS = [
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": "Create a new document in the editor panel. ALWAYS use this when the user asks to write, create, build, or generate code, scripts, programs, games, apps, or any substantial content (>15 lines). NEVER put large code blocks directly in chat — use this tool instead.",
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "ui_control",
            "description": "Control the user interface. Actions: toggle (turn tools on/off), open_panel (open a modal: documents/library, gallery, email, sessions, notes, memories/brain, skills, settings, cookbook), open_email_reply (open an email reply draft document; does NOT send), set_mode, switch_model, set_theme (presets: dark, light, midnight, paper, nord, monokai, gruvbox, dracula, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, vaporwave, lavender, gpt, coffee, claude), create_theme (CREATE any custom theme with a name + colors object — pick distinctive, evocative hex colors that match the requested aesthetic, NOT generic defaults. The theme auto-applies after creation). When a user asks for ANY theme not in the preset list, ALWAYS use create_theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["toggle", "open_panel", "open_email_reply", "set_mode", "switch_model", "set_theme", "create_theme", "get_toggles"],
                               "description": "The UI action. Use set_theme for presets, create_theme to build a custom theme with any hex colors"},
                    "name": {"type": "string", "description": "For toggle: web, bash, research, incognito, document_editor (aliases: shell, search, deepresearch, documents). For open_panel: documents, gallery, email, sessions, notes, brain/memories, skills, settings, cookbook. For open_email_reply: email UID. For set_theme: a preset theme name. For create_theme: the custom theme name."},
                    "value": {"type": "string", "description": "Value: on/off for toggle, agent/chat for set_mode, model name for switch_model, theme name for set_theme, or folder for open_email_reply"},
                    "uid": {"type": "string", "description": "Email UID for open_email_reply"},
                    "folder": {"type": "string", "description": "Email folder for open_email_reply (default INBOX)"},
                    "mode": {"type": "string", "description": "Reply draft mode for open_email_reply: reply, reply-all, or ai-reply"},
                    "colors": {"type": "object", "description": "For create_theme: the theme colors",
                               "properties": {
                                   "bg": {"type": "string", "description": "Background color (hex, e.g. #1a1a2e)"},
                                   "fg": {"type": "string", "description": "Foreground/text color (hex)"},
                                   "panel": {"type": "string", "description": "Panel/sidebar background color (hex)"},
                                   "border": {"type": "string", "description": "Border/divider color (hex)"},
                                   "accent": {"type": "string", "description": "Accent color for buttons, brand, highlights (hex)"},
                                   "userBubbleBg": {"type": "string", "description": "User chat bubble background (hex, optional)"},
                                   "aiBubbleBg": {"type": "string", "description": "AI chat bubble background (hex, optional)"},
                                   "bubbleBorder": {"type": "string", "description": "Chat bubble border color (hex, optional)"},
                                   "sidebarBg": {"type": "string", "description": "Sidebar background override (hex, optional)"},
                                   "sectionAccent": {"type": "string", "description": "Section header accent color (hex, optional)"},
                                   "brandColor": {"type": "string", "description": "Brand/logo color (hex, optional)"},
                                   "inputBg": {"type": "string", "description": "Chat input background (hex, optional)"},
                                   "inputBorder": {"type": "string", "description": "Chat input border (hex, optional)"},
                                   "sendBtnBg": {"type": "string", "description": "Send button background (hex, optional)"},
                                   "sendBtnHover": {"type": "string", "description": "Send button hover color (hex, optional)"},
                                   "codeBg": {"type": "string", "description": "Code block background (hex, optional)"},
                                   "codeFg": {"type": "string", "description": "Code block text color (hex, optional)"},
                                   "toggleBg": {"type": "string", "description": "Toggle switch off background (hex, optional)"},
                                   "toggleActive": {"type": "string", "description": "Toggle switch on color (hex, optional)"},
                                   "accentPrimary": {"type": "string", "description": "Primary accent override (hex, optional)"},
                                   "accentError": {"type": "string", "description": "Error/danger color (hex, optional)"}
                               },
                               "required": ["bg", "fg", "panel", "border", "accent"]}
                },
                "required": ["action"]
            }
        }
    },
    {
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
                        "classify_events", "mark_email_boundaries", "learn_sender_signatures",
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
    },
    {
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
                    "reminder_minutes": {"type": "integer", "description": "For create_event: create an Odysseus reminder this many minutes before the event, e.g. 5 for 'reminder 5 min before'."}
                },
                "required": ["action"]
            }
        }
    },
    {
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
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "manage_skills",
            "description": (
                "Read or modify the user's skill library. Skills are SKILL.md files "
                "(YAML frontmatter + structured body: When to Use / Procedure / "
                "Pitfalls / Verification) and follow a draft → published lifecycle. "
                "Use progressive disclosure: 'list' to see what exists, 'view' to "
                "load full content for a single skill, 'view_ref' for sub-files. "
                "Use 'patch' for surgical text edits and 'edit' for full rewrites. "
                "'publish' once you've verified the procedure works. For add, "
                "always provide an explicit name slug and only tell the user the "
                "exact name returned by the tool."
            ),
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
    },
    {
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
                    "include": {"type": "string", "description": "Glob filter for specific files (e.g. '*Q4_K_M*')"},
                },
                "required": ["repo_id"]
            }
        }
    },
    {
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
                    "local": {"type": "boolean", "description": "Force serve on THIS machine instead of the default remote server."},
                },
                "required": ["repo_id", "cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_served_models",
            "description": "List currently running model servers with status, model name, port, throughput, and structured Cookbook diagnoses. If a serve failed, this includes recent logs plus retry suggestions/adjusted commands the agent can use with serve_model.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_served_model",
            "description": "Stop a running model server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID of the server to stop"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_downloads",
            "description": "List in-progress model downloads in the Cookbook. Shows each download's model name, phase, percent (if available), session ID, and remote host.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_download",
            "description": "Cancel an in-progress model download by killing its tmux session. Use list_downloads first to get the session_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Tmux session ID from list_downloads (e.g. 'cookbook-a1b2c3d4')"},
                },
                "required": ["session_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_hf_models",
            "description": "Search HuggingFace for models matching a query. Returns a ranked list of repo IDs, sizes (when available), and download counts. Use this when the user wants to find a model to download.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms (e.g. 'Qwen 8B', 'flux', 'llama-3 instruct')"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_cookbook_servers",
            "description": "List the cookbook's configured servers (remote GPU boxes + local) and the current default host. Call this before download_model/serve_model when the user didn't specify a host, so models go to the right machine (where the GPUs and model cache are) instead of localhost. If multiple servers and intent is ambiguous, show them and ask the user which.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_serve_presets",
            "description": "List saved Cookbook serve presets. Each preset is a launch template (name, model, host, port, tmux cmd) the user previously saved from the UI. Call this BEFORE serve_model when the user asks to launch a model by name — there's almost always a working preset for it.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "serve_preset",
            "description": "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user saved before. This is the preferred way to start a known model (SD3.5, vLLM presets, etc.) — don't fabricate launch commands when a preset exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Preset name (exact or case-insensitive substring of one returned by list_serve_presets)"},
                },
                "required": ["name"]
            }
        }
    },
    {
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
    },
    {
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
    },
    {
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
                    "scale": {"type": "number", "description": "For upscale: scale factor (default 2)"},
                },
                "required": ["image_id", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_research",
            "description": "Start a deep research task on a topic. Returns a task ID for tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Research question or topic"},
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_contact",
            "description": "Look up a contact's email address by name. Searches CardDAV address book and sent email history. Use when the user says 'message [name]' or 'email [name]' without an email address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name to search for"},
                },
                "required": ["name"]
            }
        }
    },
    {
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
                    "phones": {"type": "array", "items": {"type": "string"}, "description": "Full list of phone numbers (for update)."},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_accounts",
            "description": "List configured email accounts. Use this before checking mail when the user names a mailbox/account such as Gmail, work, or a custom domain, then pass the returned account name/email/id to the other email tools.",
            "parameters": {
                "type": "object",
                "properties": {},
            }
        }
    },
    {
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
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
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
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Read the full content of a specific email by UID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID to read"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid"]
            }
        }
    },
    {
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
                    "account": {"type": "string", "description": "Optional account name/email/id from list_email_accounts, especially when the UID came from a non-default mailbox"},
                },
                "required": ["uid", "body"]
            }
        }
    },
    {
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
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts, e.g. Gmail or user@example.com"},
                },
                "required": ["action"]
            }
        }
    },
    {
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
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_email",
            "description": "Archive one email by UID. For multiple messages, use bulk_email instead. Always pass account when the email came from a named account such as Gmail.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Email UID from list_emails/read_email"},
                    "folder": {"type": "string", "description": "IMAP folder (default: INBOX)"},
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
    {
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
                    "account": {"type": "string", "description": "Account name/email/id from list_email_accounts"},
                },
                "required": ["uid"]
            }
        }
    },
]


# ---------------------------------------------------------------------------
# Converter: native function call -> ToolBlock
# ---------------------------------------------------------------------------

def function_call_to_tool_block(name: str, arguments: str) -> Optional[ToolBlock]:
    """Convert a native function call into a ToolBlock for the existing execution pipeline."""
    try:
        if not arguments or (isinstance(arguments, str) and not arguments.strip()):
            args = {}
        else:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError):
        logger.error(f"Failed to parse function call arguments for {name}: {arguments}")
        return None

    tool_type = _TOOL_NAME_MAP.get(name, name)
    # Allow MCP tools through (namespaced as mcp__serverid__toolname)
    if tool_type.startswith("mcp__"):
        content = json.dumps(args) if args else "{}"
        return ToolBlock(tool_type, content)
    # Email tools are implemented as MCP — route them to email
    _BUILTIN_EMAIL_TOOLS = {"list_email_accounts", "send_email", "list_emails", "read_email", "reply_to_email",
                            "archive_email", "delete_email", "mark_email_read", "bulk_email", "download_attachment"}
    if name in _BUILTIN_EMAIL_TOOLS:
        return ToolBlock(f"mcp__email__{name}", json.dumps(args) if args else "{}")
    if tool_type not in TOOL_TAGS:
        logger.warning(f"Unknown function call: {name}")
        return None

    # Convert structured args back to the text format each tool expects
    if tool_type == "bash":
        content = args.get("command", "")
    elif tool_type == "python":
        content = args.get("code", "")
    elif tool_type == "web_search":
        content = args.get("query", "")
    elif tool_type == "read_file":
        content = args.get("path", "")
    elif tool_type == "write_file":
        content = args.get("path", "") + "\n" + args.get("content", "")
    elif tool_type == "create_document":
        parts = [args.get("title", "Untitled")]
        if args.get("language"):
            parts.append(args["language"])
        parts.append(args.get("content", ""))
        content = "\n".join(parts)
    elif tool_type == "edit_document":
        blocks = []
        for edit in args.get("edits", []):
            blocks.append(
                f'<<<FIND>>>\n{edit.get("find", "")}\n<<<REPLACE>>>\n{edit.get("replace", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "suggest_document":
        blocks = []
        for s in args.get("suggestions", []):
            blocks.append(
                f'<<<FIND>>>\n{s.get("find", "")}\n<<<SUGGEST>>>\n{s.get("replace", "")}\n<<<REASON>>>\n{s.get("reason", "")}\n<<<END>>>'
            )
        content = "\n".join(blocks)
    elif tool_type == "update_document":
        content = args.get("content", "")
    elif tool_type == "search_chats":
        content = args.get("query", "")
    elif tool_type == "chat_with_model":
        content = args.get("model", "") + "\n" + args.get("message", "")
    elif tool_type == "create_session":
        content = args.get("name", "Untitled") + "\n" + args.get("model", "")
    elif tool_type == "list_sessions":
        content = args.get("filter", "")
    elif tool_type == "send_to_session":
        content = args.get("session_id", "") + "\n" + args.get("message", "")
    elif tool_type == "pipeline":
        # Pass as JSON for the pipeline parser
        content = json.dumps({"steps": args.get("steps", [])})
    elif tool_type == "manage_session":
        action = args.get("action", "")
        value = args.get("value", "")
        # `list` is the only action that takes an OPTIONAL keyword
        # filter — never a session_id. Don't leak the "current" default
        # into the filter slot (was producing "No sessions found
        # matching 'current'" when the agent omitted session_id).
        if action == "list":
            keyword = args.get("session_id", "") or args.get("keyword", "") or value
            content = "list" + (("\n" + keyword) if keyword and keyword.lower() != "current" else "")
        else:
            sid = args.get("session_id", "current")
            content = action + "\n" + sid
            if value:
                content += "\n" + value
    elif tool_type == "manage_memory":
        action = args.get("action", "")
        if action == "add":
            content = "add\n" + args.get("text", "")
            if args.get("category"):
                content += "\n" + args["category"]
        elif action == "edit":
            content = "edit\n" + args.get("memory_id", "") + "\n" + args.get("text", "")
        elif action == "delete":
            content = "delete\n" + args.get("memory_id", "")
        elif action == "search":
            content = "search\n" + args.get("text", "")
        elif action == "list":
            content = "list"
            if args.get("category"):
                content += "\n" + args["category"]
        else:
            content = action
    elif tool_type == "list_models":
        content = args.get("filter", "")
    elif tool_type == "ui_control":
        action = args.get("action", "")
        name = args.get("name", "")
        value = args.get("value", "")
        if action == "toggle":
            content = f"toggle {name} {value}"
        elif action == "open_panel":
            content = f"open_panel {name or value}"
        elif action == "open_email_reply":
            uid = args.get("uid") or name
            folder = args.get("folder") or value or "INBOX"
            mode = args.get("mode") or "reply"
            content = f"open_email_reply {uid} {folder} {mode}"
        elif action == "set_mode":
            content = f"set_mode {value or name}"
        elif action == "switch_model":
            content = f"switch_model {value or name}"
        elif action == "set_theme":
            content = f"set_theme {value or name}"
        elif action == "create_theme":
            colors = args.get("colors", {})
            theme_name = name or value or "custom"
            bg = colors.get("bg", "#282c34")
            fg = colors.get("fg", "#9cdef2")
            panel = colors.get("panel", "#111111")
            border = colors.get("border", "#355a66")
            accent = colors.get("accent", "#e06c75")
            content = f"create_theme {theme_name} {bg} {fg} {panel} {border} {accent}"
            # Append advanced overrides as key=value
            adv_keys = [
                "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
                "sectionAccent", "brandColor", "inputBg", "inputBorder",
                "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
                "toggleBg", "toggleActive", "accentPrimary", "accentError",
            ]
            for ak in adv_keys:
                if colors.get(ak):
                    content += f" {ak}={colors[ak]}"
        else:
            content = action
    elif tool_type in ("manage_tasks", "manage_skills", "api_call",
                        "manage_endpoints", "manage_mcp", "manage_webhooks",
                        "manage_tokens", "manage_documents", "manage_settings"):
        content = json.dumps(args)
    elif tool_type == "ask_teacher":
        content = args.get("model", "auto") + "\n" + args.get("problem", "")
    else:
        content = json.dumps(args)

    return ToolBlock(tool_type, content)
