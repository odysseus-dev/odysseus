#!/usr/bin/env python3
"""Run related multi-turn Odysseus tool flows for SFT curation.

Unlike the broad domain audit, this runner keeps one realistic task thread per
session.  Each flow has 3-4 related turns so the kept SFT rows teach follow-up
tool use, not isolated one-shot tool invocation.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.odysseus_domain_audit import (  # noqa: E402
    Case,
    _cleanup_fixtures,
    _create_session,
    _durable_tool_events,
    _event_text,
    _history_pairs,
    _render_prompt,
    _run_turn,
    _seed_fixtures,
    _session_payload,
    score_case,
)


@dataclass(frozen=True)
class FlowTurn:
    id: str
    prompt: str
    tools: tuple[str, ...]
    dry_run: bool = False


@dataclass(frozen=True)
class Flow:
    id: str
    domain: str
    title: str
    turns: tuple[FlowTurn, ...]


COMPOUND_REQUIRED_TOOLS: dict[tuple[str, str], tuple[str, ...]] = {
    ("ui_calendar_notes_context", "open_calendar"): ("ui_control", "manage_calendar"),
    ("ui_calendar_notes_context", "open_notes"): ("ui_control", "manage_notes"),
}

PROVIDER_ERROR_RE = re.compile(
    r"(?:openrouter|model provider|upstream).{0,160}"
    r"(?:unreachable|cooldown|timed?\s*out|timeout|no usable output|HTTP\s*(?:429|5\d\d))"
    r"|(?:read timeout|HTTP\s*(?:429|5\d\d)).{0,160}(?:openrouter|model provider|upstream)"
    r"|\bNo enabled endpoints found\b",
    re.IGNORECASE | re.DOTALL,
)


def _flow(
    flow_id: str,
    domain: str,
    title: str,
    rows: list[tuple[str, str, tuple[str, ...], bool] | tuple[str, str, tuple[str, ...]]],
) -> Flow:
    turns = []
    for row in rows:
        if len(row) == 3:
            turn_id, prompt, tools = row
            dry_run = False
        else:
            turn_id, prompt, tools, dry_run = row
        turns.append(FlowTurn(turn_id, prompt, tools, dry_run))
    if not 3 <= len(turns) <= 4:
        raise AssertionError(f"{flow_id} must have 3-4 turns, got {len(turns)}")
    return Flow(flow_id, domain, title, tuple(turns))


def flow_matrix() -> list[Flow]:
    return [
        _flow("skills_create_edit_cleanup", "skills", "Skill lifecycle", [
            ("list", "List my skills and tell me whether there is already an audit skill named audit-fixture-{marker}.", ("manage_skills",)),
            ("create", "Create a draft skill named audit-fixture-{marker} for reviewing tool traces.", ("manage_skills",)),
            ("edit", "Open that audit skill and add a verification step about checking persisted tool calls.", ("manage_skills",)),
            ("delete", "Delete the audit-fixture-{marker} skill now that the test is done.", ("manage_skills",)),
        ]),
        _flow("skills_search_then_panel", "skills", "Skill search and UI follow-up", [
            ("search", "Search my skills for email workflow guidance.", ("manage_skills",)),
            ("open", "Open the Skills panel so I can inspect those results too.", ("ui_control",)),
            ("view", "Search my skills for email workflow guidance again and summarize the most relevant verification guidance.", ("manage_skills",)),
        ]),
        _flow("memory_add_find_edit_delete", "memory", "Memory lifecycle", [
            ("add", "Remember this temporary audit detail: marker {marker} prefers compact SFT repair notes.", ("manage_memory",)),
            ("find", "Find the memory you just saved about marker {marker}.", ("manage_memory",)),
            ("edit", "Update that memory so it says marker {marker} prefers compact SFT repair notes with exact tool evidence.", ("manage_memory",)),
            ("delete", "Delete the temporary marker {marker} memory.", ("manage_memory",)),
        ]),
        _flow("memory_ui_followup", "memory", "Memory panel and follow-up", [
            ("open", "Open my memories panel.", ("ui_control",)),
            ("list", "List my saved memories and include the latest few.", ("manage_memory",)),
            ("search", "Search those memories for timezone or local-date preferences.", ("manage_memory",)),
        ]),
        _flow("tasks_create_edit_cleanup", "tasks", "Task lifecycle", [
            ("create", "Create a daily task named audit-task-{marker} that reminds me to review SFT traces at 9am.", ("manage_tasks",)),
            ("show", "Show the audit-task-{marker} task you just created.", ("manage_tasks",)),
            ("edit", "Change audit-task-{marker} to run at 10am instead.", ("manage_tasks",)),
            ("delete", "Delete audit-task-{marker}.", ("manage_tasks",)),
        ]),
        _flow("tasks_pause_resume_cleanup", "tasks", "Task state changes", [
            ("create", "Create a weekly task named audit-weekly-{marker} to summarize my notes every Monday morning.", ("manage_tasks",)),
            ("pause", "Pause audit-weekly-{marker}.", ("manage_tasks",)),
            ("resume", "Resume audit-weekly-{marker}.", ("manage_tasks",)),
            ("delete", "Delete audit-weekly-{marker}.", ("manage_tasks",)),
        ]),
        _flow("ui_calendar_notes_context", "notes", "UI panel context handoff", [
            ("open_calendar", "Open my calendar panel.", ("ui_control", "manage_calendar")),
            ("read_calendar", "What events are visible for the next week?", ("manage_calendar",)),
            ("open_notes", "Open my notes panel and create a short note called audit-calendar-note-{marker} summarizing that calendar context.", ("ui_control", "manage_notes")),
            ("delete_note", "Delete the audit-calendar-note-{marker} note.", ("manage_notes",)),
        ]),
        _flow("documents_open_edit_cleanup", "documents", "Document editing lifecycle", [
            ("create", "Create a document titled audit document {marker} with one sentence about SFT harness repair.", ("manage_documents", "create_document")),
            ("open", "Open audit document {marker} in the document editor.", ("manage_documents", "ui_control")),
            ("edit", "Append this sentence to the open document: Tool calls must persist after refresh.", ("edit_document", "update_document", "manage_documents")),
            ("delete", "Delete audit document {marker}.", ("manage_documents",)),
        ]),
        _flow("theme_open_change_restore", "theme", "Theme UI settings", [
            ("open", "Open theme settings.", ("ui_control",)),
            ("set_dark", "Set the theme to dark.", ("ui_control",)),
            ("set_light", "Now set the theme to light.", ("ui_control",)),
        ]),
        _flow("cookbook_browse_models", "cookbook", "Cookbook read-only model browsing", [
            ("open", "Open the Cookbook panel.", ("ui_control",)),
            ("servers", "List Cookbook servers and tell me whether anything is running.", ("list_cookbook_servers", "list_served_models"), True),
            ("search", "Search official Hugging Face models for a small Qwen instruct model, but do not download or serve anything.", ("search_hf_models",), True),
            ("cached", "List cached models, still without launching anything.", ("list_cached_models",), True),
        ]),
        _flow("cookbook_runtime_inventory", "cookbook", "Cookbook runtime inventory", [
            ("servers", "Show my configured Cookbook servers and identify the default one.", ("list_cookbook_servers",), True),
            ("running", "Now check which models are currently being served on those servers.", ("list_served_models",), True),
            ("downloads", "Check whether any model downloads are active or recently completed.", ("list_downloads",), True),
            ("presets", "List the saved serve presets I could use later, but do not launch one.", ("list_serve_presets",), True),
        ]),
        _flow("cookbook_preset_adoption_preview", "cookbook", "Preset and adoption dry-run", [
            ("presets", "List my saved Cookbook serve presets and identify the first valid preset without launching anything.", ("list_serve_presets",), True),
            ("preview_preset", "Use the serve preset tool in dry-run mode to preview launching that first preset. Do not start a server.", ("serve_preset",), True),
            ("preview_adopt", "Use the adopt served model tool in dry-run mode to preview registering tmux session audit-external-{marker} for model audit/tiny-model on local port 18092, without checking tmux or changing state.", ("adopt_served_model",), True),
        ]),
        _flow("cookbook_failed_server_cleanup", "cookbook", "Failed server inspection and cleanup", [
            ("list", "List Cookbook model servers and confirm whether tracked session serve-734ca165 is already in an error state.", ("list_served_models",), True),
            ("tail", "Read the last 120 lines of serve output for tracked session serve-734ca165 and summarize the startup failure.", ("tail_serve_output",), True),
            ("stop", "Stop and clean up the already-failed tracked Cookbook session serve-734ca165 now.", ("stop_served_model",)),
            ("verify", "List Cookbook model servers again and confirm serve-734ca165 has no live process. Its historical error record may remain visible.", ("list_served_models",), True),
        ]),
        _flow("cookbook_download_cancel", "cookbook", "Download start and cancellation", [
            ("start", "Start a local Cookbook download of Qwen/Qwen3-8B, including only *.safetensors files. Return the tracked download session ID.", ("download_model",)),
            ("list", "List active Cookbook downloads and identify the Qwen/Qwen3-8B session you just started.", ("list_downloads",), True),
            ("cancel", "Cancel that Qwen/Qwen3-8B download now using its exact tracked session ID.", ("cancel_download",)),
            ("verify", "List active Cookbook downloads again and confirm the cancelled session is no longer running.", ("list_downloads",), True),
        ]),
        _flow("cookbook_tiny_model_download", "cookbook", "Tiny model download", [
            ("start", "Start a local Cookbook download of bartowski/SmolLM2-135M-Instruct-GGUF, including only *Q4_K_M.gguf. Return the tracked session ID.", ("download_model",)),
            ("status", "List Cookbook downloads and report the SmolLM2 download status.", ("list_downloads",), True),
            ("cached", "Check the local Cookbook cache for SmolLM2-135M-Instruct-GGUF and report whether the Q4_K_M file is available.", ("list_cached_models",), True),
        ]),
        _flow("cookbook_tiny_serve_lifecycle", "cookbook", "Tiny model serve lifecycle", [
            ("serve", f"Serve bartowski/SmolLM2-135M-Instruct-GGUF locally now with this exact command: {os.environ.get('ODYSSEUS_LLAMA_SERVER', 'llama-server')} -m {os.environ['ODYSSEUS_TINY_MODEL_PATH']} --host 127.0.0.1 --port 18091 -c 512 -ngl 0. Return the tracked serve session ID.", ("serve_model",)),
            ("status", "List Cookbook model servers and report the status of the SmolLM2 server you just started on port 18091.", ("list_served_models",), True),
            ("tail", "Read the last 80 lines of serve output for that tracked SmolLM2 session and report whether startup completed.", ("tail_serve_output",), True),
            ("stop", "Stop the tracked SmolLM2 Cookbook server on port 18091 now.", ("stop_served_model",)),
        ]),
        _flow("cookbook_model_comparison", "cookbook", "Cookbook model discovery comparison", [
            ("search", "Use the Cookbook Hugging Face search to find official compact Gemma instruct models. Do not use the configured endpoint model list, and do not download anything.", ("search_hf_models",), True),
            ("cached", "Compare that with the models already cached locally.", ("list_cached_models",), True),
            ("presets", "Check whether any saved serve preset appears suitable for a compact model, without launching it.", ("list_serve_presets",), True),
            ("status", "Finally check active Cookbook downloads now and confirm this comparison did not start one.", ("list_downloads",), True),
        ]),
        _flow("browser_search_fetch", "search", "Search then browser fallback", [
            ("search", "Find the official website for the Python packaging user guide.", ("web_search",)),
            ("fetch", "Open the most relevant result and summarize the install guidance.", ("web_fetch",)),
            ("browser", "Use the private browser to open the Python packaging user guide page and report the rendered page title. Do not search again.", ("private_browser",), True),
        ]),
        _flow("browser_rendered_page_inspection", "search", "Private browser rendered-page inspection", [
            ("navigate", "Use the private browser to open https://example.com and report the rendered page title. Do not use web search or web fetch.", ("private_browser",), True),
            ("snapshot", "Take a private-browser accessibility snapshot of the open page and summarize its visible structure.", ("private_browser",), True),
            ("find", "Use the private browser to find the visible text 'Learn more' on the currently open page.", ("private_browser",), True),
            ("evaluate", "Use the private browser on the currently open page to evaluate document.location.hostname and report the result.", ("private_browser",), True),
        ]),
        _flow("contacts_email_draft_preview", "email", "Contact resolution and draft preview", [
            ("resolve", "Find Priya Shah in my contacts.", ("resolve_contact", "manage_contact")),
            ("recent", "Find recent emails from Priya so I can answer in context.", ("list_emails",)),
            ("draft", "Draft a polite reply to Priya's latest email, but leave it as a reviewable draft.", ("draft_email_reply", "ai_draft_email_reply", "read_email", "ui_control")),
        ]),
        _flow("email_account_search_read_state", "email", "Mailbox search and read-state restore", [
            ("accounts", "List my configured email accounts and identify the Primary Inbox.", ("list_email_accounts",)),
            ("search", "Search the Primary Inbox for messages from Lena Ortiz and show the matching UID.", ("search_emails",)),
            ("unread", "Mark Lena Ortiz's matching email UID 10 as unread in the Primary Inbox.", ("mark_email_read",)),
            ("restore", "Mark that same email UID 10 as read again to restore its state.", ("mark_email_read",)),
        ]),
        _flow("email_archive_restore", "email", "Email archive and restore", [
            ("search", "Search the Primary Inbox for messages from Lena Ortiz and show the matching UID.", ("search_emails",)),
            ("archive", "Archive Lena Ortiz's matching email UID 10 now.", ("archive_email",)),
            ("restore", "Unarchive email UID 10 back to the Primary Inbox now.", ("manage_email_state",)),
        ]),
        _flow("email_send_and_reply", "email", "Synthetic immediate email actions", [
            ("accounts", "List my configured email accounts and identify the Primary Inbox.", ("list_email_accounts",)),
            ("send", "Send an email now from the Primary Inbox to fixture-05@example.test with subject SFT delivery {marker} and body This is a synthetic delivery audit.", ("send_email",)),
            ("read", "Read email UID 1 in the Primary Inbox before replying.", ("read_email",)),
            ("reply", "Send a reply now to email UID 1 saying: Thanks, I have the next steps.", ("reply_to_email",)),
        ]),
        _flow("email_ai_reply_preview", "email", "AI-assisted reply preview", [
            ("read", "Read email UID 1 in the Primary Inbox so I can answer it in context.", ("read_email",)),
            ("draft", "Use AI Reply for email UID 1 in the Primary Inbox to create a concise, polite reply draft. Leave it reviewable and do not send it.", ("ai_draft_email_reply",)),
            ("open", "Open the email panel with that reply draft still available for review.", ("ui_control",)),
        ]),
        _flow("email_junk_delete_verify", "email", "Synthetic junk deletion and verification", [
            ("scan", "Scan both the Primary Inbox and Junk folder for likely spam. Identify the highest-scoring suspicious message already in Junk, but do not change anything yet.", ("scan_spam",)),
            ("delete", "Delete only the suspicious Junk message you just identified. Do not block its sender.", ("delete_email",)),
            ("verify", "Re-scan the Junk folder and confirm that exact deleted message is no longer listed.", ("scan_spam",)),
        ]),
        _flow("email_unsubscribe_verify", "email", "Newsletter unsubscribe lifecycle", [
            ("scan", "Scan the Primary Inbox for newsletter or mailing-list messages that provide an unsubscribe option. Do not change anything yet.", ("scan_email_unsubscribes",)),
            ("unsubscribe", "Unsubscribe from only the first mailing list you just identified, using that message's exact UID.", ("unsubscribe_email",)),
            ("verify", "Scan the Primary Inbox for unsubscribe options again and confirm that exact mailing list is no longer an actionable candidate.", ("scan_email_unsubscribes",)),
        ]),
        _flow("documents_suggest_cleanup", "documents", "Document suggestion lifecycle", [
            ("create", "Create a document titled Suggestion audit {marker} with exactly this sentence: The weekly report is very good.", ("create_document",)),
            ("suggest", "Suggest changing 'very good' to 'clear and actionable' in the open document, explaining that the wording is more specific. Do not apply the suggestion.", ("suggest_document",)),
            ("find", "Find the document titled Suggestion audit {marker} in my document library.", ("manage_documents",)),
            ("delete", "Delete the document titled Suggestion audit {marker} now that the audit is complete.", ("manage_documents",)),
        ]),
        _flow("image_generate_edit", "images", "Image generation and edit", [
            ("generate", "Generate a simple square image of a red ceramic mug on a plain white background for this synthetic audit.", ("generate_image",)),
            ("edit", "Upscale the image you just generated by 2x.", ("edit_image",)),
            ("gallery", "Use the safe internal app API to read the gallery list and confirm both image records are visible.", ("app_api",), True),
        ]),
        _flow("image_existing_upscale_verify", "images", "Existing gallery image edit", [
            ("gallery", "Use the safe internal app API to list gallery images and identify the first available image ID. Do not modify anything yet.", ("app_api",), True),
            ("edit", "Upscale that first gallery image by 2x using the image editing tool.", ("edit_image",)),
            ("verify", "Use the safe internal app API to list the gallery again and confirm the upscaled image record exists.", ("app_api",), True),
        ]),
        _flow("settings_tool_toggle_restore", "settings", "Settings tool toggle with restore", [
            ("list", "Show which agent tools are currently disabled.", ("manage_settings",)),
            ("disable", "Temporarily disable the image generation tool for this audit marker {marker}.", ("manage_settings",)),
            ("enable", "Turn image generation back on now.", ("manage_settings",)),
            ("open", "Open Settings so I can review the tool toggle state.", ("ui_control", "manage_settings")),
        ]),
        _flow("sessions_create_list_delete", "sessions", "Session management lifecycle", [
            ("list", "List my recent chats and include clickable chat links.", ("list_sessions",)),
            ("create", "Create a scratch chat named audit helper {marker} using model moonshotai/kimi-k3.", ("create_session",)),
            ("find", "Find the audit helper {marker} chat in my chat list.", ("list_sessions",)),
            ("delete", "Delete the audit helper {marker} scratch chat.", ("manage_session",)),
        ]),
        _flow("sessions_send_and_cleanup", "sessions", "Cross-chat message lifecycle", [
            ("create", "Create a scratch chat named audit relay {marker} using model moonshotai/kimi-k3.", ("create_session",)),
            ("send", "Send that audit relay chat this message: Reply with exactly RELAY {marker} RECEIVED.", ("send_to_session",)),
            ("find", "List chats matching audit relay {marker} so I can verify it exists.", ("list_sessions",)),
            ("delete", "Delete the audit relay {marker} scratch chat now.", ("manage_session",)),
        ]),
        _flow("sessions_search_relay_cleanup", "sessions", "Cross-chat transcript search lifecycle", [
            ("create", "Create a scratch chat named searchable relay {marker} using model moonshotai/kimi-k3.", ("create_session",)),
            ("send", "Send that searchable relay chat this message: Reply with exactly SEARCHABLE {marker} RECEIVED.", ("send_to_session",)),
            ("search", "Search my prior chat transcripts for the exact phrase SEARCHABLE {marker} RECEIVED and show the matching chat.", ("search_chats",)),
            ("delete", "Delete the searchable relay {marker} scratch chat now.", ("manage_session",)),
        ]),
        _flow("research_start_list_open", "research", "Research report lifecycle", [
            ("list", "List my saved research reports and find the most recent completed SearXNG report.", ("manage_research",)),
            ("open", "Open that completed SearXNG research report in the research panel.", ("manage_research", "ui_control")),
            ("start", "Start a concise new research report about SearXNG privacy defaults and return its task id.", ("trigger_research",)),
        ]),
        _flow("delegation_second_opinion", "delegation", "Model delegation pipeline", [
            ("models", "List the available models I can delegate a short question to.", ("list_models",), True),
            ("delegate", "Ask qwen/qwen3.8-flash for a one-sentence definition of supervised fine-tuning.", ("chat_with_model",)),
            ("pipeline", "Run a two-step pipeline using z-ai/glm-5.3-flash to draft a one-sentence SFT trace check, then qwen/qwen3.8-flash to tighten it.", ("pipeline",)),
        ]),
        _flow("delegation_teacher_review", "delegation", "Teacher review follow-up", [
            ("review", "Use the teacher review tool ask_teacher with model anthropic/claude-sonnet-4.5 to review this answer for tool-grounding: 'The action succeeded because the assistant said it did.'", ("ask_teacher",)),
            ("improve", "Use ask_teacher again with model anthropic/claude-sonnet-4.5 to rewrite that answer as one sentence requiring persisted tool evidence.", ("ask_teacher",)),
            ("check", "Use ask_teacher once more with model anthropic/claude-sonnet-4.5 to check whether the rewritten sentence is verifiable and concise.", ("ask_teacher",)),
        ]),
        _flow("plan_create_progress_finish", "planning", "Plan lifecycle", [
            ("create", "Make a three-step plan to audit a tool trace: inspect persisted calls, verify outputs, then retain or delete the trace.", ("update_plan",)),
            ("progress", "Update that plan: mark persisted-call inspection complete and output verification in progress.", ("update_plan",)),
            ("finish", "Finish the plan by marking output verification and the retain-or-delete decision complete.", ("update_plan",)),
        ]),
        _flow("internal_api_discovery", "settings", "Safe internal API discovery", [
            ("discover", "Use the internal app API catalog to list safe gallery endpoints; do not modify anything.", ("app_api",), True),
            ("read", "Use the safe internal app API to read the gallery list now; do not create or delete images.", ("app_api",), True),
            ("settings", "List current settings without changing them.", ("manage_settings",), True),
        ]),
        _flow("admin_inventory_readonly", "settings", "Admin inventory read-only", [
            ("endpoints", "List configured model endpoints and summarize which ones are enabled.", ("manage_endpoints",), True),
            ("mcp", "List configured MCP servers and say which built-in tools are connected.", ("manage_mcp",), True),
            ("tokens", "List API tokens by name and prefix only; do not create or reveal any secret token.", ("manage_tokens",), True),
            ("webhooks", "List webhook integrations and whether any reminder webhook is configured.", ("manage_webhooks", "manage_settings"), True),
        ]),
        _flow("workspace_file_shell_cleanup", "workspace", "Safe workspace file lifecycle", [
            ("write", "Create a workspace file named odysseus-sft-{marker}.txt with two lines: audit marker {marker} and status draft.", ("apply_patch", "write_file")),
            ("read", "Inspect odysseus-sft-{marker}.txt in the workspace and confirm the marker line.", ("grep", "ls", "read_file")),
            ("edit", "Use a workspace file edit tool to change the status line in odysseus-sft-{marker}.txt from draft to verified.", ("apply_patch", "edit_file")),
            ("cleanup", "Delete the workspace file odysseus-sft-{marker}.txt now that the audit is done.", ("apply_patch", "write_file", "edit_file")),
        ]),
    ]


def load_flow_spec(path: Path) -> list[Flow]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_flows = payload.get("flows") if isinstance(payload, dict) else payload
    if not isinstance(raw_flows, list):
        raise ValueError("flow spec must be a list or an object containing a flows list")
    flows: list[Flow] = []
    for raw in raw_flows:
        if not isinstance(raw, dict) or not isinstance(raw.get("turns"), list):
            raise ValueError("each flow must be an object with a turns list")
        rows = []
        for turn in raw["turns"]:
            tools = turn.get("tools") or []
            if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
                raise ValueError(f"{raw.get('id')}: turn tools must be a list of strings")
            rows.append((
                str(turn["id"]),
                str(turn["prompt"]),
                tuple(tools),
                bool(turn.get("dry_run", False)),
            ))
        flows.append(_flow(str(raw["id"]), str(raw["domain"]), str(raw["title"]), rows))
    return flows


def _tool_names(events: list[dict[str, Any]]) -> list[str]:
    tools = []
    for event in events:
        if event.get("type") not in {"tool_start", "tool_output"}:
            continue
        name = str(event.get("tool") or "")
        if name:
            normalized = name.removeprefix("mcp__").split("__")[-1]
            if name.startswith("mcp__builtin_browser__") or normalized.startswith("browser_"):
                normalized = "private_browser"
            tools.append(normalized)
    for metric in (event.get("data") for event in events if event.get("type") == "metrics"):
        if not isinstance(metric, dict):
            continue
        for event in metric.get("tool_events") or []:
            if isinstance(event, dict) and event.get("tool"):
                name = str(event["tool"])
                normalized = name.removeprefix("mcp__").split("__")[-1]
                if name.startswith("mcp__builtin_browser__") or normalized.startswith("browser_"):
                    normalized = "private_browser"
                tools.append(normalized)
    return tools


def _score_turn(flow: Flow, turn: FlowTurn, events: list[dict[str, Any]], response: str) -> dict[str, Any]:
    case = Case(
        id=f"{flow.id}_{turn.id}",
        prompt=turn.prompt,
        tools=turn.tools,
        dry_run=turn.dry_run,
    )
    result = score_case(case, events, response)
    observed = _tool_names(events)
    required = COMPOUND_REQUIRED_TOOLS.get((flow.id, turn.id), ())
    if required:
        observed_set = set(observed)
        missing = [name for name in required if name not in observed_set]
        result["required_tools"] = list(required)
        result["missing_required_tools"] = missing
        if missing:
            result["tool_ok"] = False
            result["pass"] = False
            result.setdefault("errors", []).append({
                "type": "missing_required_tools",
                "missing": missing,
            })
    if result["tool_ok"] and result["response_ok"] and not result["errors"]:
        result["pass"] = result["dry_run_ok"]
    return result


def _login_cookie(base_url: str, username: str, password: str) -> str:
    with httpx.Client(follow_redirects=False) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/auth/login",
            json={"username": username, "password": password, "remember": True},
            timeout=30,
        )
        response.raise_for_status()
        cookie = client.cookies.get("odysseus_session")
        if not cookie:
            raise RuntimeError("login succeeded but no odysseus_session cookie was returned")
        return str(cookie)


def _safe_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row, dict) else {}
    if isinstance(metadata, str):
        with contextlib.suppress(json.JSONDecodeError):
            metadata = json.loads(metadata)
    return metadata if isinstance(metadata, dict) else {}


def _latest_assistant_text(history: dict[str, Any]) -> str:
    rows = history.get("history") if isinstance(history, dict) else None
    if not isinstance(rows, list):
        return ""
    for row in reversed(rows):
        if isinstance(row, dict) and row.get("role") == "assistant":
            return str(row.get("content") or "").strip()
    return ""


def _flow_has_good_training_shape(history: dict[str, Any], expected_turns: int) -> tuple[bool, list[str]]:
    reasons = []
    pairs = _history_pairs(history)
    if len(pairs) < expected_turns:
        reasons.append(f"history has {len(pairs)} user/assistant pairs, expected {expected_turns}")
    for index, (user, assistant) in enumerate(pairs[:expected_turns], 1):
        user_content = str(user.get("content") or "")
        content = str(assistant.get("content") or "")
        metadata = _safe_metadata(assistant)
        if not content.strip():
            reasons.append(f"turn {index} assistant content is empty")
        if re.search(
            r"Here are your (emails|events|tasks|memories) \(\d+\):\n"
            r"(?:\s*[-*]?\s*(?:\[[^\]]+\]\(#(?:email|event|note|task)-|[A-Z]).*){2,}",
            content,
            re.S,
        ):
            reasons.append(f"turn {index} appears to preserve a raw harness dump")
        if _contains_false_tool_failure_claim(content):
            reasons.append(f"turn {index} contains a false/ambiguous failure claim")
        tool_events = metadata.get("tool_events") or []
        if not tool_events:
            reasons.append(f"turn {index} has no persisted tool_events")
        if re.search(r"\bmemory\b", user_content, re.IGNORECASE) and re.search(
            r"\byou\s+just\s+saved\b", user_content, re.IGNORECASE
        ) and re.search(r"\bNo memories found\b", content, re.IGNORECASE):
            reasons.append(f"turn {index} failed to find the just-saved memory")
        if re.search(r"\bfind\b.{0,80}\b(?:chat|session|conversation)\b", user_content, re.IGNORECASE) and re.search(
            r"\bNo sessions found\b", content, re.IGNORECASE
        ):
            reasons.append(f"turn {index} failed to find the just-created chat")
        for event in tool_events:
            if not isinstance(event, dict):
                continue
            output = str(event.get("output") or "")
            exit_code = event.get("exit_code")
            explicit_persisted_failure = (
                event.get("tool") == "ask_teacher"
                and re.search(
                    r"^\s*(?:No teacher model configured|No problem description provided)\b",
                    output,
                    re.IGNORECASE,
                )
            )
            if explicit_persisted_failure or exit_code not in (None, 0, "0") or (
                exit_code is None
                and re.search(
                    r"^\s*(?:Error:|Failed\s+to\b|Connection refused\b|Traceback\b|Exception\b)",
                    output,
                    re.IGNORECASE,
                )
            ):
                reasons.append(f"turn {index} has failed tool output from {event.get('tool') or 'unknown tool'}")
        calls = [
            (
                str(event.get("tool") or ""),
                str(event.get("command") or ""),
            )
            for event in tool_events
            if isinstance(event, dict) and event.get("tool")
        ]
        duplicate_calls = len(calls) - len(set(calls))
        if duplicate_calls:
            reasons.append(f"turn {index} repeated {duplicate_calls} identical tool call(s)")
        round_texts = [
            str(item or "").strip()
            for item in (metadata.get("round_texts") or [])
            if str(item or "").strip()
        ]
        if len(round_texts) > 1:
            final_round = round_texts[-1]
            cumulative_progress = all(item in final_round for item in round_texts[:-1])
            repeated_round = len(set(round_texts)) != len(round_texts)
            if repeated_round or not cumulative_progress:
                reasons.append(f"turn {index} has multiple non-empty assistant rounds")
        if _looks_like_concatenated_repeat(content):
            reasons.append(f"turn {index} appears to concatenate repeated assistant answers")
    return not reasons, reasons


def _contains_false_tool_failure_claim(content: str) -> bool:
    """Detect operational tool-failure claims without matching quoted analysis.

    Statements such as "evidence can't be checked" discuss verifiability; they
    are not claims that the assistant lacked a tool. Keep the curation gate
    focused on the assistant or a named tool surface failing to operate.
    """
    text = str(content or "")
    domain = r"(?:tool|skill|memory|task|document|calendar|email|registry)"
    patterns = (
        rf"\b{domain}\b.{{0,80}}\bmay have failed\b",
        rf"\bmay have failed\b.{{0,80}}\b{domain}\b",
        rf"\b(?:I|we)\s+(?:wasn'?t able|couldn'?t|can'?t|cannot|am unable)\b"
        rf".{{0,80}}\b(?:call|use|access|open|read|list|search|run|invoke)\b"
        rf".{{0,80}}\b{domain}\b",
        rf"\b{domain}\b.{{0,80}}\b(?:isn'?t|is not|wasn'?t|was not)\s+"
        r"(?:available|enabled|loaded|accessible|working)\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE | re.S) for pattern in patterns)


def _looks_like_concatenated_repeat(content: str) -> bool:
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if len(text) < 80:
        return False
    starts = [
        r"No agent tools are currently disabled",
        r"Done\s+[-—]\s+the image generation tool",
        r"Image generation is back on",
        r"Here are your",
        r"Here's what",
        r"The user asked",
    ]
    return any(len(re.findall(pattern, text, re.IGNORECASE)) >= 2 for pattern in starts)


def _provider_failure(events: list[dict[str, Any]], response: str = "") -> bool:
    evidence = [str(response or "")]
    for event in events:
        if event.get("type") == "error":
            if event.get("status") in {429, 502, 503, 504}:
                return True
            evidence.append(json.dumps(event, ensure_ascii=False, default=str))
        if event.get("type") == "tool_output":
            evidence.append(str(event.get("output") or ""))
    return bool(PROVIDER_ERROR_RE.search("\n".join(evidence)))


def _run_turn_with_provider_retry(
    client: httpx.Client,
    args: argparse.Namespace,
    sid: str,
    prompt: str,
) -> tuple[list[dict[str, Any]], int]:
    attempts = max(1, int(args.provider_retries) + 1)
    events: list[dict[str, Any]] = []
    for attempt in range(attempts):
        events = _run_turn(client, args, sid, prompt)
        if not _provider_failure(events, _event_text(events)):
            return events, attempt
        if attempt + 1 < attempts:
            time.sleep(float(args.provider_retry_delay) * (attempt + 1))
    return events, attempts - 1


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--cookie", default=os.environ.get("ODY_COOKIE", ""))
    parser.add_argument("--username", default="sft_alex_creator")
    parser.add_argument("--password", default=os.environ.get("ODYSSEUS_QA_PASSWORD"), required=os.environ.get("ODYSSEUS_QA_PASSWORD") is None)
    parser.add_argument("--endpoint-url", default="https://openrouter.ai/api/v1")
    parser.add_argument("--endpoint-id", default="f3904562")
    parser.add_argument("--model", default="moonshotai/kimi-k3")
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tmp" / "related-flow-audit")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--provider-retries", type=int, default=2)
    parser.add_argument("--provider-retry-delay", type=float, default=8.0)
    parser.add_argument("--delete-bad", action="store_true")
    parser.add_argument("--flows", default="all")
    parser.add_argument(
        "--flow-spec-file",
        type=Path,
        help="Optional JSON flow specification; replaces the built-in flow matrix.",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="Workspace/cwd to bind for workspace/file/shell tool flows.",
    )
    parser.add_argument(
        "--client-runtime-context",
        default="",
        help="Optional JSON object passed as client_runtime_context.",
    )
    args = parser.parse_args()
    if args.client_runtime_context:
        try:
            args.client_runtime_context = json.loads(args.client_runtime_context)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--client-runtime-context must be valid JSON: {exc}") from exc
        if not isinstance(args.client_runtime_context, dict):
            raise SystemExit("--client-runtime-context must decode to a JSON object")
    else:
        args.client_runtime_context = None

    cookie = args.cookie or _login_cookie(args.base_url, args.username, args.password)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    marker = f"{stamp}-{uuid.uuid4().hex[:8]}"
    available_flows = load_flow_spec(args.flow_spec_file) if args.flow_spec_file else flow_matrix()
    requested = None if args.flows == "all" else {item.strip() for item in args.flows.split(",") if item.strip()}
    flows = [flow for flow in available_flows if requested is None or flow.id in requested]
    if requested:
        missing = sorted(requested - {flow.id for flow in available_flows})
        if missing:
            parser.error(f"unknown flows: {', '.join(missing)}")

    rows: list[dict[str, Any]] = []
    with httpx.Client(cookies={"odysseus_session": cookie}, follow_redirects=True) as client:
        for flow in flows:
            # Keep fixture identifiers short enough for compact-router slug
            # guards. Long names get truncated by the tool normalizer, which
            # makes later "that item" follow-ups noisy even when the tool
            # effects are technically correct.
            flow_suffix = re.sub(r"[^a-z0-9]+", "-", flow.id.lower()).strip("-")[:8]
            flow_marker = f"{marker}-{flow_suffix}"
            sid = _create_session(client, args, f"related-{flow.id}-{marker}")
            # Seed read-oriented fixtures only. Lifecycle flows create their
            # own record in turn 1; pre-seeding those same markers makes later
            # "that item" follow-ups ambiguous and poisons the trace.
            seed_domains: set[str] = {flow.domain}
            if flow.id in {
                "memory_add_find_edit_delete",
                "tasks_create_edit_cleanup",
                "tasks_pause_resume_cleanup",
                "skills_create_edit_cleanup",
                "documents_open_edit_cleanup",
            }:
                seed_domains.clear()
            for domain in seed_domains:
                with contextlib.suppress(Exception):
                    _seed_fixtures(args.owner, flow_marker, domain, sid)
            turn_results = []
            infrastructure_failure = False
            for turn in flow.turns:
                prompt = _render_prompt(turn.prompt, flow_marker)
                if infrastructure_failure:
                    turn_results.append({
                        "case_id": f"{flow.id}_{turn.id}",
                        "prompt": prompt,
                        "pass": False,
                        "skipped": True,
                        "infrastructure_failure": True,
                        "errors": [{"type": "skipped_after_provider_failure"}],
                        "events": [],
                    })
                    print(f"{flow.id}: {turn.id} SKIP (provider unavailable)", flush=True)
                    continue
                try:
                    events, retry_count = _run_turn_with_provider_retry(client, args, sid, prompt)
                    durable = _session_payload(client, args.base_url, sid)
                    if durable_tools := _durable_tool_events(durable):
                        events = events + [{"type": "metrics", "data": {"tool_events": durable_tools}}]
                    result = _score_turn(
                        flow,
                        turn,
                        events,
                        _latest_assistant_text(durable) or _event_text(events),
                    )
                    result["prompt"] = prompt
                    result["events"] = events
                    result["provider_retries"] = retry_count
                    if _provider_failure(events, result.get("response") or ""):
                        result["infrastructure_failure"] = True
                        infrastructure_failure = True
                except Exception as exc:
                    result = {
                        "case_id": f"{flow.id}_{turn.id}",
                        "prompt": prompt,
                        "pass": False,
                        "errors": [repr(exc)],
                        "events": [],
                    }
                turn_results.append(result)
                print(f"{flow.id}: {turn.id} {'PASS' if result.get('pass') else 'FAIL'}", flush=True)
            history = _session_payload(client, args.base_url, sid)
            shape_ok, shape_reasons = _flow_has_good_training_shape(history, len(flow.turns))
            deterministic_pass = all(bool(turn.get("pass")) for turn in turn_results) and shape_ok
            verdict = "infrastructure" if infrastructure_failure else ("keep" if deterministic_pass else "repair")
            payload = {
                "flow_id": flow.id,
                "domain": flow.domain,
                "title": flow.title,
                "marker": flow_marker,
                "session_id": sid,
                "owner": args.owner,
                "turns": turn_results,
                "history": history,
                "shape_ok": shape_ok,
                "shape_reasons": shape_reasons,
                "deterministic_pass": deterministic_pass,
                "verdict": verdict,
            }
            path = args.out_dir / f"{flow.id}_{sid}.json"
            _write_json(path, payload)
            if args.delete_bad and payload["verdict"] != "keep":
                response = client.delete(f"{args.base_url.rstrip('/')}/api/session/{sid}", timeout=30)
                payload["deleted"] = response.is_success
                _write_json(path, payload)
            else:
                payload["deleted"] = False
            rows.append({
                "flow_id": flow.id,
                "domain": flow.domain,
                "session_id": sid,
                "turns": len(flow.turns),
                "passed": sum(bool(turn.get("pass")) for turn in turn_results),
                "shape_ok": shape_ok,
                "shape_reasons": shape_reasons,
                "verdict": payload["verdict"],
                "deleted": payload["deleted"],
                "artifact": str(path),
            })
            for domain in {"skills", "memory", "tasks", "documents", "notes"}:
                with contextlib.suppress(Exception):
                    _cleanup_fixtures(args.owner, flow_marker, domain)

    summary = {
        "marker": marker,
        "owner": args.owner,
        "model": args.model,
        "flows": rows,
        "totals": {
            "flows": len(rows),
            "kept": sum(1 for row in rows if row["verdict"] == "keep"),
            "repair": sum(1 for row in rows if row["verdict"] == "repair"),
            "infrastructure": sum(1 for row in rows if row["verdict"] == "infrastructure"),
            "turns": sum(row["turns"] for row in rows),
            "passed_turns": sum(row["passed"] for row in rows),
        },
    }
    summary_path = args.out_dir / f"summary_{stamp}.json"
    keep_path = args.out_dir / f"sft_keep_{stamp}.jsonl"
    repair_path = args.out_dir / f"repair_queue_{stamp}.jsonl"
    infrastructure_path = args.out_dir / f"infrastructure_queue_{stamp}.jsonl"
    with (
        keep_path.open("w", encoding="utf-8") as keep,
        repair_path.open("w", encoding="utf-8") as repair,
        infrastructure_path.open("w", encoding="utf-8") as infrastructure,
    ):
        for row in rows:
            artifact = json.loads(Path(row["artifact"]).read_text(encoding="utf-8"))
            pairs = _history_pairs(artifact.get("history") or {})
            if row["verdict"] == "keep":
                for index, turn in enumerate(artifact.get("turns") or []):
                    user, assistant = pairs[index] if index < len(pairs) else ({}, {})
                    keep.write(json.dumps({
                        "flow_id": artifact["flow_id"],
                        "domain": artifact["domain"],
                        "session_id": artifact["session_id"],
                        "turn_index": index + 1,
                        "case_id": turn.get("case_id"),
                        "messages": [
                            {"role": "user", "content": user.get("content") or turn.get("prompt", "")},
                            {"role": "assistant", "content": assistant.get("content") or turn.get("response", "")},
                        ],
                        "thinking_preserved": bool(_safe_metadata(assistant).get("thinking")),
                        "tool_events_preserved": bool(_safe_metadata(assistant).get("tool_events")),
                    }, ensure_ascii=False) + "\n")
            else:
                queue = infrastructure if row["verdict"] == "infrastructure" else repair
                queue.write(json.dumps({
                    "flow_id": artifact["flow_id"],
                    "domain": artifact["domain"],
                    "session_id": artifact["session_id"],
                    "turns": artifact.get("turns") or [],
                    "shape_reasons": artifact.get("shape_reasons") or [],
                    "artifact": row["artifact"],
                    "deleted": row["deleted"],
                }, ensure_ascii=False) + "\n")
    summary["artifacts"] = {
        "summary": str(summary_path),
        "keep": str(keep_path),
        "repair": str(repair_path),
        "infrastructure": str(infrastructure_path),
    }
    _write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["totals"]["repair"] == 0 and summary["totals"]["infrastructure"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
