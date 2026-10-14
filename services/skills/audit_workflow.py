"""Sequential skill audit coordination and transient owner-scoped jobs."""

import logging
from typing import Optional

from services.memory.skills import SkillsManager
from .dependencies import AuditDependencies

from .agent_runner import SkillAuditUnavailable

logger = logging.getLogger(__name__)

_skill_audit_jobs: dict = {}


async def audit_one_skill(skills_manager, skill, url, model, headers,
                           teacher, owner, log, workload: str = "foreground", *, deps: AuditDependencies) -> dict:
    """Test → judge → self-edit+retry → (teacher edit+retry) → flag. Never deletes;
    a skill the teacher still can't fix is demoted to draft for manual review.
    `teacher` is (url, model, headers) or None. `log(msg)` records progress."""
    name = skill.get("name")

    # Reflect the audit outcome in the skill's confidence so the main list
    # updates: a clean pass earns high confidence; a pass that needed fixing
    # earns a bit less; a skill that still fails is marked low.
    def _set_conf(c):
        try:
            skills_manager.update_skill(name, {"confidence": c}, owner=owner)
        except Exception:
            pass

    md = skills_manager.read_skill_md(name, owner=owner)
    if not md:
        log(f"{name}: no source — skipped")
        return {"skill": name, "result": "skipped"}

    # Cheap deterministic cleanup first. If this is an obvious lower-priority
    # duplicate, do not spend LLM turns on necessity, retrieval precision,
    # skill-vs-baseline testing, self-edit, or teacher escalation.
    duplicate_of = deps.duplicate_blocker(skills_manager, name, owner)
    if duplicate_of:
        reason = f"Lower-priority duplicate of {duplicate_of}"
        try:
            skills_manager.update_skill(name, {"status": "draft", "confidence": 0.35}, owner=owner)
            skills_manager.set_audit(name, "skipped", by_teacher=False, worker_model=model, owner=owner)
            skills_manager.set_necessity(name, False, [duplicate_of], reason, owner=owner)
        except Exception:
            pass
        log(f"{name}: draft — skipped audit ({reason[:100]})")
        return {"skill": name, "result": "skipped_duplicate", "reason": reason, "confidence": 0.35, "status": "draft"}

    # Advisory necessity/redundancy check — runs once, independent of the test
    # outcome, and only records a flag the UI surfaces (never deletes/demotes).
    others = []
    nec = None
    try:
        # Only compare against skills the SAME owner can see, so the necessity
        # judge never sees (or flags "redundant_with") another user's skills.
        sk_owner = skill.get("owner")
        others = [
            {"name": s.get("name"), "description": s.get("description", "")}
            for s in skills_manager.load(owner=owner)
            if s.get("name") and s.get("name") != name
            and (not sk_owner or not s.get("owner") or s.get("owner") == sk_owner)
        ]
        nec = await deps.evaluate_necessity(
            md, others, url, model, headers, workload=workload,
            owner=owner,
        )
        if nec is not None:
            skills_manager.set_necessity(name, nec.get("necessary", True),
                                         nec.get("redundant_with"), nec.get("reason"),
                                         owner=owner)
            if not nec.get("necessary", True):
                log(f"{name}: possibly unnecessary — {nec.get('reason', '')[:80]}")
    except Exception as e:
        log(f"{name}: necessity check skipped — {e}")

    # Retrieval precision check: if broad tags/trigger text would make this
    # narrow skill over-inject, fix only metadata before the functional test.
    try:
        if deps.should_check_retrieval_precision(skill):
            rp = await deps.evaluate_retrieval_precision(
                md, others, url, model, headers, workload=workload,
                owner=owner,
            )
            if rp and not rp.get("ok"):
                issues = rp.get("issues") or ["metadata: retrieval: narrow tags and when_to_use to the intended trigger"]
                log(f"{name}: narrowing retrieval metadata — {(rp.get('summary') or issues[0])[:80]}")
                fixed = await deps.improve_markdown(md, {
                    "verdict": "pass",
                    "confidence": 1.0,
                    "summary": rp.get("summary") or "Retrieval metadata is too broad.",
                    "issues": issues,
                }, "Retrieval audit only: the procedure may work, but matching metadata is too broad.",
                    url, model, headers, workload=workload, owner=owner)
                if fixed and fixed.strip() != md.strip() and deps.apply_markdown(skills_manager, name, fixed, owner):
                    md = fixed
                    refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
                    if refreshed:
                        skill = refreshed
    except Exception as e:
        log(f"{name}: retrieval precision check skipped — {e}")

    task = deps.test_task(skill)
    log(f"{name}: testing…")
    skill_messages = deps.test_messages(md, task)
    transcript, skill_stats, approval_required = await deps.run_arm(
        skill_messages,
        url,
        model,
        headers,
        owner,
        workload=workload,
    )
    if approval_required is not None:
        try:
            from src.tool_approvals import tool_approval_store
            tool_approval_store.consume(
                approval_required.get("approval_id"),
                decision="deny",
                owner=owner,
                session_id=None,
            )
        except Exception:
            logger.debug("Could not retire unattended skill approval", exc_info=True)
        verdict = {
            "verdict": "inconclusive",
            "confidence": 1.0,
            "summary": (
                "This automated audit reached an exact action that requires "
                "a human approval; no action was executed."
            ),
            "issues": [
                "Run this skill's manual test and review the sealed action."
            ],
            "approval_required": True,
        }
    else:
        baseline_task = task
        log(f"{name}: running no-skill baseline…")
        baseline_transcript, baseline_stats, baseline_approval = await deps.run_arm(
            deps.baseline_messages(baseline_task),
            url,
            model,
            headers,
            owner,
            workload=workload,
        )
        if baseline_approval is not None:
            try:
                from src.tool_approvals import tool_approval_store
                tool_approval_store.consume(
                    baseline_approval.get("approval_id"),
                    decision="deny",
                    owner=owner,
                    session_id=None,
                )
            except Exception:
                logger.debug("Could not retire unattended baseline approval", exc_info=True)
        verdict = await deps.evaluate_run(
            md,
            task,
            transcript,
            url,
            model,
            headers,
            baseline_transcript=baseline_transcript,
            skill_stats=skill_stats,
            baseline_stats=baseline_stats,
            workload=workload,
            owner=owner,
        )
    v = verdict.get("verdict")
    log(f"{name}: verdict = {v} ({verdict.get('summary', '')[:80]})")
    if verdict.get("approval_required"):
        # An unattended audit is not authority for an action influenced by the
        # skill under test. Preserve the skill's current publication/confidence
        # state and route the exact action to the manual test UI instead of
        # letting a safety pause demote, rewrite, or auto-publish the skill.
        skills_manager.set_audit(
            name,
            "inconclusive",
            by_teacher=False,
            worker_model=model,
            owner=owner,
            audit_summary=verdict.get("summary") or "The test requires approval for an external action.",
        )
        status = skill.get("status") or "draft"
        log(f"{name}: {status} unchanged — exact action needs manual approval")
        return {
            "skill": name,
            "result": "approval_required",
            "verdict": verdict,
            "status": status,
        }
    if v == "pass":
        # Procedure works. If the reviewer still flagged metadata (tags/category/
        # when_to_use/description), do ONE fixer pass to correct the frontmatter
        # without re-testing — a metadata-only fix can't break a passing run.
        meta_issues = [i for i in (verdict.get("issues") or []) if str(i).lower().lstrip().startswith("metadata:")]
        if meta_issues:
            log(f"{name}: pass, but fixing {len(meta_issues)} metadata issue(s)…")
            fixed = await deps.improve_markdown(
                md, verdict, transcript, url, model, headers, workload=workload,
                owner=owner,
            )
            if fixed and fixed.strip() != md.strip():
                deps.apply_markdown(skills_manager, name, fixed, owner)
        _set_conf(0.95)
        skills_manager.set_audit(
            name,
            "pass",
            by_teacher=False,
            worker_model=model,
            owner=owner,
            **deps.verdict_efficiency(verdict),
        )
        refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
        status = deps.finalize_status(skills_manager, name, owner, "pass", 0.95, (refreshed or {}).get("necessity"), verdict)
        log(f"{name}: {status} — confidence 95%")
        return {"skill": name, "result": "pass", "verdict": verdict, "confidence": 0.95, "status": status}
    if v in ("unknown", "inconclusive"):
        skills_manager.set_audit(
            name,
            "inconclusive",
            by_teacher=False,
            worker_model=model,
            owner=owner,
            **deps.verdict_efficiency(verdict),
        )
        status = deps.finalize_status(skills_manager, name, owner, "inconclusive", skill.get("confidence") or 0.0, skill.get("necessity"))
        log(f"{name}: {status} — inconclusive")
        return {"skill": name, "result": "inconclusive", "verdict": verdict, "status": status}

    # Self-edit + retry.
    log(f"{name}: self-editing to fix issues…")
    new_md = await deps.improve_markdown(
        md, verdict, transcript, url, model, headers, workload=workload,
        owner=owner,
    )
    if new_md and new_md.strip() != md.strip() and deps.apply_markdown(skills_manager, name, new_md, owner):
        md = new_md
        transcript, verdict = await deps.run_test_once(
            md, task, url, model, headers, owner, workload=workload,
        )
        v = verdict.get("verdict")
        log(f"{name}: retry (self) = {v}")
        if v == "pass":
            _set_conf(0.85)
            skills_manager.set_audit(
                name,
                "pass",
                by_teacher=False,
                worker_model=model,
                owner=owner,
                **deps.verdict_efficiency(verdict),
            )
            refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
            status = deps.finalize_status(skills_manager, name, owner, "pass", 0.85, (refreshed or {}).get("necessity"), verdict)
            log(f"{name}: {status} — confidence 85% after self-edit")
            return {"skill": name, "result": "pass_after_self_edit", "verdict": verdict, "confidence": 0.85, "status": status}

    # Teacher escalation (if a distinct teacher model is configured). The
    # teacher only REWRITES the skill — it does NOT run the test. The point is
    # to verify the regular (student) model can now succeed with the teacher's
    # improved procedure, so the retry runs on the worker model, not the teacher.
    teacher_ran = False
    if teacher and teacher[0] and teacher[1] and (teacher[1] != model or teacher[0] != url):
        teacher_ran = True
        t_url, t_model, t_headers = teacher
        log(f"{name}: teacher {t_model} rewriting the skill…")
        t_md = await deps.improve_markdown(
            md, verdict, transcript, t_url, t_model, t_headers, workload=workload,
            owner=owner,
        )
        if t_md and t_md.strip() != md.strip() and deps.apply_markdown(skills_manager, name, t_md, owner):
            md = t_md
        # Re-test with the STUDENT model (the model the skill runs under in use).
            transcript, verdict = await deps.run_test_once(
                md, task, url, model, headers, owner, workload=workload,
            )
        v = verdict.get("verdict")
        log(f"{name}: retry on student after teacher rewrite = {v}")
        if v == "pass":
            _set_conf(0.8)
            skills_manager.set_audit(
                name,
                "pass",
                by_teacher=True,
                worker_model=model,
                teacher_model=t_model,
                owner=owner,
                **deps.verdict_efficiency(verdict),
            )
            refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
            status = deps.finalize_status(skills_manager, name, owner, "pass", 0.8, (refreshed or {}).get("necessity"), verdict)
            log(f"{name}: {status} — confidence 80% after teacher rewrite")
            return {"skill": name, "result": "pass_after_teacher", "verdict": verdict, "confidence": 0.8, "status": status}

    # Still failing → demote to draft + low confidence + flag (do NOT delete).
    try:
        skills_manager.update_skill(name, {"status": "draft", "confidence": 0.35}, owner=owner)
    except Exception:
        pass
    skills_manager.set_audit(
        name, v or "fail", by_teacher=teacher_ran,
        worker_model=model,
        teacher_model=(teacher[1] if teacher_ran and teacher else ""),
        owner=owner,
        **deps.verdict_efficiency(verdict),
    )
    log(f"{name}: flagged — confidence lowered, kept as draft for manual review")
    return {"skill": name, "result": "flagged", "verdict": verdict, "confidence": 0.35}


