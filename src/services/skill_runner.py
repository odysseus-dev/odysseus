"""Skill test runner + audit helpers (ARCH-P7-02).

Real implementations of the skill-audit primitives — _resolve_audit_models,
_run_audit_all_job — and the shared _skill_audit_jobs job store now live here
(relocated from routes/skills_routes.py in P8-T15) so the dependency is
inverted: routes/skills_routes.py imports these FROM the service, and both the
route handlers and src/builtin_actions.py mutate the SAME _skill_audit_jobs dict.

The one residual dependency on the route layer is the per-skill audit step
_audit_one_skill, which carries a large cluster of route-local judging helpers
and is out of scope for this inversion; _run_audit_all_job reaches it via a
deferred (runtime, post-import) function-local import — not module-load and not
the importlib delegation this task removed.
"""

import logging
from typing import Any, Optional

from src.agent_loop import stream_agent_loop

logger = logging.getLogger(__name__)


# Shared audit-job store — the single source of truth mutated by both the route
# handlers (routes/skills_routes.py) and src/builtin_actions.py. Keyed by
# (owner,) tuples.
_skill_audit_jobs: dict = {}


def get_skill_audit_jobs() -> dict:
    """Return the shared _skill_audit_jobs dict.

    Returns a reference to the actual module-level dict so that mutations in
    builtin_actions.py and the route handlers are mutually visible.
    """
    return _skill_audit_jobs


def _resolve_audit_models(owner=None):
    """Resolve (url, model, headers, teacher) for an audit run from Settings.

    Worker = Utility model (falling back to Default, normalized to a served
    model id); teacher = the optional Settings → Teacher Model config. Shared
    by the manual /audit-all route and scheduled/event audits. Raises
    ValueError if no worker model.
    """
    from src.endpoint_resolver import resolve_endpoint
    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        raise ValueError("No model configured — set a Default or Utility model in Settings.")
    try:
        from src.llm_core import list_model_ids
        import os as _os
        _avail = list_model_ids(url, headers=headers)
        if _avail and model not in _avail:
            _base = _os.path.basename((model or "").rstrip("/"))
            model = next((a for a in _avail if _os.path.basename(a.rstrip("/")) == _base), None) or _avail[0]
    except Exception:
        pass

    teacher = None
    try:
        from src.settings import get_setting
        if get_setting("teacher_enabled", False):
            spec = (get_setting("teacher_model", "") or "").strip()
            if spec:
                from src.ai_interaction import _resolve_model
                t_url, t_model, t_headers = _resolve_model(spec, owner=owner)
                if t_url and t_model:
                    teacher = (t_url, t_model, t_headers)
    except Exception as e:
        logger.warning(f"Audit teacher resolve failed: {e}")
    return url, model, headers, teacher


async def _run_audit_all_job(key, skills_manager, names, url, model, headers, teacher, owner):
    """Background: audit each named skill in sequence, recording progress."""
    import asyncio as _asyncio
    import time as _time
    # Per-skill audit step + its judging-helper cluster stay in the route layer;
    # reached here via a deferred (runtime) import — not a module-load cycle.
    from routes.skills_routes import _audit_one_skill

    job = _skill_audit_jobs.get(key)
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
                res = await _audit_one_skill(skills_manager, sk, url, model, headers, teacher, owner, log)
            except _asyncio.CancelledError:
                cancelled = True
                job["cancel"] = True
                log("(cancelled)")
                raise
            except Exception as e:
                log(f"{nm}: error — {e}")
                res = {"skill": nm, "result": "error"}
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
                        "necessity": refreshed.get("necessity"),
                    }
            except Exception:
                pass
            job["results"].append(res)
            job["done"] = len(job["results"])
    except _asyncio.CancelledError:
        cancelled = True
    finally:
        job["current"] = None
        job["status"] = "cancelled" if cancelled or job.get("cancel") else "done"
        job["finished"] = _time.time()
        job.pop("task", None)


