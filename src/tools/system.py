"""System-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the skills/tasks tools plus the generic API bridges (api_call, app_api).
The admin manage_* tools (endpoints, mcp, webhooks, tokens, settings) live in
``src.agent_tools.admin_tools`` after the upstream registry migration (#3629);
``src.tool_implementations`` re-exports both sets for backward compatibility.
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from src.tools._common import _parse_tool_args

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skills management tool
# ---------------------------------------------------------------------------

async def do_manage_skills(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_skills tool calls.

    SKILL.md-backed CRUD with progressive disclosure (Hermes-style). Actions:

      list / index               — Level 0: name + description summary.
      view {name}                — Level 1: full SKILL.md.
      view_ref {name, path}      — Level 2: a sub-file under the skill dir.
      add  {name, description, when_to_use, procedure[], pitfalls[],
            verification[], tags[], category, status}
                                 — Create a new skill (draft by default).
      patch {name, old_string, new_string}
                                 — Token-efficient surgical edit on the
                                   raw SKILL.md text. Fails on ambiguous
                                   `old_string` (multiple matches).
      edit  {name, content}      — Replace the entire SKILL.md.
      publish {name}             — Flip status: draft -> published.
      delete {name}              — Remove the skill directory.
      search {query}             — Relevance match on published skills.
    """
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "").lower()
    from services.memory.skills import SkillsManager
    from services.memory.skill_format import Skill, slugify
    from src.constants import DATA_DIR
    sm = SkillsManager(DATA_DIR)

    # Accept legacy `skill_id` as an alias for `name`.
    name = (args.get("name") or args.get("skill_id") or "").strip()

    if action in ("list", "index", ""):
        all_skills = sm.load(owner=owner)
        if not all_skills:
            return {"results": "No skills yet. Create one with action='add'."}
        published = [s for s in all_skills if s.get("status") == "published"]
        drafts = [s for s in all_skills if s.get("status") == "draft"]
        lines = []
        if published:
            lines.append("## Published")
            for s in sorted(published, key=lambda x: x["name"]):
                lines.append(f"- **{s['name']}** ({s.get('category','general')}): {s.get('description','')}")
        if drafts:
            lines.append("\n## Drafts")
            for s in sorted(drafts, key=lambda x: x["name"]):
                lines.append(f"- **{s['name']}** [draft]: {s.get('description','')}")
        return {"results": "\n".join(lines) if lines else "No skills yet."}

    if action == "view":
        if not name:
            return {"error": "name is required for view", "exit_code": 1}
        md = sm.read_skill_md(name, owner=owner)
        if md is None:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        return {"results": md}

    if action == "view_ref":
        if not name:
            return {"error": "name is required for view_ref", "exit_code": 1}
        ref = (args.get("path") or "").strip()
        if not ref:
            return {"error": "path is required for view_ref", "exit_code": 1}
        text = sm.read_skill_reference(name, ref, owner=owner)
        if text is None:
            return {"error": f"Reference {ref!r} not found under {name!r}", "exit_code": 1}
        return {"results": text}

    if action == "add":
        if not name:
            return {
                "error": "name is required for add. Provide the exact slug the user should see, then report the returned name.",
                "exit_code": 1,
            }
        proc = args.get("procedure")
        if proc is None:
            proc = args.get("steps") or []
        if not proc and not args.get("body_extra") and not args.get("solution"):
            return {"error": "procedure (or solution body) is required", "exit_code": 1}
        # Same auto-publish gate as the extractor path — when the user
        # has auto_approve_skills on and the caller didn't pin an explicit
        # status, publish immediately. Audit later demotes/removes on fail.
        _status_arg = args.get("status")
        if not _status_arg:
            try:
                from routes.prefs_routes import _load_for_user as _load_prefs
                _prefs = _load_prefs(owner) or {}
                _status_arg = "published" if _prefs.get("auto_approve_skills", True) else "draft"
            except Exception:
                _status_arg = "draft"
        entry = sm.add_skill(
            name=args.get("name"),
            description=(args.get("description") or args.get("title") or "").strip(),
            category=args.get("category") or "general",
            tags=args.get("tags") or [],
            platforms=args.get("platforms") or [],
            requires_toolsets=args.get("requires_toolsets") or [],
            fallback_for_toolsets=args.get("fallback_for_toolsets") or [],
            when_to_use=(args.get("when_to_use") if args.get("when_to_use") is not None
                         else args.get("problem", "")),
            procedure=proc,
            pitfalls=args.get("pitfalls") or [],
            verification=args.get("verification") or [],
            status=_status_arg,
            version=args.get("version") or "1.0.0",
            confidence=args.get("confidence", 0.8),
            source=args.get("source", "learned"),
            teacher_model=args.get("teacher_model"),
            owner=owner,
            title=args.get("title", ""),
            problem=args.get("problem", ""),
            solution=args.get("solution", ""),
            steps=args.get("steps") or [],
        )
        if entry.get("_deduped"):
            return {"results": (
                f"A near-identical skill already exists: `{entry['name']}` — not creating "
                f"a duplicate. View or edit it with action='view', name='{entry['name']}'."
            )}
        try:
            from src.event_bus import fire_event
            fire_event("skill_added", owner)
        except Exception:
            logger.debug("skill_added event dispatch failed", exc_info=True)
        verify_hint = ""
        if entry.get("status") == "draft":
            verify_hint = (
                "\n\nThis skill is a DRAFT. Run through the procedure once to verify, "
                f"then publish with action='publish', name='{entry['name']}'."
            )
        return {"results": f"Created skill `{entry['name']}` — {entry.get('description','')}{verify_hint}"}

    if action == "edit":
        if not name:
            return {"error": "name is required for edit", "exit_code": 1}
        new_content = args.get("content")
        if not isinstance(new_content, str) or not new_content.strip():
            return {"error": "content (full SKILL.md) is required for edit", "exit_code": 1}
        try:
            sk_new = Skill.from_markdown(new_content)
        except Exception as e:
            return {"error": f"Could not parse content as SKILL.md: {e}", "exit_code": 1}
        sk_new.name = slugify(sk_new.name or name)
        existing = sm.load(owner=owner)
        match = next((s for s in existing if s.get("name") == name), None)
        if not match:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        if not sk_new.owner:
            sk_new.owner = match.get("owner") or owner
        ok = sm.update_skill(name, _skill_dump(sk_new), owner=owner)
        return {"results": f"Edited skill `{sk_new.name}`."} if ok else {"error": "Update failed", "exit_code": 1}

    if action == "patch":
        if not name:
            return {"error": "name is required for patch", "exit_code": 1}
        old = args.get("old_string")
        new_str = args.get("new_string", "")
        if not isinstance(old, str) or not old:
            return {"error": "old_string is required and must be non-empty", "exit_code": 1}
        md = sm.read_skill_md(name, owner=owner)
        if md is None:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        count = md.count(old)
        if count == 0:
            return {"error": "old_string not found in SKILL.md", "exit_code": 1}
        if count > 1:
            return {"error": f"old_string is ambiguous (appears {count} times). Make it more specific.", "exit_code": 1}
        new_md = md.replace(old, new_str, 1)
        try:
            sk_new = Skill.from_markdown(new_md)
        except Exception as e:
            return {"error": f"Patched content is not valid SKILL.md: {e}", "exit_code": 1}
        sk_new.name = slugify(sk_new.name or name)
        ok = sm.update_skill(name, _skill_dump(sk_new), owner=owner)
        return {"results": f"Patched skill `{sk_new.name}`."} if ok else {"error": "Patch update failed", "exit_code": 1}

    if action == "publish":
        if not name:
            return {"error": "name is required for publish", "exit_code": 1}
        all_skills = sm.load(owner=owner)
        match = next((s for s in all_skills if s.get("name") == name), None)
        if not match:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        updates = {"status": "published"}
        if args.get("confidence") is not None:
            updates["confidence"] = max(0.0, min(1.0, float(args["confidence"])))
        sm.update_skill(name, updates, owner=owner)
        return {"results": f"✅ Published `{name}`. It now appears in the skills index for future turns."}

    if action == "delete":
        if not name:
            return {"error": "name is required for delete", "exit_code": 1}
        ok = sm.delete_skill(name, owner=owner)
        return {"results": f"Deleted skill `{name}`."} if ok else {"error": f"Skill {name!r} not found", "exit_code": 1}

    if action == "search":
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "query is required for search", "exit_code": 1}
        results = sm.get_relevant_skills(query, sm.load(owner=owner), max_items=5)
        if not results:
            return {"results": "No matching skills found."}
        lines = []
        for sk in results:
            proc = sk.get("procedure") or sk.get("steps") or []
            steps_str = " → ".join(proc[:5])
            lines.append(f"**{sk['name']}**: {sk.get('description','')}\n  When: {sk.get('when_to_use','')}\n  Steps: {steps_str}")
        return {"results": "\n\n".join(lines)}

    return {
        "error": (
            f"Unknown action: {action!r}. "
            "Use one of: list, view, view_ref, add, edit, patch, publish, delete, search."
        ),
        "exit_code": 1,
    }


