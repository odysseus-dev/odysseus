"""Agent execution, transcripts and same-task stream cleanup."""

import logging
from contextlib import aclosing
from typing import Optional

from .dependencies import AuditDependencies

logger = logging.getLogger(__name__)

_skill_test_jobs: dict = {}


class SkillAuditUnavailable(RuntimeError):
    """The test infrastructure failed; this is not evidence about a skill."""


async def run_test_job(
    key,
    name,
    md,
    task,
    url,
    model,
    headers,
    owner,
    skills_manager=None,
    *,
    messages=None,
    transcript=None,
    exact_approval=None,
    request_authority=None,
    deps: AuditDependencies,
):
    """Background coroutine: run the skill in an agent loop, capture a condensed
    log + transcript, then have the judge grade it. Writes into deps.test_jobs."""
    import json as _json
    from src.agent_loop import stream_agent_loop
    from src.agent_runtime.authority import RequestAuthority

    job = deps.test_jobs.get(key)
    if job is None:
        return
    log = job["log"]
    transcript = transcript if isinstance(transcript, list) else []
    say_buf = []
    skill_stats = {"turns": 0, "tool_calls": 0}

    def _flush_say():
        if say_buf:
            log.append({"type": "say", "text": "".join(say_buf)})
            say_buf.clear()

    messages = list(messages) if isinstance(messages, list) else deps.test_messages(md, task)
    try:
        # aclosing: pausing for an approval leaves this loop early, and the
        # stream's ContextVar cleanup must run in this task, not in asyncio's
        # finalizer (#6635).
        async with aclosing(stream_agent_loop(
            url, model, messages, headers=headers,
            temperature=0.3, max_tokens=0, max_rounds=8, owner=owner,
            exact_approval=exact_approval,
            request_authority=(request_authority or (
                exact_approval.pending.request_authority if exact_approval is not None else None
            ) or RequestAuthority.empty(owner=owner)),
        )) as stream:
            async for chunk in stream:
                if not chunk.startswith("data: ") or chunk.strip() == "data: [DONE]":
                    continue
                try:
                    d = _json.loads(chunk[6:])
                except Exception:
                    continue
                if d.get("delta"):
                    say_buf.append(d["delta"]); transcript.append(d["delta"])
                elif d.get("type") == "tool_start":
                    _flush_say()
                    skill_stats["tool_calls"] += 1
                    cmd = str(d.get("command") or d.get("args") or "")[:300]
                    log.append({"type": "tool_start", "tool": d.get("tool"), "command": cmd})
                    transcript.append(f"\n[tool {d.get('tool')}] {cmd}\n")
                elif d.get("type") == "tool_output":
                    _flush_say()
                    out = str(d.get("output") or "")[:600]
                    tool_log = {"type": "tool_output", "output": out}
                    approval = d.get("ask_user")
                    if isinstance(approval, dict):
                        tool_log["ask_user"] = approval
                    log.append(tool_log)
                    transcript.append(f"[output] {out}\n")
                    if (
                        isinstance(approval, dict)
                        and approval.get("kind") == "tool_approval"
                        and approval.get("approval_id")
                    ):
                        # Manual skill tests have their own polling UI instead of a
                        # chat session. Pause the run and retain only server-side
                        # continuation state until the same owner approves/denies
                        # this exact sealed action.
                        job["status"] = "awaiting_approval"
                        job["approval"] = approval
                        job["_transcript"] = transcript
                        return
                elif d.get("type") == "agent_step":
                    _flush_say()
                    try:
                        skill_stats["turns"] = max(skill_stats["turns"], int(d.get("round") or 0))
                    except (TypeError, ValueError):
                        pass
                    log.append({"type": "agent_step", "round": d.get("round")})
                    transcript.append(f"\n--- round {d.get('round')} ---\n")
                elif d.get("type") == "metrics":
                    data = d.get("data") or {}
                    try:
                        skill_stats["turns"] = max(skill_stats["turns"], int(data.get("agent_rounds") or 0))
                    except (TypeError, ValueError):
                        pass
                    try:
                        skill_stats["tool_calls"] = max(skill_stats["tool_calls"], int(data.get("tool_calls") or 0))
                    except (TypeError, ValueError):
                        pass
                if len(log) > 600:
                    del log[0:len(log) - 600]
        _flush_say()
    except Exception as e:
        _flush_say()
        log.append({"type": "error", "error": str(e)})

    job.pop("approval", None)
    job.pop("_transcript", None)
    job.pop("_run", None)
    log.append({"type": "evaluating"})
    try:
        baseline_task = task
        log.append({"type": "agent_step", "round": "baseline"})
        baseline_transcript, baseline_stats, baseline_approval = await deps.run_arm(
            deps.baseline_messages(baseline_task),
            url,
            model,
            headers,
            owner,
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
                logger.debug("Could not retire manual-test baseline approval", exc_info=True)
        job["verdict"] = await deps.evaluate_run(
            md,
            task,
            "".join(transcript),
            url,
            model,
            headers,
            baseline_transcript=baseline_transcript,
            skill_stats=skill_stats,
            baseline_stats=baseline_stats,
            owner=owner,
        )
    except Exception as e:
        job["verdict"] = {"verdict": "unknown", "confidence": 0, "summary": f"Eval failed: {e}", "issues": []}
    # Record the result so the card shows a 'verified' check (a manual test
    # never involves the teacher) and nudge the confidence score to match the
    # verdict — same scale as Audit-all's pass=0.95. inconclusive/unknown leave
    # the score alone (missing-fixture or parse failures shouldn't punish it).
    if skills_manager is not None:
        v = (job["verdict"] or {}).get("verdict") or "unknown"
        try:
            skills_manager.set_audit(
                name,
                v,
                by_teacher=False,
                worker_model=model,
                owner=owner,
                **deps.verdict_efficiency(job.get("verdict")),
            )
        except Exception:
            pass
        conf = {"pass": 0.95, "needs_work": 0.6, "fail": 0.4}.get(v)
        if conf is not None:
            try:
                skills_manager.update_skill(name, {"confidence": conf}, owner=owner)
            except Exception:
                pass
    job["status"] = "done"


async def run_audit_arm(messages: list[dict], url, model, headers, owner,
                               workload: str = "foreground") -> tuple[str, dict, Optional[dict]]:
    """Run one audit arm in the agent loop; return transcript, stats, approval."""
    import json as _json
    from src.agent_loop import stream_agent_loop
    from src.agent_runtime.authority import RequestAuthority, active_request_authority
    transcript = []
    approval_required = None
    stats = {"turns": 0, "tool_calls": 0}
    try:
        # max_tokens explicitly set: passing 0 lets some upstreams (Ollama,
        # OpenAI-compat) generate an empty completion, which manifested as
        # the skill test returning nothing while chat (which carries its
        # preset's max_tokens) worked. 4096 matches the chat default.
        # aclosing: stopping for an approval leaves this loop early (#6635).
        async with aclosing(stream_agent_loop(
            url, model, messages, headers=headers,
            temperature=0.3, max_tokens=4096, max_rounds=8,
            owner=owner, workload=workload, suppress_skills=True,
            request_authority=(active_request_authority() or RequestAuthority.empty(owner=owner)),
        )) as stream:
            async for chunk in stream:
                # Streams can include an SSE event line before the data line,
                # notably `event: error`. Do not silently discard those failures.
                payload = next((line[6:] for line in chunk.splitlines() if line.startswith("data: ")), None)
                if payload is None or payload == "[DONE]":
                    continue
                try:
                    d = _json.loads(payload)
                except Exception:
                    continue
                if d.get("error") or d.get("type") == "error":
                    raise SkillAuditUnavailable(str(d.get("error") or d.get("message") or "Audit stream failed"))
                if d.get("delta"):
                    transcript.append(d["delta"])
                elif d.get("type") == "tool_start":
                    stats["tool_calls"] += 1
                    transcript.append(f"\n[tool {d.get('tool')}] {str(d.get('command') or d.get('args') or '')[:300]}\n")
                elif d.get("type") == "tool_output":
                    transcript.append(f"[output] {str(d.get('output') or '')[:600]}\n")
                    approval = d.get("ask_user")
                    if (
                        isinstance(approval, dict)
                        and approval.get("kind") == "tool_approval"
                    ):
                        approval_required = approval
                        break
                elif d.get("type") == "agent_step":
                    try:
                        stats["turns"] = max(stats["turns"], int(d.get("round") or 0))
                    except (TypeError, ValueError):
                        pass
                    transcript.append(f"\n--- round {d.get('round')} ---\n")
                elif d.get("type") == "metrics":
                    data = d.get("data") or {}
                    try:
                        stats["turns"] = max(stats["turns"], int(data.get("agent_rounds") or 0))
                    except (TypeError, ValueError):
                        pass
                    try:
                        stats["tool_calls"] = max(stats["tool_calls"], int(data.get("tool_calls") or 0))
                    except (TypeError, ValueError):
                        pass
    except SkillAuditUnavailable:
        raise
    except Exception as e:
        raise SkillAuditUnavailable(str(e)) from e
    return "".join(transcript), stats, approval_required


async def run_test_once(md: str, task: str, url, model, headers, owner,
                               workload: str = "foreground", *, deps: AuditDependencies) -> tuple:
    """Run the skill once in the agent loop; return (transcript, verdict)."""
    messages = deps.test_messages(md, task)
    text, stats, approval_required = await deps.run_arm(
        messages, url, model, headers, owner, workload=workload,
    )
    if approval_required is not None:
        # Unattended audits have no authority to approve and no UI that could
        # resume this record. Destructively deny it now instead of leaving a
        # reusable opaque grant pending until TTL/cap eviction.
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
        return text, {
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
    verdict = await deps.evaluate_run(
        md,
        task,
        text,
        url,
        model,
        headers,
        skill_stats=stats,
        workload=workload,
        owner=owner,
    )
    return text, verdict
