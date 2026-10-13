#!/usr/bin/env python3
"""Run isolated, curation-aware Odysseus audits across non-email domains.

The runner deliberately keeps setup, execution, scoring, review, and deletion
separate. A failed session is serialized before deletion so a bad trace can be
diagnosed without contaminating the SFT set.
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
from typing import Any, Iterable

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOMAINS = ("skills", "tasks", "theme", "memory", "documents", "cookbook")


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    tools: tuple[str, ...]
    action: str = ""
    mutation: bool = False
    dry_run: bool = False


def _cases(domain: str, rows: Iterable[tuple[str, tuple[str, ...], str, bool, bool]]) -> list[Case]:
    cases = [Case(f"{domain}_{i:02d}_{name}", prompt, tools, action, mutation, dry_run)
             for i, (name, tools, prompt, action, mutation, dry_run) in enumerate(rows, 1)]
    if len(cases) != 20:
        raise AssertionError(f"{domain} requires exactly 20 cases, got {len(cases)}")
    return cases


def prompt_matrix() -> dict[str, list[Case]]:
    """Return the stable 20-case matrix for every requested audit domain."""
    def read_rows(prefix: str, tool: str, prompts: list[str], action: str = "list"):
        return [(f"{prefix}{i:02d}", (tool,), p, action, False, False) for i, p in enumerate(prompts, 1)]

    skills = [
        "List my skills", "Search my skills for calendar workflows", "View the email skill",
        "Show the verification section of the email skill", "List published skills", "List draft skills",
        "Search skills for document editing", "View the cookbook skill", "Find skills tagged search",
        "Add a draft skill named audit-fixture-{marker}", "View audit-fixture-{marker}",
        "Patch audit-fixture-{marker} to add a verification step", "Edit audit-fixture-{marker} with a short procedure",
        "Publish audit-fixture-{marker}", "List skills after the fixture change", "Search for audit-fixture-{marker}",
        "View a reference file for audit-fixture-{marker}", "Delete audit-fixture-{marker}",
        "List skills and report their categories", "Search skills for safe dry runs",
    ]
    tasks = [
        "List my scheduled tasks", "Find tasks about weekly review", "Create a task named audit-fixture-{marker} to review notes daily",
        "List my tasks after creating the fixture", "Pause the task audit-fixture-{marker}", "Resume the task audit-fixture-{marker}",
        "Edit audit-fixture-{marker} so it runs at 10:00", "Show the task audit-fixture-{marker}",
        "Run the task audit-fixture-{marker} once", "List active tasks", "List paused tasks", "Search tasks for audit-fixture-{marker}",
        "Create a recurring weekly background task audit-weekly-{marker} to check calendar", "Edit audit-weekly-{marker} to check email too",
        "Pause audit-weekly-{marker}", "Resume audit-weekly-{marker}", "List tasks with their next run", "Delete audit-weekly-{marker}",
        "Delete audit-fixture-{marker}", "List tasks after cleanup",
    ]
    theme = [
        "Open theme settings", "Set my theme to dark", "Set my theme to light", "Set my theme to terminal",
        "Set my theme to forest", "Set my theme to ocean", "Set my theme to paper", "Set my theme to midnight",
        "Set my theme to copper", "Set my theme to cyberpunk", "Set my theme to retrowave", "Set my theme to ume",
        "Set my theme to gpt", "Set my theme to claude", "Set my theme to lavender", "Set my theme to organs",
        "Set my theme to cute", "Create a custom theme called audit-{marker}", "Open settings after changing the theme",
        "Tell me which theme is active",
    ]
    memory = [
        "List my saved memories", "Search my memories for timezone", "Search memories for audit fixture", "Add memory: audit marker {marker}",
        "List memories after adding the audit marker", "Show the memory about audit marker {marker}", "Edit the audit marker memory to say verified",
        "Search memories for verified", "Add a preference memory for concise audit reports", "List preference memories",
        "Search memories for concise", "Show my latest memory", "Add a fact memory named audit fact {marker}",
        "Edit audit fact {marker} to include deterministic checks", "Search memories for deterministic", "List memories newest first",
        "Delete the audit fact {marker}", "Delete the audit marker memory {marker}", "Search memories after cleanup", "List my memories after cleanup",
    ]
    documents = [
        "List my documents", "Find the document named audit fixture {marker}", "Read audit fixture {marker}",
        "Open the document titled audit fixture {marker} in the editor", "Summarize audit fixture {marker}", "Search documents for deterministic checks",
        "Edit audit fixture {marker} and append a verification line", "Rename audit fixture {marker} to audit renamed {marker}",
        "Read the updated audit fixture {marker}", "List markdown documents", "Find documents containing audit marker {marker}",
        "Open the first audit fixture document", "Append a second line to audit fixture {marker}", "Show the current document content",
        "Suggest an edit to audit fixture {marker}", "Update audit fixture {marker} with a clean summary",
        "Read audit fixture {marker} from the beginning", "List documents after the fixture edit", "Delete audit fixture {marker}",
        "List documents after cleanup",
    ]
    cookbook = [
        "Open the Cookbook panel", "List Cookbook servers", "List served models", "List model downloads", "List cached models",
        "List saved serve presets", "Search official Hugging Face models for Qwen", "Search official Hugging Face models for a small text model",
        "Find a GGUF model without downloading it", "Show Cookbook state", "Check whether any model server is running",
        "List Cookbook servers and their default", "List cached models on the local server", "Show saved launch presets",
        "Search official models for an embedding model", "Find a quantized model but do not launch it", "Report active downloads",
        "Open the Cookbook and show its current state", "Dry-run a search for official DeepSeek models", "Tell me whether Cookbook has a running server",
    ]
    skills_rows = read_rows("case", "manage_skills", skills[:9]) + [
        ("add", ("manage_skills",), skills[9], "add", True, False),
        ("view", ("manage_skills",), skills[10], "view", False, False),
        ("patch", ("manage_skills",), skills[11], "patch", True, False),
        ("edit", ("manage_skills",), skills[12], "edit", True, False),
        ("publish", ("manage_skills",), skills[13], "publish", True, False),
        ("list_after", ("manage_skills",), skills[14], "list", False, False),
        ("search_fixture", ("manage_skills",), skills[15], "search", False, False),
        ("view_ref", ("manage_skills",), skills[16], "view_ref", False, False),
        ("delete", ("manage_skills",), skills[17], "delete", True, False),
        ("list_categories", ("manage_skills",), skills[18], "list", False, False),
        ("search_safe", ("manage_skills",), skills[19], "search", False, False),
    ]
    cookbook_tools = [("ui_control",), ("list_cookbook_servers",), ("list_served_models",),
                      ("list_downloads",), ("list_cached_models",), ("list_serve_presets",),
                      ("search_hf_models",), ("search_hf_models",), ("search_hf_models",),
                      ("app_api",), ("list_served_models",), ("list_cookbook_servers",),
                      ("list_cached_models",), ("list_serve_presets",), ("search_hf_models",),
                      ("search_hf_models",), ("list_downloads",), ("ui_control",),
                      ("search_hf_models",), ("list_served_models",)]
    cookbook_rows = [(f"t{i:02d}", tool, prompt, "", False, True)
                     for i, (prompt, tool) in enumerate(zip(cookbook, cookbook_tools), 1)]
    return {
        "skills": _cases("skills", skills_rows),
        "tasks": _cases("tasks", [
            (f"t{i:02d}", ("manage_tasks",), p, "list" if i in (1,2,4,10,11,12,17,20) else "", i in (3,5,6,7,9,13,14,15,16,18,19), False)
            for i, p in enumerate(tasks, 1)
        ]),
        "theme": _cases("theme", [
            (f"t{i:02d}", ("ui_control",), p, "open_panel" if i == 1 or i == 19 else ("set_theme" if 2 <= i <= 17 else ("create_theme" if i == 18 else "")), i in range(2, 19), False)
            for i, p in enumerate(theme, 1)
        ]),
        "memory": _cases("memory", [
            (f"t{i:02d}", ("manage_memory",), p, "list" if i in (1,5,10,12,16,19,20) else ("search" if i in (2,3,8,11,15,18) else ("add" if i in (4,9,13) else ("edit" if i in (7,14) else "delete"))), i in (4,7,9,13,14,17), False)
            for i, p in enumerate(memory, 1)
        ]),
        "documents": _cases("documents", [
            (f"t{i:02d}", ("manage_documents",) if i not in (4,7,8,13,16) else (("edit_document", "manage_documents") if i in (7,8,13,16) else ("ui_control", "manage_documents")), p, "list" if i in (1,2,6,10,11,18,20) else ("read" if i in (3,5,9,12,14,17) else ("edit" if i in (7,8,13,16) else "open")), i in (7,8,13,16,19), False)
            for i, p in enumerate(documents, 1)
        ]),
        "cookbook": _cases("cookbook", cookbook_rows),
    }


def _sse_events(response: httpx.Response):
    data: list[str] = []
    event_name = ""
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            data.append(line.partition(":")[2].lstrip())
        elif not line.strip() and data:
            raw = "\n".join(data)
            data = []
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = {"type": event_name or "raw", "content": raw}
            if isinstance(obj, dict) and event_name and "type" not in obj:
                obj["type"] = event_name
            yield obj
            event_name = ""


def _event_text(events: list[dict[str, Any]]) -> str:
    text = []
    for event in events:
        if isinstance(event.get("delta"), str):
            text.append(event["delta"])
        elif event.get("type") == "final_response" and isinstance(event.get("content"), str):
            text = [event["content"]]
    return "".join(text).strip()


def _tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [e for e in events if e.get("type") in {"tool_start", "tool_output"}]
    for metric in (e.get("data") for e in events if e.get("type") == "metrics"):
        if isinstance(metric, dict):
            out.extend(e for e in metric.get("tool_events", []) if isinstance(e, dict))
    return out


def score_case(case: Case, events: list[dict[str, Any]], response: str) -> dict[str, Any]:
    tools = _tool_events(events)
    starts = [e for e in tools if e.get("type") == "tool_start"]
    invocations = starts or [e for e in tools if e.get("type") == "tool_output"]
    names = [str(e.get("tool") or "") for e in invocations if e.get("tool")]
    first = names[0] if names else None
    expected = set(case.tools)
    tool_ok = any(name in expected or name.removeprefix("mcp__").split("__")[-1] in expected for name in names)
    if case.dry_run:
        tool_ok = tool_ok and not any(n in {"download_model", "serve_model", "stop_served_model", "adopt_model_server"} for n in names)
    errors = [e for e in events if e.get("type") == "error"] + [e for e in tools if str(e.get("output") or "").lstrip().lower().startswith("error")]
    duplicate = len(names) != len(set((str(e.get("tool") or ""), str(e.get("command") or "")) for e in invocations))
    malformed = bool(re.search(r"<tool_call|<function=|mcp__\w+__\w+\s*\(|\{\s*\\?\"function", response, re.I))
    unavailable = bool(re.search(r"(don't|do not|cannot|can't) have (a |the )?(manage_\w+|ui_control|cookbook|memory|document|task|skill) tool", response, re.I))
    return {
        "case_id": case.id,
        "prompt": case.prompt,
        "expected_tools": list(case.tools),
        "first_tool": first,
        "observed_tools": names,
        "tool_ok": tool_ok,
        "errors": errors[:5],
        "response": response[:4000],
        "response_ok": bool(response) and not malformed and not unavailable,
        "duplicate_tool_call": duplicate,
        "dry_run_ok": not any(n in {"download_model", "serve_model", "stop_served_model", "delete_model"} for n in names) if case.dry_run else True,
        "pass": tool_ok and not errors and bool(response) and not malformed and not unavailable and not duplicate and (not case.dry_run or not any(n in {"download_model", "serve_model", "stop_served_model", "delete_model"} for n in names)),
    }


def _session_payload(client: httpx.Client, base_url: str, sid: str) -> dict[str, Any]:
    response = client.get(f"{base_url.rstrip('/')}/api/history/{sid}", timeout=30)
    response.raise_for_status()
    return response.json()


def _durable_tool_events(history: dict[str, Any]) -> list[dict[str, Any]]:
    """Return tool events persisted with the latest assistant response.

    The streaming endpoint intentionally keeps tool metadata out of the
    metrics event.  The history endpoint is the durable source of truth and
    is also what SFT export consumes, so score from it rather than guessing
    from the visible stream.
    """
    rows = history.get("history") if isinstance(history, dict) else None
    if not isinstance(rows, list):
        return []
    for message in reversed(rows):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        metadata = message.get("metadata")
        if isinstance(metadata, str):
            with contextlib.suppress(json.JSONDecodeError):
                metadata = json.loads(metadata)
        if isinstance(metadata, dict) and isinstance(metadata.get("tool_events"), list):
            return [event for event in metadata["tool_events"] if isinstance(event, dict)]
        return []
    return []


def _history_pairs(history: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair each user turn with the assistant response that followed it."""
    rows = history.get("history") if isinstance(history, dict) else None
    if not isinstance(rows, list):
        return []
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    pending: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("role") == "user":
            pending = row
        elif row.get("role") == "assistant" and pending is not None:
            pairs.append((pending, row))
            pending = None
    return pairs