async def run_audit_batch(key, skills_manager, names, url, model, headers, teacher, owner,
                             workload: str = "foreground", *, deps: AuditDependencies):
    """Background: audit each named skill in sequence, recording progress."""
    import asyncio as _asyncio
    import time as _time

    job = deps.audit_jobs.get(key)
    if job is None:
        return

    def log(msg):
        job["log"].append(msg)
        if len(job["log"]) > 1000:
            del job["log"][0:len(job["log"]) - 1000]

    cancelled = False
    try:
        for nm in names:
            if job.get("cancel"):
                cancelled = True
                log("(cancelled)")
                break
            job["current"] = nm
            skills = skills_manager.load(owner=owner)
            sk = next((s for s in skills if s.get("name") == nm), None)
            if not sk:
                continue
            try:
                res = await deps.audit_one(
                    skills_manager, sk, url, model, headers, teacher, owner, log,
                    workload=workload,
                )
            except _asyncio.CancelledError:
                cancelled = True
                job["cancel"] = True
                log("(cancelled)")
                raise
            except SkillAuditUnavailable as e:
                job["unavailable"] = str(e)
                log(f"Audit paused: {e}. Skill verdicts unchanged; retry when the model is available.")
                break
            except Exception as e:
                log(f"{nm}: error — {e}")
                res = {"skill": nm, "result": "error"}
                skills_manager.set_audit(nm, "inconclusive", worker_model=model, owner=owner)
            try:
                refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == nm), None)
                if refreshed:
                    res["skill_state"] = {
                        "name": refreshed.get("name"),
                        "status": refreshed.get("status"),
                        "confidence": refreshed.get("confidence"),
                        "audit_verdict": refreshed.get("audit_verdict"),
                        "audit_by_teacher": refreshed.get("audit_by_teacher"),
                        "audit_worker_model": refreshed.get("audit_worker_model"),
                        "audit_teacher_model": refreshed.get("audit_teacher_model"),
                        "audited_at": refreshed.get("audited_at"),
                        "saved_turns": refreshed.get("saved_turns"),
                        "saved_tool_calls": refreshed.get("saved_tool_calls"),
                        "baseline_verdict": refreshed.get("baseline_verdict"),
                        "usefulness": refreshed.get("usefulness"),
                        "necessity": refreshed.get("necessity"),
                    }
            except Exception:
                pass
            job["results"].append(res)
            job["done"] = len(job["results"])
    except _asyncio.CancelledError:
        cancelled = True
    finally:
        if not cancelled and not job.get("cancel") and not job.get("unavailable"):
            try:
                deps.finalize_batch(skills_manager, job.get("results") or [], owner, log)
            except Exception:
                logger.warning("Could not finalize skills audit batch", exc_info=True)
        job["current"] = None
        job["status"] = "cancelled" if cancelled or job.get("cancel") else "error" if job.get("unavailable") else "done"
        job["finished"] = _time.time()
        job.pop("task", None)


