# routes/skills_routes.py
"""REST API for the Skills system.

The on-disk format is SKILL.md (frontmatter + structured body) under
`data/skills/<category>/<name>/`. Old shape (`title`, `problem`, `solution`,
`steps`) still accepted on input — they're translated to the new fields
(`description`, `when_to_use`, `body_extra`, `procedure`).
"""

import logging
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.memory.skills import SkillsManager
from src.auth_helpers import get_current_user
from core.middleware import require_admin

logger = logging.getLogger(__name__)


class SkillAddRequest(BaseModel):
    # New schema (preferred)
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=200)
    category: str = Field("general", max_length=40)
    tags: List[str] = Field(default_factory=list)
    platforms: List[str] = Field(default_factory=list)
    requires_toolsets: List[str] = Field(default_factory=list)
    fallback_for_toolsets: List[str] = Field(default_factory=list)
    when_to_use: Optional[str] = Field(None, max_length=2000)
    procedure: List[str] = Field(default_factory=list)
    pitfalls: List[str] = Field(default_factory=list)
    verification: List[str] = Field(default_factory=list)
    status: str = "draft"
    version: str = "1.0.0"
    confidence: float = 0.8
    # Manual adds via this endpoint are human-authored → "user", which exempts
    # them from auto-dedup and cap-eviction in add_skill. (The agent's own
    # skill writes go through do_manage_skills with source="learned".)
    source: str = "user"
    teacher_model: Optional[str] = None
    session_id: Optional[str] = None

    # Old schema (back-compat)
    title: Optional[str] = Field(None, max_length=200)
    problem: Optional[str] = Field(None, max_length=2000)
    solution: Optional[str] = Field(None, max_length=5000)
    steps: List[str] = Field(default_factory=list)


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    platforms: Optional[List[str]] = None
    requires_toolsets: Optional[List[str]] = None
    fallback_for_toolsets: Optional[List[str]] = None
    when_to_use: Optional[str] = None
    procedure: Optional[List[str]] = None
    pitfalls: Optional[List[str]] = None
    verification: Optional[List[str]] = None
    status: Optional[str] = None
    version: Optional[str] = None
    confidence: Optional[float] = None
    body_extra: Optional[str] = None
    # Old shape
    title: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    steps: Optional[List[str]] = None


def _skill_test_task(skill: dict) -> str:
    """Build a self-contained test task. Many skills act ON something (a doc,
    an email); if we just hand over the 'when to use' text the agent has nothing
    to work on and stalls asking for input. So we tell it to create its own
    realistic fixture first, then apply the skill end-to-end."""
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
    # Give the judge plenty of transcript, and when it must trim, keep the TAIL
    # (the final result lives at the end) plus a bit of the head — truncating to
    # a short prefix made the judge wrongly call complete runs "incomplete /
    # missing sections" because it never saw the end.
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
        # Strip closed think blocks. If a <think> was opened but never closed
        # (the model ran out of budget mid-reasoning), drop everything from it
        # onward so its stray braces don't poison JSON extraction.
        text = _re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', text, flags=_re.I)
        text = _re.sub(r'<think(?:ing)?>[\s\S]*$', '', text, flags=_re.I).strip()

        def _coerce(d):
            return d if (isinstance(d, dict) and "verdict" in d) else None

        data = None
        # Scan every balanced {...} candidate and keep the LAST one that parses
        # and carries a "verdict" — the transcript is full of JSON API bodies,
        # so a naive first-brace/last-brace span almost never parses.
        for m in _re.finditer(r'\{[\s\S]*?\}', text):
            frag = m.group(0)
            for cand in (frag, _re.sub(r',(\s*[}\]])', r'\1', frag)):
                try:
                    d = _coerce(_json.loads(cand))
                except Exception:
                    d = None
                if d is not None:
                    data = d
        # Fallback to the greedy outermost span (handles nested objects the
        # non-greedy scan above splits apart).
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
        # Last resort: pull the verdict keyword straight out of the prose so a
        # clearly-decided run isn't thrown away as "unparseable".
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

    # Two attempts: the first lets the judge reason; if a heavy reasoning model
    # burns its budget inside <think> and never emits the JSON, the second
    # forbids thinking and demands the JSON immediately.
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
                # Generous budget so a heavy reasoner can think AND still have
                # room to emit the JSON afterwards (reasoning tokens come out of
                # this same cap; the server clamps to its own max).
                url, model, msgs,
                temperature=0.1, max_tokens=32768, headers=headers, timeout=180,
            )
        except Exception as e:
            # Don't give up on a transient first-attempt error — let the second
            # (no-think) attempt run before reporting failure.
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


