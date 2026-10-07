#!/usr/bin/env python3
"""Audit the remaining Odysseus tools with isolated, resumable sessions.

This uses the same curation contract as ``odysseus_domain_audit.py`` but
creates one session per tool. Prompts prefer read-only behavior, but mutating
email prompts target synthetic SFT fixture accounts only so they can produce
real reviewable action traces.
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
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.odysseus_domain_audit import (  # noqa: E402
    Case,
    _create_session,
    _durable_tool_events,
    _event_text,
    _history_pairs,
    _render_prompt,
    _run_turn,
    _session_payload,
    score_case,
)
from scripts.odysseus_related_flow_audit import (  # noqa: E402
    _flow_has_good_training_shape,
    _latest_assistant_text,
    _login_cookie,
    _safe_metadata,
)


# These are intentionally excluded from this job because they already have
# dedicated 20-case coverage in the domain audit or the earlier email/search
# runs. Aliases are omitted; each canonical runtime tool is tested once.
REMAINING_TOOLS = (
    "bash", "python", "read_file", "write_file", "edit_file", "apply_patch",
    "grep", "glob", "ls", "get_workspace", "host_shell", "manage_bg_jobs",
    "manage_contact", "resolve_contact", "manage_session", "list_sessions",
    "search_chats", "web_fetch", "private_browser", "youtube_tool",
    "ask_user", "update_plan",
    "trigger_research", "manage_research", "chat_with_model", "ask_teacher",
    "pipeline", "list_models", "create_session", "send_to_session",
    "download_model", "serve_model", "serve_preset", "adopt_served_model",
    "stop_served_model", "tail_serve_output", "list_served_models",
    "list_downloads", "list_cached_models", "list_cookbook_servers",
    "list_serve_presets", "cancel_download",
    "manage_endpoints", "manage_mcp", "api_call", "app_api", "manage_settings",
    "manage_webhooks", "manage_tokens", "download_attachment", "scan_spam",
    "block_sender", "manage_email_state", "scan_email_unsubscribes",
    "unsubscribe_email", "draft_email", "draft_email_reply", "ai_draft_email_reply",
    "bulk_email",
)

# These are intentionally unavailable to ``sft_*`` owners under the current
# workspace-isolation policy. They are still listed in REMAINING_TOOLS so the
# matrix documents the full catalog, but are audited separately as policy
# checks rather than spending 20 live turns on guaranteed unavailable tools.
SFT_POLICY_DISABLED_TOOLS = frozenset({
    "python", "read_file", "write_file", "edit_file", "apply_patch",
    "grep", "glob", "ls", "get_workspace", "host_shell", "manage_bg_jobs",
})


def _tool_prompts(tool: str, marker: str) -> list[str]:
    """Return exactly 20 prompts for a canonical tool.

    The prompts exercise discovery, repeated use, and follow-up wording.
    """
    safe = {
        "bash": ["Run a read-only shell check for audit marker {marker}", "Show the current working directory without changing files"],
        "python": ["Compute 2 + 2 in Python", "Parse this audit marker as plain text: {marker}"],
        "read_file": ["Read the audit fixture file if it exists: /tmp/audit-{marker}.txt", "Read one harmless system metadata file such as /etc/hostname"],
        "write_file": ["Preview creating a fixture file named /tmp/audit-{marker}.txt; do not write it", "Explain the exact write_file arguments for a fixture file without executing"],
        "edit_file": ["Preview changing /tmp/audit-{marker}.txt; do not edit files", "Explain the exact edit_file arguments for a fixture replacement without executing"],
        "apply_patch": ["Preview a patch to a fixture file; do not apply it", "Explain how to apply a no-op patch to an audit fixture without executing"],
        "grep": ["Search the workspace for the audit marker {marker}", "Search source files for the word fixture"],
        "glob": ["Find markdown files in the workspace", "Find files matching **/*audit*"],
        "ls": ["List the workspace directory", "List the temporary audit directory"],
        "get_workspace": ["What is the active workspace folder?", "Show the workspace path used by this chat"],
        "host_shell": ["Preview a read-only host check for the audit marker; do not change anything", "Show the host operating system name without modifying it"],
        "manage_bg_jobs": ["List my background jobs", "Show whether any background jobs are running"],
        "manage_contact": ["Search my address book contacts for Priya Shah", "List my address-book contacts"],
        "resolve_contact": ["Find the email address for Casey Morgan", "Resolve Priya Shah in my contacts"],
        "manage_session": [
            "Rename this current audit chat to manage-session-audit-{marker}",
            "Archive this current audit chat",
            "Unarchive this current audit chat",
        ],
        "list_sessions": ["List my chats", "Show recent chat sessions"],
        "search_chats": ["Search past chats for audit marker {marker}", "Find previous chats mentioning calendar tools"],
        "web_fetch": ["Read the text of https://example.com", "Fetch https://www.rfc-editor.org/rfc/rfc9110"],
        "private_browser": ["Open https://example.com in the private browser and inspect its title", "Open https://www.w3.org and report the visible heading"],
        "youtube_tool": ["Find the metadata for YouTube video https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Read the latest available metadata for that YouTube video"],
        "ask_user": [
            "Ask me which day next month my dinner reservation should be saved for; do not guess the date",
            "Ask me to choose whether to keep, archive, or delete a suspicious email; do not take action",
        ],
        "update_plan": [
            "Make a short plan for testing Odysseus SFT rows and write it to the plan panel",
            "Update the plan panel with inspect marked done and patch still pending",
        ],
        "trigger_research": ["Start a small research job about the history of SearXNG", "Research the difference between PDF and HTML extraction"],
        "manage_research": ["List my saved research reports", "Search saved research for SearXNG"],
        "chat_with_model": ["Ask another model for a one-sentence definition of SFT", "Compare another model's answer about tool calling"],
        "ask_teacher": ["Ask the teacher how to validate a tool trace", "Ask the teacher for one concise SFT quality check"],
        "pipeline": ["Describe a two-step analysis pipeline without running it", "Preview a pipeline that summarizes then checks a result"],
        "list_models": ["List available models", "Show the configured model endpoints"],
        "create_session": ["Preview creating a chat named audit-{marker}; do not create it", "Explain the arguments for a new chat without creating one"],
        "send_to_session": ["Preview sending a message to another chat; do not send it", "Explain how cross-chat messaging works without sending"],
        "download_model": ["Preview a download of Qwen/Qwen3-0.6B; do not start it", "Explain which server would receive a model download without starting one"],
        "serve_model": ["Preview serving a tiny local model; do not launch a server", "Explain the safe arguments for a model server dry run without launching it"],
        "serve_preset": ["Preview launching a saved serve preset; do not launch it", "List what a serve preset would do without starting it"],
        "adopt_served_model": ["Preview adopting an existing model server; do not change tracking", "Explain how an existing server would be adopted without registering it"],
        "stop_served_model": ["Preview stopping a model server; do not stop anything", "Explain how to identify a model server before stopping it"],
        "tail_serve_output": ["List model servers before reading any logs", "Explain how to inspect serve output without changing a server"],
        "list_served_models": ["List currently running Cookbook model servers", "Show what is serving in Cookbook right now"],
        "list_downloads": ["List active Cookbook downloads", "Show current model download progress"],
        "list_cached_models": ["List cached models on disk", "Show downloaded models already available locally"],
        "list_cookbook_servers": ["List configured Cookbook servers", "Show the current default Cookbook server"],
        "list_serve_presets": ["List saved Cookbook serve presets", "Show available serve presets without launching one"],
        "cancel_download": ["List downloads before considering cancellation; do not cancel anything", "Explain how to cancel a download without executing cancellation"],
        "manage_endpoints": ["List configured API endpoints", "Show enabled endpoints without changing them"],
        "manage_mcp": ["List configured MCP servers", "Show available MCP tools without changing configuration"],
        "api_call": ["Preview a harmless GET integration request without sending it", "Explain how to inspect a configured integration safely"],
        "app_api": ["List allowed internal API endpoints for cookbook state", "Preview reading a harmless internal status endpoint"],
        "manage_settings": ["Show available settings without changing them", "Read the current search setting without modifying it"],
        "manage_webhooks": ["List configured webhooks", "Show webhook status without changing anything"],
        "manage_tokens": ["List API tokens without creating or deleting one", "Explain token management without changing tokens"],
        "download_attachment": ["Open attachment 0 from email UID 112 and summarize it", "Read the creator payout sample attachment from email UID 112"],
        "scan_spam": ["Scan my inbox for likely spam without deleting or blocking anything", "Run a spam scan on recent inbox messages without taking action"],
        "block_sender": ["Block sender alerts@secure-rowan-login.co but do not delete existing messages", "Block sender notice@creator-awards.example.net and leave existing messages alone"],
        "manage_email_state": ["List blocked senders and reversible email state without changing it", "Show my blocked email senders without changing anything"],
        "scan_email_unsubscribes": ["Scan recent email headers for unsubscribe candidates", "Find newsletter unsubscribe candidates in my inbox"],
        "unsubscribe_email": ["Unsubscribe from email UID 162 using method 0", "Use unsubscribe method 0 for email UID 163", "Unsubscribe from UID 162 using method 0"],
        "draft_email": ["Create a reviewable draft email to Casey Morgan saying hello", "Draft an email to Priya Shah saying I will review the agenda", "Create a reviewable email draft to Marco Wells saying I saw the playbook"],
        "draft_email_reply": ["Create a reply draft for email UID 10 saying thanks for the next steps", "Draft a reply to UID 104 saying I received the invoice backup", "Create a reply draft to email UID 123 saying I saw the playbook"],
        "ai_draft_email_reply": ["Create an AI reply draft for email UID 10", "Use AI Reply to draft a response to email UID 104", "Create an AI reply draft for email UID 123"],
        "bulk_email": ["Mark emails UID 162 and UID 163 as read", "Mark UIDs 162 and 163 unread in one bulk action"],
    }
    variants = safe[tool]
    prompts = []
    fixture_mutating = {
        "unsubscribe_email",
        "draft_email",
        "draft_email_reply",
        "ai_draft_email_reply",
        "bulk_email",
        "block_sender",
    }
    for index in range(20):
        base = variants[index % len(variants)]
        if tool in fixture_mutating:
            qualifier = " Use the synthetic SFT fixture only and report the result."
        else:
            qualifier = (" Use the tool directly and report the result." if index % 2 == 0
                          else " Keep this read-only and concise.")
        prompts.append(base + qualifier)
    return prompts


EXPECTED_TOOL_ALIASES = {
    "draft_email_reply": ("draft_email_reply", "ui_control"),
    "ai_draft_email_reply": ("ai_draft_email_reply", "draft_email_reply", "ui_control"),
}


def tool_matrix() -> dict[str, list[Case]]:
    matrix = {}
    for tool in REMAINING_TOOLS:
        prompts = _tool_prompts(tool, "{marker}")
        expected_tools = EXPECTED_TOOL_ALIASES.get(tool, (tool,))
        matrix[tool] = [Case(f"{tool}_{i:02d}", prompt, expected_tools, "", False,
                       tool in {"download_model", "serve_model", "serve_preset", "adopt_served_model", "stop_served_model", "cancel_download", "bulk_email"})
                        for i, prompt in enumerate(prompts, 1)]
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--cookie", default=os.environ.get("ODY_COOKIE", ""))
    parser.add_argument("--username", default="sft_alex_creator")
    parser.add_argument("--password", default=os.environ.get("ODYSSEUS_QA_PASSWORD"), required=os.environ.get("ODYSSEUS_QA_PASSWORD") is None)
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--endpoint-id", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--tools", default="all")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tmp" / "remaining-tool-audit")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--delete-bad", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--include-policy-disabled", action="store_true",
                        help="Also run tools hidden from sft_* owners (expected to fail policy checks)")
    parser.add_argument(
        "--workspace",
        default="",
        help="Workspace/cwd to bind for workspace/file/shell tool cases.",
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
    requested = list(REMAINING_TOOLS) if args.tools == "all" else [x.strip() for x in args.tools.split(",") if x.strip()]
    unknown = sorted(set(requested) - set(REMAINING_TOOLS))
    if unknown:
        parser.error(f"unknown tools: {', '.join(unknown)}")
    skipped_policy = []
    if not args.include_policy_disabled and str(args.owner).startswith("sft_"):
        skipped_policy = [tool for tool in requested if tool in SFT_POLICY_DISABLED_TOOLS]
        requested = [tool for tool in requested if tool not in SFT_POLICY_DISABLED_TOOLS]
    matrix = tool_matrix()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    marker = f"{time.strftime('%Y%m%d_%H%M%S')}-{uuid.uuid4().hex[:8]}"
    rows = []
    with httpx.Client(cookies={"odysseus_session": cookie}, follow_redirects=True) as client:
        for tool in requested:
            sid = _create_session(client, args, f"tool-{tool}-{marker}")
            turns = []
            path = args.out_dir / f"{tool}_{sid}.json"
            for case in matrix[tool][:args.limit]:
                prompt = _render_prompt(case.prompt, marker)
                try:
                    events = _run_turn(client, args, sid, prompt)
                    durable = _session_payload(client, args.base_url, sid)
                    tool_events = _durable_tool_events(durable)
                    if tool_events:
                        events += [{"type": "metrics", "data": {"tool_events": tool_events}}]
                    durable_response = _latest_assistant_text(durable) or _event_text(events)
                    result = score_case(case, events, durable_response)
                    result["events"] = events
                except Exception as exc:
                    result = {"case_id": case.id, "prompt": prompt, "pass": False, "errors": [repr(exc)], "events": []}
                turns.append(result)
                print(f"{tool}: {case.id} {'PASS' if result.get('pass') else 'FAIL'}", flush=True)
                partial_history = {}
                with contextlib.suppress(Exception):
                    partial_history = _session_payload(client, args.base_url, sid)
                partial_payload = {
                    "tool": tool,
                    "marker": marker,
                    "session_id": sid,
                    "owner": args.owner,
                    "turns": turns,
                    "history": partial_history,
                    "partial": True,
                }
                path.write_text(json.dumps(partial_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            history = _session_payload(client, args.base_url, sid)
            shape_ok, shape_reasons = _flow_has_good_training_shape(history, len(turns))
            passed = sum(bool(turn.get("pass")) for turn in turns)
            payload = {"tool": tool, "marker": marker, "session_id": sid, "owner": args.owner,
                       "turns": turns, "history": history, "passed": passed,
                       "shape_ok": shape_ok, "shape_reasons": shape_reasons,
                       "deterministic_pass": bool(turns) and passed == len(turns) and shape_ok,
                       "partial": False}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            verdict = "keep" if payload["deterministic_pass"] else "repair"
            payload["verdict"] = verdict
            if args.delete_bad and verdict != "keep":
                payload["deleted"] = client.delete(f"{args.base_url.rstrip('/')}/api/session/{sid}", timeout=30).is_success
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append({"tool": tool, "session_id": sid, "passed": passed, "turns": len(turns),
                         "shape_ok": shape_ok, "shape_reasons": shape_reasons,
                         "verdict": verdict, "artifact": str(path), "deleted": payload.get("deleted", False)})
    stamp = time.strftime("%Y%m%d_%H%M%S")
    summary = {"marker": marker, "tools": rows, "skipped_policy_tools": skipped_policy,
               "matrix_size": {tool: len(matrix[tool]) for tool in requested},
               "policy_matrix_size": {tool: len(matrix[tool]) for tool in skipped_policy}}
    (args.out_dir / f"summary_{stamp}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    keep = args.out_dir / f"sft_keep_{stamp}.jsonl"
    repair = args.out_dir / f"repair_queue_{stamp}.jsonl"
    with keep.open("w", encoding="utf-8") as keep_file, repair.open("w", encoding="utf-8") as repair_file:
        for row in rows:
            artifact = json.loads(Path(row["artifact"]).read_text(encoding="utf-8"))
            pairs = _history_pairs(artifact.get("history") or {})
            for index, turn in enumerate(artifact["turns"]):
                if not turn.get("pass"):
                    continue
                user, assistant = pairs[index] if index < len(pairs) else ({}, {})
                keep_file.write(json.dumps({"tool": artifact["tool"], "session_id": artifact["session_id"],
                                            "case_id": turn["case_id"], "messages":[
                                                {"role":"user", "content": user.get("content") or turn.get("prompt", "")},
                                                {"role":"assistant", "content": assistant.get("content") or turn.get("response", "")},
                                            ], "turn": turn,
                                            "thinking_preserved": bool(_safe_metadata(assistant).get("thinking")),
                                            "tool_events_preserved": bool(_safe_metadata(assistant).get("tool_events"))}, ensure_ascii=False) + "\n")
            if row["verdict"] != "keep":
                repair_file.write(json.dumps({"tool": artifact["tool"], "session_id": artifact["session_id"],
                                              "turns": artifact["turns"],
                                              "shape_reasons": artifact.get("shape_reasons") or [],
                                              "artifact": row["artifact"],
                                              "deleted": row.get("deleted", False)}, ensure_ascii=False) + "\n")
    print(json.dumps({"summary": str(args.out_dir / f"summary_{stamp}.json"), "keep": str(keep), "repair": str(repair)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