async def run_scheduled_skill_audit(skills_manager: SkillsManager,
                                    owner: Optional[str] = None,
                                    max_skills: int = 8, *, deps: AuditDependencies) -> dict:
    """Nightly audit pass. Audits the LEAST-recently-audited skills first and
    caps the batch so it rotates through the library over successive nights
    instead of re-checking the same ones every run. Reuses the same job store
    the manual 'Audit all' button uses, so its progress shows in the UI too."""
    import time as _time

    key = (owner or "",)
    existing = deps.audit_jobs.get(key)
    if existing and existing.get("status") == "running":
        logger.info("Scheduled skill audit skipped — a run is already active.")
        return {"status": "running", "skipped": True}

    try:
        url, model, headers, teacher = deps.resolve_models(owner=owner)
    except ValueError as e:
        logger.info(f"Scheduled skill audit skipped — {e}")
        return {"status": "skipped", "reason": str(e)}

    from services.memory.skill_lifecycle import automatic_audit_candidates
    skills = automatic_audit_candidates(skills_manager.load(owner=owner), limit=max_skills)
    names = [s["name"] for s in skills]
    if not names:
        return {"status": "done", "total": 0}

    deps.audit_jobs[key] = {
        "status": "running", "scope": "scheduled", "model": model,
        "teacher": teacher[1] if teacher else None,
        "total": len(names), "done": 0, "current": None,
        "results": [], "log": [f"Nightly audit of {len(names)} least-recently-checked skill(s) with {model}"
                               + (f"; teacher {teacher[1]}" if teacher else "")],
        "started": _time.time(), "cancel": False,
    }
    logger.info(f"Scheduled skill audit starting: {len(names)} skill(s) (owner={owner or 'all'})")
    await deps.run_batch(
        key, skills_manager, names, url, model, headers, teacher, owner,
        workload="background",
    )
    job = deps.audit_jobs.get(key, {})
    return {"status": "done", "total": len(names), "results": job.get("results", [])}