def _skill_dump(sk) -> Dict:
    """Translate a parsed Skill back into the kwargs `update_skill` expects."""
    return {
        "name": sk.name,
        "description": sk.description,
        "version": sk.version,
        "category": sk.category,
        "tags": sk.tags,
        "platforms": sk.platforms,
        "requires_toolsets": sk.requires_toolsets,
        "fallback_for_toolsets": sk.fallback_for_toolsets,
        "status": sk.status,
        "confidence": sk.confidence,
        "source": sk.source,
        "teacher_model": sk.teacher_model,
        "owner": sk.owner,
        "when_to_use": sk.when_to_use,
        "procedure": sk.procedure,
        "pitfalls": sk.pitfalls,
        "verification": sk.verification,
        "body_extra": sk.body_extra,
    }


# ---------------------------------------------------------------------------
# Task management tool
# ---------------------------------------------------------------------------

async def do_manage_tasks(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_tasks tool calls: CRUD on scheduled tasks."""
    import uuid as _uuid
    from core.database import SessionLocal, ScheduledTask
    from src.task_scheduler import compute_next_run

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = args.get("action", "list")
    db = SessionLocal()
    try:
        if action == "list":
            q = db.query(ScheduledTask)
            if owner:
                q = q.filter(ScheduledTask.owner == owner)
            tasks = q.order_by(ScheduledTask.created_at.desc()).all()
            task_list = []
            for t in tasks:
                task_list.append({
                    "id": t.id, "name": t.name, "status": t.status,
                    "task_type": t.task_type or "llm",
                    "action": t.action,
                    "trigger_type": t.trigger_type or "schedule",
                    "schedule": t.schedule,
                    "trigger_event": t.trigger_event,
                    "trigger_count": t.trigger_count,
                    "next_run": t.next_run.isoformat() + "Z" if t.next_run else None,
                    "last_run": t.last_run.isoformat() + "Z" if t.last_run else None,
                    "run_count": t.run_count or 0,
                })
            return {"response": f"Found {len(task_list)} tasks", "tasks": task_list, "exit_code": 0}

        elif action == "create":
            task_type = args.get("task_type", "llm")
            trigger_type = args.get("trigger_type", "schedule")

            if task_type in ("llm", "research") and not args.get("prompt"):
                return {"error": "Prompt is required for llm/research tasks", "exit_code": 1}
            if task_type == "action" and not args.get("action_name"):
                return {"error": "action_name is required for action tasks", "exit_code": 1}

            # Compute next_run for schedule triggers
            next_run = None
            if trigger_type == "schedule":
                schedule = args.get("schedule", "daily")
                next_run = compute_next_run(
                    schedule, args.get("scheduled_time", "09:00"),
                    args.get("scheduled_day"),
                )

            task_id = str(_uuid.uuid4())
            # Guard each fallback with `or`: args.get("prompt", default) returns
            # None when the key is present but null, and None[:50] raises.
            name = args.get("name") or (args.get("prompt") or args.get("action_name") or "Task")[:50]

            task = ScheduledTask(
                id=task_id,
                owner=owner,
                name=name,
                prompt=args.get("prompt"),
                task_type=task_type,
                action=args.get("action_name"),
                schedule=args.get("schedule") if trigger_type == "schedule" else None,
                scheduled_time=args.get("scheduled_time", "09:00") if trigger_type == "schedule" else None,
                scheduled_day=args.get("scheduled_day"),
                trigger_type=trigger_type,
                trigger_event=args.get("trigger_event"),
                trigger_count=args.get("trigger_count"),
                trigger_counter=0,
                next_run=next_run,
                status="active",
                output_target=args.get("output_target", "session"),
            )
            db.add(task)
            db.commit()
            return {"response": f"Created task '{name}' (id: {task_id})", "task_id": task_id, "exit_code": 0}

        elif action == "edit":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for edit", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            changed = []
            for field in ("name", "prompt", "output_target"):
                if args.get(field) is not None:
                    setattr(task, field, args[field])
                    changed.append(field)
            if args.get("task_type") is not None:
                task.task_type = args["task_type"]
                changed.append("task_type")
            if args.get("action_name") is not None:
                task.action = args["action_name"]
                changed.append("action")
            if args.get("trigger_type") is not None:
                task.trigger_type = args["trigger_type"]
                changed.append("trigger_type")
            if args.get("trigger_event") is not None:
                task.trigger_event = args["trigger_event"]
                changed.append("trigger_event")
            if args.get("trigger_count") is not None:
                task.trigger_count = args["trigger_count"]
                changed.append("trigger_count")

            schedule_changed = False
            for field in ("schedule", "scheduled_time", "scheduled_day"):
                if args.get(field) is not None:
                    setattr(task, field, args[field])
                    changed.append(field)
                    schedule_changed = True

            if schedule_changed and (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time, task.scheduled_day,
                )

            db.commit()
            return {"response": f"Updated task '{task.name}': {', '.join(changed)}", "exit_code": 0}

        elif action == "delete":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for delete", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}
            name = task.name
            db.delete(task)
            db.commit()
            return {"response": f"Deleted task '{name}'", "exit_code": 0}

        elif action in ("pause", "resume"):
            task_id = args.get("task_id")
            if not task_id:
                return {"error": f"task_id is required for {action}", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            if action == "pause":
                task.status = "paused"
            else:
                task.status = "active"
                if (task.trigger_type or "schedule") == "schedule":
                    task.next_run = compute_next_run(
                        task.schedule, task.scheduled_time, task.scheduled_day,
                    )
            db.commit()
            return {"response": f"Task '{task.name}' {action}d", "exit_code": 0}

        elif action == "run":
            task_id = args.get("task_id")
            if not task_id:
                return {"error": "task_id is required for run", "exit_code": 1}
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return {"error": f"Task {task_id} not found", "exit_code": 1}
            if owner and task.owner and task.owner != owner:
                return {"error": "Access denied", "exit_code": 1}

            from src.event_bus import get_task_scheduler
            scheduler = get_task_scheduler()
            if scheduler:
                started = await scheduler.run_task_now(task_id)
                if started:
                    return {"response": f"Task '{task.name}' triggered", "exit_code": 0}
                else:
                    return {"error": "Task is already running", "exit_code": 1}
            return {"error": "Task scheduler not available", "exit_code": 1}

        else:
            return {"error": f"Unknown action: {action}", "exit_code": 1}

    except Exception as e:
        logger.error(f"manage_tasks error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API call tool
# ---------------------------------------------------------------------------

async def do_api_call(content: str) -> Dict:
    """Execute an API call to a registered integration."""
    from src.integrations import execute_api_call, load_integrations
    try:
        args = json.loads(content)
    except json.JSONDecodeError:
        # Try line-based format: integration\nmethod path\nbody
        lines = content.strip().split("\n")
        args = {"integration": lines[0].strip() if lines else ""}
        if len(lines) > 1:
            parts = lines[1].strip().split(" ", 1)
            args["method"] = parts[0] if parts else "GET"
            args["path"] = parts[1] if len(parts) > 1 else "/"
        if len(lines) > 2:
            try:
                args["body"] = json.loads("\n".join(lines[2:]))
            except json.JSONDecodeError:
                pass

    integration_name = args.get("integration", "")
    integrations = load_integrations()
    intg = next((i for i in integrations if i["id"] == integration_name
                 or i["name"].lower() == integration_name.lower()), None)
    if not intg:
        available = ", ".join(i["name"] for i in integrations if i.get("enabled", True))
        return {"error": f"No integration matching '{integration_name}'. Available: {available or 'none configured'}", "exit_code": 1}

    return await execute_api_call(
        intg["id"],
        args.get("method", "GET"),
        args.get("path", "/"),
        params=args.get("params"),
        body=args.get("body"),
        extra_headers=args.get("headers"),
    )


# Paths the generic `app_api` tool will refuse to call. Auth/token/user
# administration and host shell execution are too risky to route through an
# agent surface even when the agent is admin-context; accidental account or
# command mistakes have permanent blast radius.
_APP_API_BLOCKLIST_PREFIXES = (
    "/api/auth",           # login/logout/password
    "/api/users",          # user CRUD (bare /api/users list+create+delete must also block)
    "/api/tokens",         # api token mgmt (bare /api/tokens list+create must also block)
    "/api/admin",          # admin one-shots (wipe etc.)
    "/api/shell",          # host shell execution must stay behind named command tooling
    "/api/backup/restore", # destructive restore
)

# (method, prefix) pairs to refuse specifically. Used for endpoints
# where GET is fine but writes are destructive or host-control shaped.
# Saw the agent wipe cookbook_state.json (presets + tasks) by POSTing
# {"tasks": []} to /api/cookbook/state, which overwrote the whole file.
# Use dedicated tools or UI flows instead.
_APP_API_BLOCKLIST_METHOD_PATH = (
    ("GET",    "/api/email/accounts"),  # owner-filtered in tool context; use list_email_accounts MCP tool
    ("POST",   "/api/cookbook/state"),   # whole-file overwrite — agent must use serve_preset/serve_model instead
    ("DELETE", "/api/cookbook/state"),
    # Host-control routes: package install, engine rebuild, and process
    # signalling should not be reachable through the generic API bridge.
    ("POST",   "/api/cookbook/packages/install"),
    ("POST",   "/api/cookbook/rebuild-engine"),
    ("POST",   "/api/cookbook/kill-pid"),
    # Use the named tools (download_model / serve_model) — they handle
    # host-name resolution, per-host env_prefix, AND register the task
    # in cookbook state so it shows in the UI + list_downloads. Hitting
    # the raw endpoint via app_api skips all of that → orphan task.
    ("POST",   "/api/model/download"),
    ("POST",   "/api/model/serve"),
    # Use trigger_research — it returns a UI hint so the Deep Research
    # sidebar surfaces the session. Raw start works but the agent
    # fumbles the payload + the session doesn't reliably show up.
    ("POST",   "/api/research/start"),
    # Use the named tools — they handle owner attribution, natural-
    # language due_date parsing, timezone, dedup, and tag/category
    # normalization. Hitting the raw endpoint via app_api saves a
    # note/event with the wrong fields, no reminder, or the wrong tz.
    ("POST",   "/api/notes"),
    ("PUT",    "/api/notes"),
    ("DELETE", "/api/notes"),
    ("POST",   "/api/calendar/events"),
    ("PUT",    "/api/calendar/events"),
    ("DELETE", "/api/calendar/events"),
)


async def do_app_api(content: str, owner: Optional[str] = None) -> Dict:
    """Generic loopback to allowed internal Odysseus API endpoints. Lets the
    agent reach the full UI-button surface (cookbook, email, notes,
    calendar, skills, sessions, gallery, research, etc.) without us
    landing a named tool wrapper for every one.

    Args (JSON):
      action: "call" (default) | "endpoints"
      path:   "/api/cookbook/gpus"     # required for call
      method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" (default GET)
      body:   <object>                 # JSON body for POST/PUT/PATCH
      query:  <object>                 # querystring params

    The `endpoints` action returns the OpenAPI surface (method + path +
    summary) so the agent can discover what's reachable. A blocklist
    refuses sensitive auth/user/admin/shell paths and method-specific
    host-control routes to keep blast radius bounded.
    """
    # `_internal_headers` and `_INTERNAL_BASE` still live in
    # tool_implementations.py (shared by many domain tools). Function-local
    # import avoids a top-level circular dependency until a later task
    # relocates them.
    from src.tool_implementations import _internal_headers, _INTERNAL_BASE

    import httpx
    try:
        args = _parse_tool_args(content) if content.strip() else {}
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "call").lower()
    base = _INTERNAL_BASE

    if action == "endpoints":
        # Fetch FastAPI's OpenAPI schema so the agent can discover any
        # endpoint without us pre-listing them. Filter by an optional
        # `filter` keyword (substring match on path or summary).
        kw = (args.get("filter") or "").lower()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{base}/openapi.json",
                                        headers=_internal_headers())
                data = resp.json()
        except Exception as e:
            return {"error": f"OpenAPI fetch failed: {e}", "exit_code": 1}
        rows: List[Dict[str, Any]] = []
        for path, methods in (data.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
                continue
            for method, op in methods.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete"):
                    continue
                if any(method.upper() == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
                    continue
                summary = (op or {}).get("summary") or (op or {}).get("description") or ""
                if isinstance(summary, str):
                    summary = summary.strip().split("\n")[0][:140]
                if kw and kw not in path.lower() and kw not in (summary or "").lower():
                    continue
                rows.append({"method": method.upper(), "path": path, "summary": summary})
        rows.sort(key=lambda r: (r["path"], r["method"]))
        if not rows:
            return {"output": f"No endpoints match filter {kw!r}." if kw else "No endpoints found.", "exit_code": 0}
        lines = [f"{len(rows)} endpoint(s)" + (f" matching {kw!r}" if kw else "") + ":"]
        for r in rows[:200]:
            line = f"  {r['method']:6s} {r['path']}"
            if r["summary"]:
                line += f"  — {r['summary']}"
            lines.append(line)
        if len(rows) > 200:
            lines.append(f"  ...({len(rows) - 200} more — filter to narrow)")
        return {"output": "\n".join(lines), "endpoints": rows, "exit_code": 0}

    # action == "call"
    path = args.get("path") or ""
    if not path:
        return {"error": "path is required (e.g. '/api/cookbook/gpus')", "exit_code": 1}
    if not path.startswith("/"):
        path = "/" + path
    if any(path.startswith(p) for p in _APP_API_BLOCKLIST_PREFIXES):
        return {"error": f"Path blocked for safety: {path}. Sensitive endpoints are off-limits via app_api.", "exit_code": 1}

    method = (args.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"error": f"Unsupported method: {method}", "exit_code": 1}
    if any(method == m and path.startswith(p) for m, p in _APP_API_BLOCKLIST_METHOD_PATH):
        if "/api/email/accounts" in path:
            return {"error": "Don't use /api/email/accounts via app_api — it is owner-filtered in tool context and may return empty. Use the `list_email_accounts` email tool, then pass `account` to list_emails/read_email.", "exit_code": 1}
        if "/api/cookbook/packages/install" in path:
            return {"error": "Don't POST /api/cookbook/packages/install via app_api — package installation is host code execution. Use the dedicated Cookbook dependency UI/flow instead.", "exit_code": 1}
        if "/api/cookbook/rebuild-engine" in path:
            return {"error": "Don't POST /api/cookbook/rebuild-engine via app_api — engine rebuild mutates local or remote host state. Use the dedicated Cookbook UI/flow instead.", "exit_code": 1}
        if "/api/cookbook/kill-pid" in path:
            return {"error": "Don't POST /api/cookbook/kill-pid via app_api — process signalling is host control. Use the dedicated Cookbook stop/diagnostic flow instead.", "exit_code": 1}
        if "/api/model/download" in path:
            return {"error": "Don't POST /api/model/download directly — use the `download_model` tool (it resolves the server name, sets the venv env_prefix, and registers the task so it shows in the UI).", "exit_code": 1}
        if "/api/model/serve" in path:
            return {"error": "Don't POST /api/model/serve directly — use the `serve_model` or `serve_preset` tool (handles host resolution, env_prefix, and cookbook tracking).", "exit_code": 1}
        if "/api/research/start" in path:
            return {"error": "Don't POST /api/research/start directly — use the `trigger_research` tool (it surfaces the session in the Deep Research sidebar).", "exit_code": 1}
        if "/api/notes" in path:
            return {"error": "Don't hit /api/notes via app_api — use the `manage_notes` tool. It accepts natural-language due_date ('11pm today', 'tomorrow at 9am'), fires reminders from the due_date itself (no separate calendar event), and uses the caller's timezone. The raw endpoint requires ISO-UTC + a separate calendar event, both of which the agent tends to get wrong.", "exit_code": 1}
        if "/api/calendar/events" in path:
            return {"error": "Don't hit /api/calendar/events via app_api — use the `manage_calendar` tool. It handles tz-aware natural-language datetimes and reminder_minutes correctly. If the user wants a note + reminder, prefer `manage_notes` with due_date — it bundles both.", "exit_code": 1}
        return {"error": f"{method} {path} is blocked — it overwrites the whole cookbook state file. Use list_serve_presets / serve_preset / serve_model instead.", "exit_code": 1}

    body = args.get("body")
    query = args.get("query") or None
    # Pass owner so the backend impersonates the user — without this,
    # POSTs (notes, calendar, todos, ...) get owner="internal-tool"
    # and the user that asked for them can't see the result.
    headers = {**_internal_headers(owner=owner), "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method, f"{base}{path}",
                json=body if body is not None and method in ("POST", "PUT", "PATCH") else None,
                params=query,
                headers=headers,
            )
        # Try to parse JSON; fall back to raw text.
        try:
            payload = resp.json()
            preview = json.dumps(payload, indent=2, default=str)
            if len(preview) > 4000:
                preview = preview[:4000] + "\n... (truncated)"
        except Exception:
            payload = None
            preview = (resp.text or "")[:4000]
        if resp.status_code >= 400:
            return {
                "error": f"{method} {path} -> HTTP {resp.status_code}",
                "status_code": resp.status_code,
                "body": preview,
                "exit_code": 1,
            }
        return {
            "output": f"{method} {path} -> {resp.status_code}\n{preview}",
            "status_code": resp.status_code,
            "json": payload,
            "exit_code": 0,
        }
    except Exception as e:
        return {"error": f"{method} {path} failed: {e}", "exit_code": 1}