async def _eval_skill_necessity(skill_md: str, others: list, url: str, model: str,
                                headers: Optional[dict]) -> Optional[dict]:
    """Advisory judge: is this skill worth keeping, or is it redundant / trivially
    unnecessary? Sees the OTHER skills' names+descriptions so it can spot
    duplicates. Returns {necessary, redundant_with, reason} or None. Never acts —
    purely a flag the UI surfaces."""
    import json as _json
    import re as _re
    from src.llm_core import llm_call_async

    catalog = "\n".join(f"- {o.get('name')}: {o.get('description', '')}" for o in others) or "(no other skills)"
    sys_prompt = (
        "You assess whether a reusable AI 'skill' (a saved procedure) is worth keeping. "
        "A skill is UNNECESSARY if it essentially duplicates another skill in the library, "
        "OR if it's so trivial/generic that a capable assistant would do it correctly with no "
        "saved procedure at all. A skill IS necessary if it captures a specific, non-obvious "
        "procedure, tool sequence, or hard-won detail.\n\n"
        "Be conservative: only call it unnecessary when you're confident. Reason in "
        "<think></think> first if needed, then output ONLY this JSON:\n"
        '{"necessary": true|false, "redundant_with": ["skill-name", ...], '
        '"reason": "one short sentence"}'
    )
    user_msg = (
        f"=== SKILL UNDER REVIEW ===\n{(skill_md or '')[:3000]}\n\n"
        f"=== OTHER SKILLS IN THE LIBRARY ===\n{catalog[:4000]}"
    )
    try:
        raw = await llm_call_async(
            url, model,
            [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=8192, headers=headers, timeout=120,
        )
    except Exception as e:
        logger.warning(f"Necessity check failed: {e}")
        return None
    text = _re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', (raw or ''), flags=_re.I)
    text = _re.sub(r'<think(?:ing)?>[\s\S]*$', '', text, flags=_re.I).strip()
    data = None
    a, b = text.find('{'), text.rfind('}')
    if a >= 0 and b > a:
        frag = text[a:b + 1]
        for cand in (frag, _re.sub(r',(\s*[}\]])', r'\1', frag)):
            try:
                data = _json.loads(cand)
                break
            except Exception:
                continue
    if not isinstance(data, dict) or "necessary" not in data:
        return None
    return {
        "necessary": bool(data.get("necessary", True)),
        "redundant_with": [str(x)[:80] for x in (data.get("redundant_with") or []) if str(x).strip()][:6],
        "reason": str(data.get("reason", ""))[:200],
    }


def _should_check_retrieval_precision(skill: dict) -> bool:
    """Cheap prefilter for the expensive retrieval-precision judge.

    Skills with broad tags like "network" or "document" are the ones most likely
    to over-inject. Narrow command/vendor tags alone are fine.
    """
    broad = {
        "arch", "arch linux", "linux", "network", "networking", "wifi",
        "installation", "install", "system", "ssh", "document", "documents",
        "search", "email", "calendar", "gpu", "server", "python",
    }
    tags = {str(t or "").strip().lower() for t in (skill.get("tags") or [])}
    if tags & broad:
        return True
    text = " ".join([
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("when_to_use") or ""),
    ]).lower()
    return sum(1 for t in broad if t in text) >= 2