def _create_session(client: httpx.Client, args: argparse.Namespace, name: str) -> str:
    fields = {
        "name": f"[domain-audit] {name}", "endpoint_url": args.endpoint_url,
        "endpoint_id": args.endpoint_id, "model": args.model,
        "skip_validation": "true", "rag": "false",
    }
    workspace = str(getattr(args, "workspace", "") or "").strip()
    if workspace:
        fields["cwd"] = workspace
    response = client.post(f"{args.base_url.rstrip('/')}/api/session", data=fields, timeout=30)
    response.raise_for_status()
    return str(response.json()["id"])


def _run_turn(client: httpx.Client, args: argparse.Namespace, sid: str, prompt: str) -> list[dict[str, Any]]:
    fields = {
        "message": prompt, "session": sid, "mode": "agent",
        "agent_prompt_mode": "auto", "selected_endpoint_id": args.endpoint_id,
        "selected_endpoint_url": args.endpoint_url, "selected_model": args.model,
    }
    runtime_context = getattr(args, "client_runtime_context", None)
    workspace = str(getattr(args, "workspace", "") or "").strip()
    if runtime_context:
        fields["client_runtime_context"] = json.dumps(
            runtime_context,
            separators=(",", ":"),
            sort_keys=True,
        )
    if workspace:
        fields["cwd"] = workspace
        fields["workspace"] = workspace
    events: list[dict[str, Any]] = []
    with client.stream("POST", f"{args.base_url.rstrip('/')}/api/chat_stream", data=fields,
                       headers={"Accept": "text/event-stream"}, timeout=args.timeout) as response:
        response.raise_for_status()
        events.extend(_sse_events(response))
    return events


