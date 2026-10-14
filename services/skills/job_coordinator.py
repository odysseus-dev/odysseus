"""Transient job transitions and public polling projections.

HTTP ownership and approval validation happen before these operations. These
functions use the supplied registries and preserve the existing task ownership.
"""
import asyncio as _asyncio
import logging
import time as _time

from src.prompt_security import untrusted_context_message
from .dependencies import AuditDependencies

logger = logging.getLogger(__name__)


def start_test_job(name, md, task, url, model, headers, user, skills_manager,
                   request_authority, *, deps: AuditDependencies):
    key = (user or "", name)
    previous_job = deps.test_jobs.get(key) or {}
    previous_approval = previous_job.get("approval") or {}
    if previous_approval.get("approval_id"):
        try:
            from src.tool_approvals import tool_approval_store
            tool_approval_store.consume(
                previous_approval["approval_id"],
                decision="deny",
                owner=user,
                session_id=None,
            )
        except Exception:
            logger.debug("Could not retire replaced skill approval", exc_info=True)
    deps.test_jobs[key] = {
        "status": "running",
        "task": task,
        "model": model,
        "skill": name,
        "started": _time.time(),
        "log": [{"type": "skill_test_start", "task": task, "skill": name, "model": model}],
        "verdict": None,
        "_run": {
            "md": md,
            "url": url,
            "model": model,
            "headers": headers,
            "owner": user,
            "request_authority": request_authority,
        },
    }
    _asyncio.create_task(deps.run_test_job(
        key, name, md, task, url, model, headers, user, skills_manager,
        request_authority=request_authority))
    return {"ok": True, "status": "running", "skill": name, "model": model}


def continue_test_job(job, key, name, decision, expected, exact_approval,
                      skills_manager, *, deps: AuditDependencies):
    job.pop("approval", None)
    if decision == "deny":
        job.pop("_transcript", None)
        job.pop("_run", None)
        job["log"].append({
            "type": "approval_denied",
            "text": "Exact action denied; the skill test stopped without executing it.",
        })
        job["verdict"] = {
            "verdict": "inconclusive",
            "confidence": 1.0,
            "summary": "The test stopped because its exact action was denied.",
            "issues": [],
        }
        job["status"] = "done"
        return {"ok": True, "status": "done", "decision": "deny"}

    run = job.get("_run") or {}
    transcript = job.pop("_transcript", [])
    # stream_agent_loop owns its per-round message list internally. Rebuild
    # continuation context from the original untrusted skill plus the
    # accumulated transcript so repeated approvals do not lose earlier
    # approved results, while keeping every transcript byte tainted.
    messages = deps.test_messages(
        run.get("md", ""),
        job.get("task", ""),
    )
    if transcript:
        messages.append(untrusted_context_message(
            "skill test transcript",
            "".join(str(item) for item in transcript),
        ))
    messages.extend([
        {
            "role": "assistant",
            "content": str(expected.get("question") or "Allow this exact action once?"),
        },
        {
            "role": "user",
            "content": (
                f"Approved the exact {exact_approval.pending.tool_name} "
                "action shown above once."
            ),
        },
    ])
    job["status"] = "running"
    job["log"].append({
        "type": "approval_granted",
        "text": (
            f"Approved exact {exact_approval.pending.tool_name} action once; "
            "resuming test."
        ),
    })
    _asyncio.create_task(deps.run_test_job(
        key,
        name,
        run.get("md", ""),
        job.get("task", ""),
        run.get("url"),
        run.get("model"),
        run.get("headers"),
        run.get("owner"),
        skills_manager,
        messages=messages,
        transcript=transcript,
        exact_approval=exact_approval,
    ))
    return {"ok": True, "status": "running", "decision": "approve"}


def test_status(user, name, *, deps: AuditDependencies):
    job = deps.test_jobs.get((user or "", name))
    if not job:
        return {"status": "none"}
    return {
        "status": job["status"],
        "task": job.get("task"),
        "model": job.get("model"),
        "log": job.get("log", []),
        "verdict": job.get("verdict"),
        "approval": job.get("approval"),
    }


def select_audit_names(skills_manager, user, scope, requested_names, skip_audited):
    skills = skills_manager.load(owner=user)
    # Built-ins are tracked, pre-approved application procedures. They do
    # not consume audit turns and cannot be demoted by an audit result.
    auditable_skills = [s for s in skills if s.get("source") != "builtin"]
    by_name = {s.get("name"): s for s in auditable_skills if s.get("name")}
    if isinstance(requested_names, list):
        names = []
        seen = set()
        for raw in requested_names:
            nm = str(raw or "").strip()
            if not nm or nm in seen or nm not in by_name:
                continue
            if scope not in ("all", "selected") and (by_name[nm].get("status") or "draft") == "published":
                continue
            if skip_audited and by_name[nm].get("audit_verdict"):
                continue
            names.append(nm)
            seen.add(nm)
        scope = "selected" if requested_names else scope
    elif scope == "all":
        names = [
            s.get("name") for s in auditable_skills
            if s.get("name") and (not skip_audited or not s.get("audit_verdict"))
        ]
    else:
        scope = "unchecked" if scope == "drafts" else scope
        names = [
            s.get("name") for s in auditable_skills
            if s.get("name")
            and (s.get("status") or "draft") != "published"
            and not s.get("audit_verdict")
        ]
    return names, scope


def start_audit_job(key, skills_manager, names, url, model, headers, teacher, user,
                    scope, *, deps: AuditDependencies):
    if not names:
        return {"ok": True, "status": "done", "total": 0, "results": [], "log": ["No skills to audit."]}

    deps.audit_jobs[key] = {
        "status": "running", "scope": scope, "model": model,
        "teacher": teacher[1] if teacher else None,
        "total": len(names), "done": 0, "current": None,
        "results": [], "log": [f"Auditing {len(names)} skill(s) with {model}" + (f"; teacher {teacher[1]}" if teacher else "")],
        "started": _time.time(), "cancel": False,
    }
    task = _asyncio.create_task(deps.run_batch(key, skills_manager, names, url, model, headers, teacher, user))
    deps.audit_jobs[key]["task"] = task
    return {"ok": True, "status": "running", "total": len(names), "model": model}


def audit_status(user, *, deps: AuditDependencies):
    job = deps.audit_jobs.get((user or "",))
    if not job:
        return {"status": "none"}
    return {
        "status": job["status"], "scope": job.get("scope"),
        "total": job.get("total", 0), "done": job.get("done", 0),
        "current": job.get("current"), "model": job.get("model"), "teacher": job.get("teacher"),
        "results": job.get("results", []), "log": job.get("log", []),
        "started": job.get("started"), "finished": job.get("finished"),
    }


def cancel_audit(user, *, deps: AuditDependencies):
    job = deps.audit_jobs.get((user or "",))
    if job:
        job["cancel"] = True
        job["status"] = "cancelled"
        job["current"] = None
        task = job.get("task")
        if task and not task.done():
            task.cancel()
    return {"ok": True, "status": "cancelled" if job else "none"}