async def _eval_skill_retrieval_precision(skill_md: str, others: list,
                                          url: str, model: str,
                                          headers: Optional[dict]) -> Optional[dict]:
    """Advisory judge: would this skill's metadata make retrieval over-select it?

    This is distinct from "does the procedure work?". It asks whether tags,
    description, and when_to_use are specific enough that the skill won't be
    injected into adjacent but wrong tasks.
    """
    import json as _json
    import re as _re
    from src.llm_core import llm_call_async

    catalog = "\n".join(f"- {o.get('name')}: {o.get('description', '')}" for o in others[:80]) or "(no other skills)"
    sys_prompt = (
        "You are auditing retrieval metadata for a reusable AI skill. The app selects "
        "skills by matching user requests against the skill name, description, tags, "
        "when_to_use, and procedure text. Judge whether this skill is likely to be "
        "over-selected for nearby but wrong requests.\n\n"
        "Focus ONLY on metadata/retrieval precision, not whether the procedure works. "
        "Flag broad tags such as network, installation, system, document, search, ssh, "
        "python, gpu, or server when they would cause this narrow skill to match too "
        "many adjacent tasks. Recommend narrower tags/when_to_use wording. Compare "
        "against the other skills to spot boundaries.\n\n"
        "Return ok=true only when the trigger metadata is narrow enough. If not ok, "
        "issues MUST start with 'metadata: retrieval:' and be actionable. Output ONLY JSON:\n"
        '{"ok": true|false, "summary": "one short sentence", "issues": ["metadata: retrieval: ..."]}'
    )
    user_msg = (
        f"=== SKILL UNDER REVIEW ===\n{(skill_md or '')[:5000]}\n\n"
        f"=== OTHER SKILLS IN LIBRARY ===\n{catalog[:5000]}\n\n"
        "Decide if this skill's retrieval metadata should be narrowed so it only "
        "fires for its intended scenario and not for adjacent skills above."
    )
    try:
        raw = await llm_call_async(
            url, model,
            [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=4096, headers=headers, timeout=90,
        )
    except Exception as e:
        logger.warning(f"Retrieval precision check failed: {e}")
        return None
    text = _re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', (raw or ''), flags=_re.I)
    text = _re.sub(r'<think(?:ing)?>[\s\S]*$', '', text, flags=_re.I).strip()
    data = None
    a, b = text.find('{'), text.rfind('}')
    if a >= 0 and b > a:
        frag = text[a:b + 1]
        for cand in (frag, _re.sub(r',(\s*[}\]])', r'\1', frag)):
            try:
                data = _json.loads(cand)
                break
            except Exception:
                continue
    if not isinstance(data, dict) or "ok" not in data:
        return None
    return {
        "ok": bool(data.get("ok")),
        "summary": str(data.get("summary", ""))[:300],
        "issues": [str(x)[:220] for x in (data.get("issues") or []) if str(x).strip()][:6],
    }


# In-memory skill-test jobs, keyed by (owner, skill_name). Runs server-side so
# the test survives the modal being closed; the UI polls /test-status. (Not
# persisted across restart — it's a "come back in a bit" convenience.)
_skill_test_jobs: dict = {}


async def _run_skill_test_job(key, name, md, task, url, model, headers, owner, skills_manager=None):
    """Background coroutine: run the skill in an agent loop, capture a condensed
    log + transcript, then have the judge grade it. Writes into _skill_test_jobs."""
    import json as _json
    from src.agent_loop import stream_agent_loop

    job = _skill_test_jobs.get(key)
    if job is None:
        return
    log = job["log"]
    transcript = []
    say_buf = []

    def _flush_say():
        if say_buf:
            log.append({"type": "say", "text": "".join(say_buf)})
            say_buf.clear()

    messages = [
        {"role": "system", "content":
            "You are TESTING a skill. Below is a reusable skill (a procedure). Follow it "
            "to complete the user's task for real, using your available tools, step by "
            "step. If the skill is wrong, unclear, or references tools that don't exist, "
            "do your best — the problems will be reviewed afterward.\n\n=== SKILL ===\n" + md},
        {"role": "user", "content": task},
    ]
    try:
        async for chunk in stream_agent_loop(
            url, model, messages, headers=headers,
            temperature=0.3, max_tokens=0, max_rounds=8, owner=owner,
        ):
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
                cmd = str(d.get("command") or d.get("args") or "")[:300]
                log.append({"type": "tool_start", "tool": d.get("tool"), "command": cmd})
                transcript.append(f"\n[tool {d.get('tool')}] {cmd}\n")
            elif d.get("type") == "tool_output":
                _flush_say()
                out = str(d.get("output") or "")[:600]
                log.append({"type": "tool_output", "output": out})
                transcript.append(f"[output] {out}\n")
            elif d.get("type") == "agent_step":
                _flush_say()
                log.append({"type": "agent_step", "round": d.get("round")})
                transcript.append(f"\n--- round {d.get('round')} ---\n")
            if len(log) > 600:
                del log[0:len(log) - 600]
        _flush_say()
    except Exception as e:
        _flush_say()
        log.append({"type": "error", "error": str(e)})

    log.append({"type": "evaluating"})
    try:
        job["verdict"] = await _eval_skill_run(md, task, "".join(transcript), url, model, headers)
    except Exception as e:
        job["verdict"] = {"verdict": "unknown", "confidence": 0, "summary": f"Eval failed: {e}", "issues": []}
    # Record the result so the card shows a 'verified' check (a manual test
    # never involves the teacher) and nudge the confidence score to match the
    # verdict — same scale as Audit-all's pass=0.95. inconclusive/unknown leave
    # the score alone (missing-fixture or parse failures shouldn't punish it).
    if skills_manager is not None:
        v = (job["verdict"] or {}).get("verdict") or "unknown"
        try:
            skills_manager.set_audit(name, v, by_teacher=False, worker_model=model)
        except Exception:
            pass
        conf = {"pass": 0.95, "needs_work": 0.6, "fail": 0.4}.get(v)
        if conf is not None:
            try:
                skills_manager.update_skill(name, {"confidence": conf}, owner=owner)
            except Exception:
                pass
    job["status"] = "done"


# ── Autonomous skill audit: test → judge → self-edit → retry → teacher → flag ──
_skill_audit_jobs: dict = {}


def _audit_auto_publish_policy(owner) -> tuple[bool, float]:
    """Return (auto_publish_enabled, minimum_confidence) for audit finalization."""
    try:
        from routes.prefs_routes import _load_for_user
        prefs = _load_for_user(owner) or {}
    except Exception:
        prefs = {}
    try:
        from src.settings import get_setting
        default_min = get_setting("skill_autosave_min_confidence", 0.85)
    except Exception:
        default_min = 0.85
    enabled = bool(prefs.get("auto_approve_skills", True))
    try:
        min_conf = float(prefs.get("skill_min_confidence", default_min))
    except (TypeError, ValueError):
        min_conf = 0.85
    return enabled, max(0.0, min(1.0, min_conf))


def _skill_duplicate_blocker(skills_manager, name: str, owner) -> Optional[str]:
    """Cheap duplicate guard matching the UI's duplicate grouping.

    The LLM necessity check catches semantic redundancy, but the UI also has a
    cheap similarity pass. Use the same broad signal before auto-publishing so
    a high-scoring lower-priority duplicate stays draft.
    """
    import re as _re

    def _tokens(sk: dict) -> set[str]:
        text = " ".join([
            str(sk.get("name") or ""),
            str(sk.get("description") or ""),
            str(sk.get("when_to_use") or ""),
            " ".join(sk.get("procedure") or []),
            " ".join(sk.get("tags") or []),
        ]).lower()
        text = _re.sub(r"-\d+\b", "", text)
        return {
            t for t in _re.split(r"[^a-z0-9]+", text)
            if len(t) > 2 and t not in {"the", "and", "with", "for", "from", "using"}
        }

    def _sim(a: dict, b: dict) -> float:
        A, B = _tokens(a), _tokens(b)
        if not A or not B:
            return 0.0
        return len(A & B) / max(1, len(A | B))

    def _base(n: str) -> str:
        return _re.sub(r"-\d+$", "", str(n or ""))

    def _score(sk: dict) -> float:
        return (
            (100000 if (sk.get("status") == "published") else 0)
            + int(sk.get("uses") or 0) * 100
            + round(float(sk.get("confidence") or 0) * 100)
            + (-5 if sk.get("audit_by_teacher") else 0)
            - (len(str(sk.get("name") or "")) / 1000)
        )

    skills = skills_manager.load(owner=owner)
    current = next((s for s in skills if (s.get("name") or s.get("id")) == name), None)
    if not current:
        return None
    duplicates = []
    cur_name = current.get("name") or current.get("id") or name
    for other in skills:
        other_name = other.get("name") or other.get("id")
        if not other_name or other_name == cur_name:
            continue
        if _base(cur_name) == _base(other_name) or _sim(current, other) >= 0.38:
            duplicates.append(other)
    if not duplicates:
        return None
    keeper = sorted([current, *duplicates], key=_score, reverse=True)[0]
    keeper_name = keeper.get("name") or keeper.get("id") or ""
    if keeper_name and keeper_name != cur_name:
        try:
            skills_manager.set_necessity(
                cur_name,
                False,
                [keeper_name],
                f"Lower-priority duplicate of {keeper_name}",
            )
        except Exception:
            pass
        return keeper_name
    return None


def _audit_flag_text(*parts) -> str:
    text_parts = []
    for part in parts:
        if isinstance(part, dict):
            text_parts.extend(str(v or "") for v in part.values())
        elif isinstance(part, (list, tuple, set)):
            text_parts.extend(str(v or "") for v in part)
        else:
            text_parts.append(str(part or ""))
    return " ".join(text_parts).lower()


def _audit_generic_blocker(skill: Optional[dict], necessity: Optional[dict],
                           verdict_data: Optional[dict]) -> Optional[str]:
    """Return a short reason when a generic/trivial skill must stay draft."""
    generic_re = re.compile(
        r"\b(too[-\s]?generic|generic|trivial|capable assistant|without a saved|"
        r"not need|unnecessary|irrelevant)\b",
        re.I,
    )
    if isinstance(necessity, dict):
        reason = str(necessity.get("reason") or "")
        if necessity.get("necessary") is False and generic_re.search(reason):
            return reason or "Generic or unnecessary skill"

    if isinstance(skill, dict):
        tag_text = _audit_flag_text(skill.get("tags") or [])
        if generic_re.search(tag_text):
            return "Skill is tagged generic"

    if isinstance(verdict_data, dict):
        verdict_text = _audit_flag_text(
            verdict_data.get("summary"),
            verdict_data.get("issues") or [],
        )
        if generic_re.search(verdict_text):
            return "Audit flagged the skill as generic or unnecessary"
    return None


def _audit_finalize_status(skills_manager, name: str, owner, verdict: str,
                           confidence: Optional[float], necessity: Optional[dict] = None,
                           verdict_data: Optional[dict] = None) -> str:
    """Apply the user's audit publishing policy.

    Audit is the final pass: skills that pass at/above the threshold are
    published; anything below threshold, inconclusive, failing, or marked
    unnecessary/redundant is returned to draft. This intentionally demotes a
    previously-published skill when a fresh audit no longer clears policy.
    """
    auto_publish, min_conf = _audit_auto_publish_policy(owner)
    necessary = True
    current = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
    generic_reason = _audit_generic_blocker(current, necessity, verdict_data)
    if isinstance(necessity, dict) and necessity.get("necessary") is False:
        necessary = False
    if generic_reason:
        necessary = False
        try:
            skills_manager.set_necessity(name, False, [], generic_reason)
        except Exception:
            pass
    duplicate_of = _skill_duplicate_blocker(skills_manager, name, owner) if verdict == "pass" else None
    if duplicate_of:
        necessary = False
    c = float(confidence or 0.0)
    status = "published" if (auto_publish and necessary and verdict == "pass" and c >= min_conf) else "draft"
    try:
        skills_manager.update_skill(name, {"status": status}, owner=owner)
    except Exception:
        pass
    return status


def _apply_skill_md(skills_manager, name: str, md: str, owner) -> bool:
    """Parse + persist an edited SKILL.md. Returns True on success."""
    try:
        from services.memory.skill_format import Skill, slugify
        sk = Skill.from_markdown(md)
        # Pin the identity: the audit's fixer is now allowed to edit frontmatter
        # (tags/category/when_to_use/description), but it must NEVER rename the
        # skill — a changed `name` would move the dir and orphan the usage/audit
        # sidecar entries that the caller keeps writing under the original name.
        sk.name = name
        return bool(skills_manager.update_skill(name, {
            "name": sk.name, "description": sk.description, "version": sk.version,
            "category": sk.category, "tags": sk.tags, "platforms": sk.platforms,
            "requires_toolsets": sk.requires_toolsets, "fallback_for_toolsets": sk.fallback_for_toolsets,
            "status": sk.status, "confidence": sk.confidence, "source": sk.source,
            "teacher_model": sk.teacher_model, "owner": sk.owner or owner,
            "when_to_use": sk.when_to_use, "procedure": sk.procedure,
            "pitfalls": sk.pitfalls, "verification": sk.verification, "body_extra": sk.body_extra,
        }, owner=owner))
    except Exception as e:
        logger.warning(f"Audit: could not save edited skill {name}: {e}")
        return False


async def _run_skill_test_once(md: str, task: str, url, model, headers, owner) -> tuple:
    """Run the skill once in the agent loop; return (transcript, verdict)."""
    import json as _json
    from src.agent_loop import stream_agent_loop
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


async def _improve_skill_md(skill_md: str, verdict: dict, transcript: str, url, model, headers):
    """Have a model rewrite SKILL.md to fix the reviewer's issues. Returns the
    corrected markdown, or None if it couldn't produce a usable change."""
    import re as _re
    from src.llm_core import llm_call_async
    issues = "\n".join("- " + str(i) for i in (verdict.get("issues") or []))
    sys_prompt = (
        "You are improving a reusable AI SKILL written in Markdown (frontmatter + body). "
        "A QA reviewer found problems after a test run. Rewrite the SKILL.md to fix them: "
        "make vague steps concrete, correct or remove references to tools that don't exist, "
        "ensure the procedure is reproducible. "
        "Keep the `name` field EXACTLY as-is (it is the skill's identity / filename). You MAY "
        "correct the OTHER frontmatter — tags, category, when_to_use, description — when the "
        "reviewer flagged them (issues prefixed 'metadata:') or they don't match the body; keep "
        "retrieval metadata narrow: remove broad tags that would over-select the skill, and make "
        "`when_to_use` say when NOT to use the skill if adjacent tasks are easy to confuse. Keep "
        "valid frontmatter structure. Do NOT invent capabilities the agent lacks. Reason in "
        "<think></think> first if needed, then output ONLY the full corrected SKILL.md (no "
        "fences, no commentary)."
    )
    user_msg = (
        f"=== CURRENT SKILL.md ===\n{skill_md}\n\n"
        f"=== REVIEWER VERDICT ===\n{verdict.get('summary', '')}\nIssues:\n{issues}\n\n"
        f"=== TEST TRANSCRIPT ===\n{(transcript or '')[:6000]}"
    )
    try:
        raw = await llm_call_async(url, model,
                                   [{"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": user_msg}],
                                   temperature=0.2, max_tokens=16384, headers=headers, timeout=180)
    except Exception as e:
        logger.warning(f"Audit: improve call failed: {e}")
        return None
    text = _re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', (raw or ''), flags=_re.I)
    text = _re.sub(r'<think(?:ing)?>[\s\S]*$', '', text, flags=_re.I)
    text = _re.sub(r'</think(?:ing)?>', '', text, flags=_re.I).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    # Some reasoning models still prepend analysis or echo the old skill before
    # the final corrected document. Keep only the last complete-looking
    # frontmatter document so reviewer prose never gets persisted into SKILL.md.
    starts = list(_re.finditer(r'(?m)^---\s*\n(?=[\s\S]*?^name\s*:)', text))
    if starts:
        text = text[starts[-1].start():].strip()
    return text or None


async def _audit_one_skill(skills_manager, skill, url, model, headers,
                           teacher, owner, log) -> dict:
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
        nec = await _eval_skill_necessity(md, others, url, model, headers)
        if nec is not None:
            skills_manager.set_necessity(name, nec.get("necessary", True),
                                         nec.get("redundant_with"), nec.get("reason"))
            if not nec.get("necessary", True):
                log(f"{name}: possibly unnecessary — {nec.get('reason', '')[:80]}")
    except Exception as e:
        log(f"{name}: necessity check skipped — {e}")

    generic_reason = _audit_generic_blocker(skill, nec, None)
    duplicate_of = _skill_duplicate_blocker(skills_manager, name, owner)
    if generic_reason or duplicate_of or (isinstance(nec, dict) and nec.get("necessary") is False):
        reason = generic_reason or (f"Lower-priority duplicate of {duplicate_of}" if duplicate_of else str((nec or {}).get("reason") or "Unnecessary skill"))
        try:
            skills_manager.update_skill(name, {"status": "draft", "confidence": 0.35}, owner=owner)
            skills_manager.set_audit(name, "skipped", by_teacher=False, worker_model=model)
            if duplicate_of:
                skills_manager.set_necessity(name, False, [duplicate_of], reason)
            else:
                skills_manager.set_necessity(name, False, [], reason)
        except Exception:
            pass
        log(f"{name}: draft — skipped functional test ({reason[:100]})")
        return {"skill": name, "result": "skipped", "reason": reason, "confidence": 0.35, "status": "draft"}

    # Retrieval precision check: if broad tags/trigger text would make this
    # narrow skill over-inject, fix only metadata before the functional test.
    try:
        if _should_check_retrieval_precision(skill):
            rp = await _eval_skill_retrieval_precision(md, others, url, model, headers)
            if rp and not rp.get("ok"):
                issues = rp.get("issues") or ["metadata: retrieval: narrow tags and when_to_use to the intended trigger"]
                log(f"{name}: narrowing retrieval metadata — {(rp.get('summary') or issues[0])[:80]}")
                fixed = await _improve_skill_md(md, {
                    "verdict": "pass",
                    "confidence": 1.0,
                    "summary": rp.get("summary") or "Retrieval metadata is too broad.",
                    "issues": issues,
                }, "Retrieval audit only: the procedure may work, but matching metadata is too broad.", url, model, headers)
                if fixed and fixed.strip() != md.strip() and _apply_skill_md(skills_manager, name, fixed, owner):
                    md = fixed
                    refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
                    if refreshed:
                        skill = refreshed
    except Exception as e:
        log(f"{name}: retrieval precision check skipped — {e}")

    task = _skill_test_task(skill)
    log(f"{name}: testing…")
    transcript, verdict = await _run_skill_test_once(md, task, url, model, headers, owner)
    v = verdict.get("verdict")
    log(f"{name}: verdict = {v} ({verdict.get('summary', '')[:80]})")
    if v == "pass":
        # Procedure works. If the reviewer still flagged metadata (tags/category/
        # when_to_use/description), do ONE fixer pass to correct the frontmatter
        # without re-testing — a metadata-only fix can't break a passing run.
        meta_issues = [i for i in (verdict.get("issues") or []) if str(i).lower().lstrip().startswith("metadata:")]
        if meta_issues:
            log(f"{name}: pass, but fixing {len(meta_issues)} metadata issue(s)…")
            fixed = await _improve_skill_md(md, verdict, transcript, url, model, headers)
            if fixed and fixed.strip() != md.strip():
                _apply_skill_md(skills_manager, name, fixed, owner)
        _set_conf(0.95)
        skills_manager.set_audit(name, "pass", by_teacher=False, worker_model=model)
        refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
        status = _audit_finalize_status(skills_manager, name, owner, "pass", 0.95, (refreshed or {}).get("necessity"), verdict)
        log(f"{name}: {status} — confidence 95%")
        return {"skill": name, "result": "pass", "verdict": verdict, "confidence": 0.95, "status": status}
    if v in ("unknown", "inconclusive"):
        skills_manager.set_audit(name, "inconclusive", by_teacher=False, worker_model=model)
        status = _audit_finalize_status(skills_manager, name, owner, "inconclusive", skill.get("confidence") or 0.0, skill.get("necessity"))
        log(f"{name}: {status} — inconclusive")
        return {"skill": name, "result": "inconclusive", "verdict": verdict, "status": status}

    # Self-edit + retry.
    log(f"{name}: self-editing to fix issues…")
    new_md = await _improve_skill_md(md, verdict, transcript, url, model, headers)
    if new_md and new_md.strip() != md.strip() and _apply_skill_md(skills_manager, name, new_md, owner):
        md = new_md
        transcript, verdict = await _run_skill_test_once(md, task, url, model, headers, owner)
        v = verdict.get("verdict")
        log(f"{name}: retry (self) = {v}")
        if v == "pass":
            _set_conf(0.85)
            skills_manager.set_audit(name, "pass", by_teacher=False, worker_model=model)
            refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
            status = _audit_finalize_status(skills_manager, name, owner, "pass", 0.85, (refreshed or {}).get("necessity"), verdict)
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
        t_md = await _improve_skill_md(md, verdict, transcript, t_url, t_model, t_headers)
        if t_md and t_md.strip() != md.strip() and _apply_skill_md(skills_manager, name, t_md, owner):
            md = t_md
        # Re-test with the STUDENT model (the model the skill runs under in use).
        transcript, verdict = await _run_skill_test_once(md, task, url, model, headers, owner)
        v = verdict.get("verdict")
        log(f"{name}: retry on student after teacher rewrite = {v}")
        if v == "pass":
            _set_conf(0.8)
            skills_manager.set_audit(name, "pass", by_teacher=True, worker_model=model, teacher_model=t_model)
            refreshed = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
            status = _audit_finalize_status(skills_manager, name, owner, "pass", 0.8, (refreshed or {}).get("necessity"), verdict)
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
    )
    log(f"{name}: flagged — confidence lowered, kept as draft for manual review")
    return {"skill": name, "result": "flagged", "verdict": verdict, "confidence": 0.35}