def _render_prompt(prompt: str, marker: str) -> str:
    return prompt.replace("{marker}", marker)


def _seed_fixtures(owner: str, marker: str, domain: str, session_id: str | None = None) -> None:
    """Create only marker-scoped records used by the audit prompts."""
    import uuid
    from datetime import datetime
    from core.database import Document, DocumentVersion, ScheduledTask, SessionLocal

    db = SessionLocal()
    try:
        if domain == "documents":
            title = f"audit fixture {marker}"
            doc_id = str(uuid.uuid4())
            content = f"Audit fixture {marker}.\nDeterministic checks are pending."
            db.add(Document(id=doc_id, session_id=session_id, title=title, language="markdown",
                            current_content=content, version_count=1, is_active=True,
                            archived=False, owner=owner))
            db.add(DocumentVersion(id=str(uuid.uuid4()), document_id=doc_id, version_number=1,
                                   content=content, summary="domain audit fixture", source="domain-audit"))
        elif domain == "tasks":
            db.add(ScheduledTask(id=str(uuid.uuid4()), owner=owner, name=f"audit fixture {marker}",
                                 prompt=f"Audit fixture {marker}", task_type="llm", schedule="daily",
                                 scheduled_time="09:00", trigger_type="schedule", next_run=datetime(2026, 8, 29, 9),
                                 status="active", output_target="session"))
        db.commit()
    finally:
        db.close()
    if domain == "memory":
        from services.memory.memory import MemoryManager
        manager = MemoryManager(str(ROOT / "data"))
        entries = manager.load_all()
        if not any(str(e.get("text")) == f"audit marker {marker}" for e in entries if isinstance(e, dict)):
            entries.append(manager.add_entry(f"audit marker {marker}", source="domain-audit", category="fact", owner=owner))
            manager.save(entries)
    if domain == "skills":
        from services.memory.skills import SkillsManager
        manager = SkillsManager(ROOT / "data")
        if not manager.read_skill_md(f"audit-fixture-{marker}", owner=owner):
            manager.add_skill(name=f"audit-fixture-{marker}", description="domain audit fixture",
                              when_to_use="Only during the domain audit", procedure=["Run the fixture check"],
                              pitfalls=[], verification=["The check passes"], tags=["audit"],
                              category="general", status="draft", owner=owner)