def _skill_test_task(skill: dict) -> str:
    """Build a self-contained test task. Many skills act ON something (a doc,
    an email); if we just hand over the 'when to use' text the agent has nothing
    to work on and stalls asking for input. So we tell it to create its own
    realistic fixture first, then apply the skill end-to-end."""
    if not isinstance(skill, dict):
        skill = {}
    ctx = (skill.get("when_to_use") or skill.get("description") or skill.get("name") or "").strip()
    return (
        "Test this skill end-to-end. FIRST, set up a small realistic scenario it "
        "applies to — create any sample input it needs (e.g. a short document, a "
        "note, sample data). Do NOT ask the user for input; invent a plausible "
        "example yourself. THEN apply the skill fully to that example and show the "
        "result. Context for when this skill is used: " + (ctx or "(general)")
    )


async def _eval_skill_run(skill_md: str, task: str, transcript: str,
                          url: str, model: str, headers: Optional[dict]) -> dict:
    """LLM-as-judge: grade a skill test run from its transcript. Advisory only.

    Robust against local reasoning models (strips <think>, lenient JSON,
    generous token budget) — same defensive parsing used elsewhere.
    """
    import json as _json
    import re as _re
    from src.llm_core import llm_call_async

    sys_prompt = (
        "You are a strict QA reviewer judging whether an AI 'skill' (a reusable "
        "procedure) actually works. You are given the SKILL, the TASK it was tested "
        "on, and the TRANSCRIPT of the agent's run.\n\n"
        "Judge honestly:\n"
        "- Did following the skill accomplish the task?\n"
        "- Are the steps clear, correct, and reproducible?\n"
        "- Did it reference tools/commands that don't exist or that errored?\n"
        "- Is it too vague or generic to be a useful, reusable skill?\n"
        "- METADATA: do the frontmatter fields match what the skill actually does? "
        "Flag wrong/misleading/missing tags, a wrong category, a when_to_use that "
        "doesn't describe the real trigger, or a description that oversells or "
        "mismatches the body. List each metadata problem in 'issues' (prefix it "
        "with 'metadata:'). Metadata problems alone do NOT make the verdict 'fail' "
        "if the procedure works — note them as issues on an otherwise-passing run.\n\n"
        "IMPORTANT — fairness rule: if the run could NOT proceed because it lacked "
        "an input or target the test never provided (e.g. there was no document/"
        "email/data to act on, so the agent reasonably asked for it), that is NOT "
        "the skill's fault. Return verdict \"inconclusive\" — do NOT mark it fail "
        "or needs_work. Only judge the skill's PROCEDURE; reserve fail/needs_work "
        "for when the steps themselves are wrong, vague, or reference missing tools.\n\n"
        "If you need to reason, do it inside <think></think> FIRST. Then output "
        "ONLY this JSON (no fences):\n"
        '{"verdict": "pass" | "needs_work" | "fail" | "inconclusive", '
        '"confidence": 0.0-1.0, "summary": "one short sentence", '
        '"issues": ["short issue", ...]}'
    )

    def _clip(t: str, limit: int = 24000) -> str:
        t = (t or "").strip() or "(no output produced)"
        if len(t) <= limit:
            return t
        head = limit // 4
        return t[:head] + "\n\n…[transcript trimmed for length]…\n\n" + t[-(limit - head):]

    user_msg = (
        f"=== SKILL ===\n{(skill_md or '')[:4000]}\n\n"
        f"=== TASK ===\n{task}\n\n"
        f"=== TRANSCRIPT ===\n{_clip(transcript)}"
    )
    _VERDICTS = ("pass", "needs_work", "fail", "inconclusive")

    def _parse(raw: str):
        """Return a final result dict on success, or None if unparseable."""
        text = (raw or '')
        text = _re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', text, flags=_re.I)
        text = _re.sub(r'<think(?:ing)?>[\s\S]*$', '', text, flags=_re.I).strip()

        def _coerce(d):
            return d if (isinstance(d, dict) and "verdict" in d) else None

        data = None
        for m in _re.finditer(r'\{[\s\S]*?\}', text):
            frag = m.group(0)
            for cand in (frag, _re.sub(r',(\s*[}\]])', r'\1', frag)):
                try:
                    d = _coerce(_json.loads(cand))
                except Exception:
                    d = None
                if d is not None:
                    data = d
        if data is None:
            a, b = text.find('{'), text.rfind('}')
            if a >= 0 and b > a:
                frag = text[a:b + 1]
                for cand in (frag, _re.sub(r',(\s*[}\]])', r'\1', frag)):
                    try:
                        d = _coerce(_json.loads(cand))
                    except Exception:
                        d = None
                    if d is not None:
                        data = d
                        break

        v = str(data.get("verdict", "")).lower().strip() if isinstance(data, dict) else None
        if v not in _VERDICTS:
            km = _re.search(r'verdict["\'\s:]*\s*["\']?(pass|needs_work|fail|inconclusive)', text, _re.I)
            if km:
                v = km.group(1).lower()
                if data is None:
                    data = {}
        if not isinstance(data, dict) or v not in _VERDICTS:
            return None
        try:
            conf = float(data.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0
        return {
            "verdict": v,
            "confidence": max(0.0, min(1.0, conf)),
            "summary": str(data.get("summary", ""))[:400],
            "issues": [str(x)[:200] for x in (data.get("issues") or []) if str(x).strip()][:8],
        }

    last_text = ""
    last_err = None
    for attempt in range(2):
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg}]
        if attempt == 1:
            msgs[0]["content"] = (
                sys_prompt + "\n\nDO NOT use <think> or any reasoning. Your reply "
                "must START with '{' and be ONLY the JSON object, nothing else."
            )
        try:
            raw = await llm_call_async(
                url, model, msgs,
                temperature=0.1, max_tokens=32768, headers=headers, timeout=180,
            )
        except Exception as e:
            last_err = e
            continue
        last_text = (raw or '')
        parsed = _parse(raw)
        if parsed is not None:
            return parsed

    if last_err is not None and not last_text:
        return {"verdict": "unknown", "confidence": 0, "summary": f"Evaluator call failed: {last_err}", "issues": []}
    return {"verdict": "unknown", "confidence": 0,
            "summary": "Evaluator returned unparseable output.", "issues": [], "raw": last_text[:300]}