async def _run_audit_all_job(key, skills_manager, names, url, model, headers, teacher, owner):
    """Background: audit each named skill in sequence, recording progress."""
    import asyncio as _asyncio
    import time as _time

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


def _resolve_audit_models():
    """Resolve (url, model, headers, teacher) for an audit run from Settings.

    Worker = Utility model (falling back to Default, normalized to a served
    model id); teacher = the optional Settings → Teacher Model config. Shared
    by the manual /audit-all route and scheduled/event audits. Raises
    ValueError if no worker model.
    """
    from src.endpoint_resolver import resolve_endpoint
    url, model, headers = resolve_endpoint("utility")
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
                t_url, t_model, t_headers = _resolve_model(spec)
                if t_url and t_model:
                    teacher = (t_url, t_model, t_headers)
    except Exception as e:
        logger.warning(f"Audit teacher resolve failed: {e}")
    return url, model, headers, teacher


async def run_scheduled_skill_audit(skills_manager: SkillsManager,
                                    owner: Optional[str] = None,
                                    max_skills: int = 8) -> dict:
    """Nightly audit pass. Audits the LEAST-recently-audited skills first and
    caps the batch so it rotates through the library over successive nights
    instead of re-checking the same ones every run. Reuses the same job store
    the manual 'Audit all' button uses, so its progress shows in the UI too."""
    import time as _time

    key = (owner or "",)
    existing = _skill_audit_jobs.get(key)
    if existing and existing.get("status") == "running":
        logger.info("Scheduled skill audit skipped — a run is already active.")
        return {"status": "running", "skipped": True}

    try:
        url, model, headers, teacher = _resolve_audit_models()
    except ValueError as e:
        logger.info(f"Scheduled skill audit skipped — {e}")
        return {"status": "skipped", "reason": str(e)}

    skills = skills_manager.load(owner=owner)
    # Oldest-audited first (never-audited sort to the very front via -1), so each
    # night picks up where the last left off and we don't repeat fresh ones.
    skills.sort(key=lambda s: (s.get("audited_at") if s.get("audited_at") is not None else -1.0))
    names = [s.get("name") for s in skills if s.get("name")][:max(1, max_skills)]
    if not names:
        return {"status": "done", "total": 0}

    _skill_audit_jobs[key] = {
        "status": "running", "scope": "scheduled", "model": model,
        "teacher": teacher[1] if teacher else None,
        "total": len(names), "done": 0, "current": None,
        "results": [], "log": [f"Nightly audit of {len(names)} least-recently-checked skill(s) with {model}"
                               + (f"; teacher {teacher[1]}" if teacher else "")],
        "started": _time.time(), "cancel": False,
    }
    logger.info(f"Scheduled skill audit starting: {len(names)} skill(s) (owner={owner or 'all'})")
    await _run_audit_all_job(key, skills_manager, names, url, model, headers, teacher, owner)
    job = _skill_audit_jobs.get(key, {})
    return {"status": "done", "total": len(names), "results": job.get("results", [])}