def _cleanup_fixtures(owner: str, marker: str, domain: str) -> None:
    from core.database import Document, DocumentVersion, ScheduledTask, SessionLocal
    db = SessionLocal()
    try:
        if domain == "documents":
            docs = db.query(Document).filter(Document.title.like(f"%{marker}%")).all()
            for doc in docs:
                db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).delete()
                db.delete(doc)
        elif domain == "tasks":
            db.query(ScheduledTask).filter(ScheduledTask.name.like(f"%{marker}%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    if domain == "memory":
        from services.memory.memory import MemoryManager
        manager = MemoryManager(str(ROOT / "data"))
        manager.save([e for e in manager.load_all() if not (isinstance(e, dict) and marker in str(e.get("text", "")))])
    if domain == "skills":
        from services.memory.skills import SkillsManager
        SkillsManager(ROOT / "data").delete_skill(f"audit-fixture-{marker}", owner=owner)


def _review_session(payload: dict[str, Any], args: argparse.Namespace, session_id: str = "") -> dict[str, Any] | None:
    if not args.deepseek:
        return None
    try:
        from scripts.audit_email_sft_with_deepseek import call_judge, deepseek_endpoint
        import sqlite3
        con = sqlite3.connect(ROOT / "data" / "app.db")
        con.row_factory = sqlite3.Row
        endpoint = deepseek_endpoint(con, endpoint_id=args.deepseek_endpoint_id, model=args.deepseek_model)
        reviewed = call_judge(endpoint, [{
            "session": {"id": session_id, "name": payload.get("name")},
            "messages": payload.get("history", []),
            "domain": "non-email",
        }])
        return (reviewed.get("results") or [None])[0]
    except Exception as exc:
        # A judge outage is not evidence that the trace is bad. Preserve the
        # error in the artifact while leaving the deterministic verdict in
        # control so curation remains reproducible.
        return {"verdict": None, "unavailable": True, "error": repr(exc)}


def _snapshot_theme_preferences() -> bytes | None:
    path = ROOT / "data" / "user_prefs.json"
    try:
        return path.read_bytes() if path.exists() else None
    except OSError:
        return None


def _restore_theme_preferences(snapshot: bytes | None) -> None:
    if snapshot is None:
        return
    path = ROOT / "data" / "user_prefs.json"
    tmp = path.with_suffix(path.suffix + ".domain-audit.tmp")
    tmp.write_bytes(snapshot)
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:7011")
    parser.add_argument("--cookie", default=os.environ.get("ODY_COOKIE", ""))
    parser.add_argument("--endpoint-url", default="")
    parser.add_argument("--endpoint-id", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--owner", default="sft_alex_creator")
    parser.add_argument("--domains", default=",".join(DOMAINS))
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tmp" / "domain-audit")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--delete-bad", action="store_true")
    parser.add_argument("--deepseek", action="store_true")
    parser.add_argument("--deepseek-endpoint-id")
    parser.add_argument("--deepseek-model")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    unknown = sorted(set(domains) - set(DOMAINS))
    if unknown:
        parser.error(f"unknown domains: {', '.join(unknown)}")
    if not args.cookie:
        parser.error("--cookie or ODY_COOKIE is required for live audits")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    marker = f"{stamp}-{uuid.uuid4().hex[:8]}"
    matrix = prompt_matrix()
    theme_snapshot = _snapshot_theme_preferences()
    all_rows: list[dict[str, Any]] = []
    with httpx.Client(cookies={"odysseus_session": args.cookie}, follow_redirects=True) as client:
        for domain in domains:
            cases = matrix[domain][:args.limit]
            for case in cases:
                # Keep each case in its own session. A single bad turn must
                # never quarantine otherwise valid SFT turns from the same
                # domain, and deletion can then be exact and auditable.
                case_marker = f"{marker}-{case.id}"
                session_id = _create_session(client, args, f"{domain}-{case.id}-{marker}")
                _seed_fixtures(args.owner, case_marker, domain, session_id)
                prompt = _render_prompt(case.prompt, case_marker)
                try:
                    events = _run_turn(client, args, session_id, prompt)
                    durable = _session_payload(client, args.base_url, session_id)
                    if durable_tools := _durable_tool_events(durable):
                        events = events + [{"type": "metrics", "data": {"tool_events": durable_tools}}]
                    result = score_case(case, events, _event_text(events))
                    result["events"] = events
                except Exception as exc:
                    result = {"case_id": case.id, "prompt": prompt, "pass": False, "errors": [repr(exc)], "events": []}
                print(f"{domain}: {case.id} {'PASS' if result.get('pass') else 'FAIL'}", flush=True)
                try:
                    history = _session_payload(client, args.base_url, session_id)
                except Exception as exc:
                    history = {"history_error": repr(exc)}
                deterministic_pass = bool(result.get("pass"))
                payload = {"domain": domain, "marker": case_marker, "session_id": session_id,
                           "owner": args.owner, "turns": [result], "history": history,
                           "deterministic_pass": deterministic_pass}
                review = _review_session(history, args, session_id)
                payload["model_review"] = review
                verdict = "keep" if deterministic_pass else "repair"
                if review and review.get("verdict") in {"repair", "delete"}:
                    verdict = review["verdict"]
                payload["verdict"] = verdict
                path = args.out_dir / f"{domain}_{case.id}_{session_id}.json"
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                deleted = False
                if args.delete_bad and verdict != "keep":
                    response = client.delete(f"{args.base_url.rstrip('/')}/api/session/{session_id}", timeout=30)
                    deleted = response.is_success
                    payload["deleted"] = deleted
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                all_rows.append({"domain": domain, "case_id": case.id, "session_id": session_id,
                                 "verdict": verdict, "turns": 1, "passed": int(deterministic_pass),
                                 "artifact": str(path), "deleted": deleted})
                _cleanup_fixtures(args.owner, case_marker, domain)
        _restore_theme_preferences(theme_snapshot)
    summary = {"marker": marker, "domains": all_rows, "matrix_size": {d: len(matrix[d]) for d in domains}}
    summary_path = args.out_dir / f"summary_{stamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    keep_path = args.out_dir / f"sft_keep_{stamp}.jsonl"
    repair_path = args.out_dir / f"repair_queue_{stamp}.jsonl"
    delete_path = args.out_dir / f"delete_queue_{stamp}.jsonl"
    with keep_path.open("w", encoding="utf-8") as keep, repair_path.open("w", encoding="utf-8") as repair, delete_path.open("w", encoding="utf-8") as delete:
        for row in all_rows:
            artifact = json.loads(Path(row["artifact"]).read_text(encoding="utf-8"))
            pairs = _history_pairs(artifact.get("history") or {})
            for index, turn in enumerate(artifact.get("turns") or []):
                if not turn.get("pass"):
                    continue
                user, assistant = pairs[index] if index < len(pairs) else ({}, {})
                assistant_meta = assistant.get("metadata") if isinstance(assistant, dict) else {}
                keep.write(json.dumps({
                    "domain": artifact["domain"],
                    "session_id": artifact["session_id"],
                    "case_id": turn.get("case_id"),
                    "messages": [
                        {"role": "user", "content": user.get("content") or turn.get("prompt", "")},
                        {"role": "assistant", "content": assistant.get("content") or turn.get("response", "")},
                    ],
                    "turn": turn,
                    "thinking_preserved": bool(isinstance(assistant_meta, dict) and assistant_meta.get("thinking")),
                }, ensure_ascii=False) + "\n")
            if row["verdict"] != "keep":
                target = delete if row["verdict"] == "delete" else repair
                target.write(json.dumps({"domain": artifact["domain"], "session_id": artifact["session_id"],
                                         "verdict": artifact["verdict"], "turns": artifact["turns"],
                                         "artifact": row["artifact"]}, ensure_ascii=False) + "\n")
    summary["artifacts"] = {"keep": str(keep_path), "repair": str(repair_path), "delete": str(delete_path)}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(row["verdict"] == "keep" for row in all_rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