async def _run_skill_test_once(md: str, task: str, url, model, headers, owner) -> tuple:
    """Run the skill once in the agent loop; return (transcript, verdict)."""
    import json as _json
    transcript = []
    messages = [
        {"role": "system", "content":
            "You are TESTING a skill. Follow this skill's procedure to complete the task "
            "for real, using your tools, step by step.\n\n=== SKILL ===\n" + md},
        {"role": "user", "content": task},
    ]
    try:
        async for chunk in stream_agent_loop(url, model, messages, headers=headers,
                                             temperature=0.3, max_tokens=0, max_rounds=8, owner=owner):
            if not chunk.startswith("data: ") or chunk.strip() == "data: [DONE]":
                continue
            try:
                d = _json.loads(chunk[6:])
            except Exception:
                continue
            if d.get("delta"):
                transcript.append(d["delta"])
            elif d.get("type") == "tool_start":
                transcript.append(f"\n[tool {d.get('tool')}] {str(d.get('command') or d.get('args') or '')[:300]}\n")
            elif d.get("type") == "tool_output":
                transcript.append(f"[output] {str(d.get('output') or '')[:600]}\n")
            elif d.get("type") == "agent_step":
                transcript.append(f"\n--- round {d.get('round')} ---\n")
    except Exception as e:
        transcript.append(f"\n[run error] {e}\n")
    text = "".join(transcript)
    verdict = await _eval_skill_run(md, task, text, url, model, headers)
    return text, verdict