def setup_skills_routes(skills_manager: SkillsManager) -> APIRouter:
    router = APIRouter(prefix="/api/skills", tags=["skills"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _verify_owner(skill: dict, user: Optional[str]):
        if user is None:
            return
        # SECURITY: strict check — previously `sk_owner and sk_owner != user`
        # let any user mutate/read a skill that happened to have no owner
        # field (legacy or un-stamped writes), since the truthiness guard
        # short-circuited the comparison. Treat missing owner as not-owned.
        if skill.get("owner") != user:
            raise HTTPException(404, "Skill not found")

    def _fire_skill_added(user: Optional[str]):
        try:
            from src.event_bus import fire_event
            fire_event("skill_added", user)
        except Exception:
            logger.debug("skill_added event dispatch failed", exc_info=True)

    @router.get("")
    async def list_skills(request: Request):
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        return {"skills": skills, "count": len(skills)}

    @router.get("/index")
    async def get_index(request: Request):
        """The lightweight `[{name, description, category}]` list that the
        agent's system prompt sees. Useful for the UI's "what does the model
        actually have access to?" view."""
        user = _owner(request)
        idx = skills_manager.index_for(owner=user)
        return {"index": idx, "count": len(idx)}

    @router.get("/builtin")
    async def list_builtin_skills(request: Request):
        """Read-only list of the agent's built-in tool capabilities (research,
        sessions, tasks, email, etc.) — the things it natively knows how to do.
        Surfaced so the Skills tab can show them in a separate "Built-in"
        section alongside the user's learned SKILL.md skills. Sourced from
        agent_loop.TOOL_SECTIONS (the same descriptions the model is given)."""
        import re

        def _clean(raw: str) -> str:
            s = raw or ""
            s = re.sub(r"```.*?```", "", s, flags=re.S)   # drop code fences (incl. inline ```name```)
            s = re.sub(r"\s+", " ", s).strip()
            s = re.sub(r"^[-–—:\s]+", "", s)              # drop leftover "- — " / ": " bullet prefix
            return s[:240]

        try:
            from src.agent_loop import TOOL_SECTIONS, get_builtin_overrides
        except Exception as e:
            return {"builtin": [], "count": 0, "error": str(e)}

        overrides = get_builtin_overrides()
        out = []
        for key, raw in TOOL_SECTIONS.items():
            names = key if isinstance(key, tuple) else (key,)
            for nm in names:
                if isinstance(nm, str):
                    overridden = nm in overrides
                    eff = overrides.get(nm, raw)
                    out.append({
                        "name": nm,
                        "description": _clean(eff),
                        "is_overridden": overridden,
                    })
        out.sort(key=lambda x: x["name"])
        return {"builtin": out, "count": len(out)}

    @router.get("/builtin/{name}")
    async def get_builtin_skill(name: str, request: Request):
        """Full text of a built-in tool's instruction block — the override
        if one is set, plus the shipped default (for the revert button)."""
        try:
            from src.agent_loop import TOOL_SECTIONS, get_builtin_overrides
        except Exception as e:
            raise HTTPException(500, str(e))
        default = None
        for key, raw in TOOL_SECTIONS.items():
            names = key if isinstance(key, tuple) else (key,)
            if name in names:
                default = raw
                break
        if default is None:
            raise HTTPException(404, f"No built-in tool named {name!r}")
        overrides = get_builtin_overrides()
        return {
            "name": name,
            "text": overrides.get(name, default),
            "default": default,
            "is_overridden": name in overrides,
        }

    @router.put("/builtin/{name}")
    async def set_builtin_override(name: str, request: Request):
        """Save a user override for a built-in tool's instruction block.
        WARNING surfaced in the UI — this changes how the assistant is
        told to use a native tool."""
        require_admin(request)
        from src.agent_loop import TOOL_SECTIONS
        valid = set()
        for key in TOOL_SECTIONS:
            valid.update(key if isinstance(key, tuple) else (key,))
        if name not in valid:
            raise HTTPException(404, f"No built-in tool named {name!r}")
        body = await request.json()
        text = (body or {}).get("text", "")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "text is required")
        from src.settings import get_setting, save_settings, load_settings
        settings = load_settings()
        ov = settings.get("builtin_tool_overrides")
        if not isinstance(ov, dict):
            ov = {}
        ov[name] = text
        settings["builtin_tool_overrides"] = ov
        save_settings(settings)
        return {"ok": True, "name": name, "is_overridden": True}

    @router.delete("/builtin/{name}")
    async def reset_builtin_override(name: str, request: Request):
        """Revert a built-in tool to its shipped instruction block."""
        require_admin(request)
        from src.settings import load_settings, save_settings
        settings = load_settings()
        ov = settings.get("builtin_tool_overrides")
        if isinstance(ov, dict) and name in ov:
            del ov[name]
            settings["builtin_tool_overrides"] = ov
            save_settings(settings)
        return {"ok": True, "name": name, "is_overridden": False}

    @router.post("/add")
    async def add_skill(request: Request, body: SkillAddRequest):
        user = _owner(request)
        entry = skills_manager.add_skill(
            # New shape
            name=body.name,
            description=body.description,
            category=body.category,
            tags=body.tags,
            platforms=body.platforms,
            requires_toolsets=body.requires_toolsets,
            fallback_for_toolsets=body.fallback_for_toolsets,
            when_to_use=body.when_to_use,
            procedure=body.procedure,
            pitfalls=body.pitfalls,
            verification=body.verification,
            status=body.status,
            version=body.version,
            confidence=body.confidence,
            source=body.source,
            teacher_model=body.teacher_model,
            session_id=body.session_id,
            owner=user,
            # Old shape (manager translates)
            title=body.title or "",
            problem=body.problem or "",
            solution=body.solution or "",
            steps=body.steps,
        )
        if not entry.get("_deduped"):
            _fire_skill_added(user)
        return {"ok": True, "deduped": bool(entry.get("_deduped")), "skill": entry}

    @router.get("/{skill_id}")
    async def get_skill(request: Request, skill_id: str):
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        for sk in skills:
            if sk.get("name") == skill_id or sk.get("id") == skill_id:
                return sk
        raise HTTPException(404, "Skill not found")

    @router.get("/{skill_id}/markdown")
    async def get_skill_markdown(request: Request, skill_id: str):
        """Return the raw SKILL.md text — used by the slash-invocation flow
        and the editor's 'view source' affordance."""
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
        if not match:
            raise HTTPException(404, "Skill not found")
        _verify_owner(match, user)
        md = skills_manager.read_skill_md(match.get("name"), owner=user)
        if md is None:
            raise HTTPException(404, "Skill source unavailable (legacy entry?)")
        return {"name": match.get("name"), "markdown": md}

    @router.post("/{skill_id}/test")
    async def test_skill(request: Request, skill_id: str):
        """Kick off a background skill test (agent run + LLM judge). Returns
        immediately; the run executes server-side so it survives the modal being
        closed. Poll GET /{skill_id}/test-status for progress + verdict.
        On completion it records the verdict and nudges the skill's confidence
        to match (pass→0.95, needs_work→0.6, fail→0.4; inconclusive/unknown leave
        it untouched). It never changes the skill's published/draft STATUS."""
        import time as _time
        import asyncio as _asyncio
        from src.endpoint_resolver import resolve_endpoint

        user = _owner(request)
        body = await request.json()
        task = (body.get("task") or "").strip()

        skills = skills_manager.load(owner=user)
        match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
        if not match:
            raise HTTPException(404, "Skill not found")
        _verify_owner(match, user)
        name = match.get("name")
        md = skills_manager.read_skill_md(name, owner=user) or ""

        if not task:
            task = _skill_test_task(match)

        # Prefer the configured DEFAULT (→ Utility) model — not the current chat
        # session's model. Fall back to the caller's session model only if unset.
        url, model, headers = resolve_endpoint("default")
        if not url or not model:
            url = url or ((body.get("endpoint_url") or "").strip() or None)
            model = model or ((body.get("model") or "").strip() or None)
            if headers is None and isinstance(body.get("headers"), dict):
                headers = body.get("headers")
        if not url or not model:
            raise HTTPException(400, "No model configured — set a Default or Utility model in Settings.")

        # Normalize against the endpoint's served models (avoids 404 model drift).
        try:
            from src.llm_core import list_model_ids
            _avail = list_model_ids(url, headers=headers)
            if _avail and model not in _avail:
                import os as _os
                _base = _os.path.basename((model or "").rstrip("/"))
                _match = next((a for a in _avail if _os.path.basename(a.rstrip("/")) == _base), None)
                model = _match or _avail[0]
        except Exception as _e:
            logger.warning(f"Skill-test model resolve failed: {_e}")

        key = (user or "", name)
        _skill_test_jobs[key] = {
            "status": "running",
            "task": task,
            "model": model,
            "skill": name,
            "started": _time.time(),
            "log": [{"type": "skill_test_start", "task": task, "skill": name, "model": model}],
            "verdict": None,
        }
        _asyncio.create_task(_run_skill_test_job(key, name, md, task, url, model, headers, user, skills_manager))
        return {"ok": True, "status": "running", "skill": name, "model": model}

    @router.get("/{skill_id}/test-status")
    async def test_skill_status(request: Request, skill_id: str):
        """Current background-test state for a skill (status / log / verdict)."""
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
        name = (match or {}).get("name", skill_id)
        job = _skill_test_jobs.get((user or "", name))
        if not job:
            return {"status": "none"}
        return {
            "status": job["status"],
            "task": job.get("task"),
            "model": job.get("model"),
            "log": job.get("log", []),
            "verdict": job.get("verdict"),
        }

    @router.post("/audit-all")
    async def audit_all_skills(request: Request):
        """Kick off a background audit of skills: each is tested + judged; if it
        needs work the model self-edits and retries; if a teacher model is
        configured it escalates; a skill that still fails is demoted to draft
        (never deleted). Poll GET /audit-status. Body:
        {scope: 'drafts'|'unchecked'|'all', names?: [...], skip_audited?: bool}. Default 'all'
        means every visible skill, including already-published skills, so audit
        can publish or demote according to the confidence threshold."""
        import asyncio as _asyncio
        import time as _time

        user = _owner(request)
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        scope = (body.get("scope") or "all").lower()
        requested_names = body.get("names")
        skip_audited = bool(body.get("skip_audited"))

        key = (user or "",)
        existing = _skill_audit_jobs.get(key)
        if existing and existing.get("status") == "running":
            return {
                "ok": True, "status": "running", "total": existing.get("total", 0),
                "done": existing.get("done", 0), "model": existing.get("model"),
            }

        # Worker model (Default, normalized) + optional teacher — shared resolver.
        try:
            url, model, headers, teacher = _resolve_audit_models()
        except ValueError as e:
            raise HTTPException(400, str(e))

        skills = skills_manager.load(owner=user)
        by_name = {s.get("name"): s for s in skills if s.get("name")}
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
                s.get("name") for s in skills
                if s.get("name") and (not skip_audited or not s.get("audit_verdict"))
            ]
        else:
            scope = "unchecked" if scope == "drafts" else scope
            names = [
                s.get("name") for s in skills
                if s.get("name")
                and (s.get("status") or "draft") != "published"
                and not s.get("audit_verdict")
            ]
        if not names:
            return {"ok": True, "status": "done", "total": 0, "results": [], "log": ["No skills to audit."]}

        _skill_audit_jobs[key] = {
            "status": "running", "scope": scope, "model": model,
            "teacher": teacher[1] if teacher else None,
            "total": len(names), "done": 0, "current": None,
            "results": [], "log": [f"Auditing {len(names)} skill(s) with {model}" + (f"; teacher {teacher[1]}" if teacher else "")],
            "started": _time.time(), "cancel": False,
        }
        task = _asyncio.create_task(_run_audit_all_job(key, skills_manager, names, url, model, headers, teacher, user))
        _skill_audit_jobs[key]["task"] = task
        return {"ok": True, "status": "running", "total": len(names), "model": model}

    @router.get("/audit-all/status")
    async def audit_status(request: Request):
        user = _owner(request)
        job = _skill_audit_jobs.get((user or "",))
        if not job:
            return {"status": "none"}
        return {
            "status": job["status"], "scope": job.get("scope"),
            "total": job.get("total", 0), "done": job.get("done", 0),
            "current": job.get("current"), "model": job.get("model"), "teacher": job.get("teacher"),
            "results": job.get("results", []), "log": job.get("log", []),
            "started": job.get("started"), "finished": job.get("finished"),
        }

    @router.post("/audit-all/cancel")
    async def audit_cancel(request: Request):
        user = _owner(request)
        job = _skill_audit_jobs.get((user or "",))
        if job:
            job["cancel"] = True
            job["status"] = "cancelled"
            job["current"] = None
            task = job.get("task")
            if task and not task.done():
                task.cancel()
        return {"ok": True, "status": "cancelled" if job else "none"}

    @router.post("/{skill_id}/markdown")
    async def save_skill_markdown(request: Request, skill_id: str):
        """Replace SKILL.md with new raw content. Parses + validates first."""
        from services.memory.skill_format import Skill, slugify
        user = _owner(request)
        body = await request.json()
        new_content = body.get("markdown")
        if not isinstance(new_content, str) or not new_content.strip():
            raise HTTPException(400, "markdown is required")
        skills = skills_manager.load(owner=user)
        match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
        if not match:
            raise HTTPException(404, "Skill not found")
        _verify_owner(match, user)
        try:
            sk = Skill.from_markdown(new_content)
        except Exception as e:
            raise HTTPException(400, f"Could not parse SKILL.md: {e}")
        sk.name = slugify(sk.name or match.get("name"))
        if not sk.owner:
            sk.owner = match.get("owner") or user
        ok = skills_manager.update_skill(match.get("name"), {
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
        }, owner=user)
        if not ok:
            raise HTTPException(500, "Update failed")
        # Manual markdown edits can create or substantially rewrite a draft
        # skill without going through /add. Treat unaudited saves as new audit
        # candidates so the event-driven Skills Audit pipeline still runs.
        if not match.get("audit_verdict"):
            _fire_skill_added(user)
        return {"ok": True, "name": sk.name}

    @router.put("/{skill_id}")
    async def update_skill(request: Request, skill_id: str, body: SkillUpdateRequest):
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
        if not match:
            raise HTTPException(404, "Skill not found")
        _verify_owner(match, user)

        updates = body.dict(exclude_none=True)
        if not updates:
            return {"ok": True}
        ok = skills_manager.update_skill(match.get("name"), updates, owner=user)
        if not ok:
            raise HTTPException(404, "Skill not found")
        if not match.get("audit_verdict"):
            _fire_skill_added(user)
        return {"ok": True}

    @router.delete("/{skill_id}")
    async def delete_skill(request: Request, skill_id: str):
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        match = next((s for s in skills if s.get("name") == skill_id or s.get("id") == skill_id), None)
        if not match:
            raise HTTPException(404, "Skill not found")
        _verify_owner(match, user)
        ok = skills_manager.delete_skill(match.get("name"), owner=user)
        if not ok:
            raise HTTPException(404, "Skill not found")
        return {"ok": True}

    @router.post("/search")
    async def search_skills(request: Request):
        body = await request.json()
        query = body.get("query", "")
        if not query.strip():
            raise HTTPException(400, "query is required")
        user = _owner(request)
        skills = skills_manager.load(owner=user)
        results = skills_manager.get_relevant_skills(query, skills, max_items=10)
        return {"skills": results, "query": query, "count": len(results)}

    return router
