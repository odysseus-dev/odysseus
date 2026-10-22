"""Model grading and advisory skill assessment with explicit offline call seams."""

import logging
import re
from typing import Optional

from src.text_helpers import strip_closed_think_blocks
from src.utility_effort import effort_for_call

logger = logging.getLogger(__name__)

# Last-resort verdict extraction from a teacher/verifier model's prose (run when
# JSON parsing fails). `["\'\s:]*` already consumes whitespace, so the original
# trailing `\s*` made two adjacent \s-matching quantifiers that backtrack O(n^2)
# on a `verdict` + whitespace flood in untrusted model output (CodeQL
# py/polynomial-redos). Without it a single unbounded quantifier remains — the
# matched text is identical, and the scan is linear.
_VERDICT_PROSE_RE = re.compile(
    r'verdict["\'\s:]*["\']?(pass|needs_work|fail|inconclusive)', re.I
)


def verdict_efficiency(verdict: Optional[dict]) -> dict:
    if not isinstance(verdict, dict):
        return {}
    out = {"audit_summary": str(verdict.get("summary") or "")[:2000]}
    for key in ("saved_turns", "saved_tool_calls"):
        if key not in verdict:
            continue
        try:
            out[key] = int(verdict.get(key))
        except (TypeError, ValueError):
            pass
    baseline = str(verdict.get("baseline_verdict") or "").lower().strip()
    if baseline in {"better", "same", "worse", "unknown"}:
        out["baseline_verdict"] = baseline
    if "usefulness" in verdict:
        try:
            out["usefulness"] = max(0.0, min(1.0, float(verdict.get("usefulness"))))
        except (TypeError, ValueError):
            pass
    return out


def parse_run_verdict(raw: str):
    """Return a final result dict on success, or None if unparseable."""
    import json as _json
    import re as _re
    _VERDICTS = ("pass", "needs_work", "fail", "inconclusive")

    text = (raw or '')
    # Strip closed think blocks. If a <think> was opened but never closed
    # (the model ran out of budget mid-reasoning), drop everything from it
    # onward so its stray braces don't poison JSON extraction.
    text = strip_closed_think_blocks(text)
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
        km = _VERDICT_PROSE_RE.search(text)
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
    try:
        usefulness = float(data.get("usefulness", 0.0))
    except (TypeError, ValueError):
        usefulness = 0.0
    try:
        saved_turns = int(data.get("saved_turns", 0))
    except (TypeError, ValueError):
        saved_turns = 0
    try:
        saved_tool_calls = int(data.get("saved_tool_calls", 0))
    except (TypeError, ValueError):
        saved_tool_calls = 0
    return {
        "verdict": v,
        "confidence": max(0.0, min(1.0, conf)),
        "summary": str(data.get("summary", ""))[:400],
        "issues": [str(x)[:200] for x in (data.get("issues") or []) if str(x).strip()][:8],
        "baseline_verdict": (
            str(data.get("baseline_verdict", "unknown")).lower().strip()
            if str(data.get("baseline_verdict", "unknown")).lower().strip() in {"better", "same", "worse", "unknown"}
            else "unknown"
        ),
        "usefulness": max(0.0, min(1.0, usefulness)),
        "saved_turns": saved_turns,
        "saved_tool_calls": saved_tool_calls,
    }


async def evaluate_run(skill_md: str, task: str, transcript: str,
                          url: str, model: str, headers: Optional[dict],
                          baseline_transcript: str = "",
                          skill_stats: Optional[dict] = None,
                          baseline_stats: Optional[dict] = None,
                          workload: str = "foreground", owner: Optional[str] = None,
                          *, _llm_call=None, _effort_for_call=None) -> dict:
    """LLM-as-judge: grade a skill test run from its transcript. Advisory only.

    Robust against local reasoning models (strips <think>, lenient JSON,
    generous token budget) — same defensive parsing used elsewhere.
    """
    if _llm_call is None:
        from src.llm_core import llm_call_async
        _llm_call = llm_call_async
    _effort_for_call = _effort_for_call or effort_for_call

    sys_prompt = (
        "You are a strict QA reviewer judging whether an AI 'skill' (a reusable "
        "procedure) actually works. You are given the SKILL, the TASK it was tested "
        "on, and the TRANSCRIPT of the agent's run.\n\n"
        "Judge honestly:\n"
        "- The functional verdict judges ONLY whether the SKILL RUN completed the "
        "task correctly and whether the skill procedure is usable. A correct skill "
        "run is pass even when the baseline is equally good. Record comparative "
        "value separately in baseline_verdict/usefulness.\n"
        "- Did following the skill accomplish the task accurately?\n"
        "- Are the steps clear, correct, and reproducible?\n"
        "- Did it reference tools/commands that don't exist or that errored?\n"
        "- Separately, compared with the baseline run WITHOUT the skill, did the skill make "
        "the agent more accurate, use fewer turns/tool calls, or avoid avoidable "
        "thrashing?\n"
        "- Do NOT penalize a skill merely for being broad or generic. A broad "
        "skill is useful if it makes the agent finish correctly in fewer turns "
        "or with fewer tools than baseline.\n"
        "- METADATA: do the frontmatter fields match what the skill actually does? "
        "Flag wrong/misleading/missing tags, a wrong category, a when_to_use that "
        "doesn't describe the real trigger, or a description that oversells or "
        "mismatches the body. List each metadata problem in 'issues' (prefix it "
        "with 'metadata:'). Metadata problems alone do NOT make the verdict 'fail' "
        "if the procedure works — note them as issues on an otherwise-passing run.\n\n"
        "Never use inconclusive merely because the baseline was the same or better. "
        "IMPORTANT — fairness rule: if the SKILL RUN could NOT proceed because it lacked "
        "an input or target the test never provided (e.g. there was no document/"
        "email/data to act on, so the agent reasonably asked for it), that is NOT "
        "the skill's fault. Return verdict \"inconclusive\" — do NOT mark it fail "
        "or needs_work. Only judge the skill's PROCEDURE; reserve fail/needs_work "
        "for when the steps themselves are wrong, vague, or reference missing tools.\n\n"
        "If you need to reason, do it inside <think></think> FIRST. Then output "
        "ONLY this JSON (no fences):\n"
        'Set baseline_verdict to how the SKILL RUN compares to the BASELINE RUN.\n\n'
        '{"verdict": "pass" | "needs_work" | "fail" | "inconclusive", '
        '"confidence": 0.0-1.0, "summary": "one short sentence", '
        '"issues": ["short issue", ...], '
        '"baseline_verdict": "better" | "same" | "worse" | "unknown", '
        '"usefulness": 0.0-1.0, "saved_turns": integer, "saved_tool_calls": integer}'
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
    skill_stats = skill_stats or {}
    baseline_stats = baseline_stats or {}
    user_msg = (
        f"=== SKILL ===\n{(skill_md or '')[:4000]}\n\n"
        f"=== TASK ===\n{task}\n\n"
        f"=== SKILL RUN STATS ===\n"
        f"turns={skill_stats.get('turns', 'unknown')} "
        f"tool_calls={skill_stats.get('tool_calls', 'unknown')}\n\n"
        f"=== SKILL RUN TRANSCRIPT ===\n{_clip(transcript)}\n\n"
        f"=== BASELINE RUN WITHOUT SKILL STATS ===\n"
        f"turns={baseline_stats.get('turns', 'unknown')} "
        f"tool_calls={baseline_stats.get('tool_calls', 'unknown')}\n\n"
        f"=== BASELINE RUN WITHOUT SKILL TRANSCRIPT ===\n{_clip(baseline_transcript)}"
    )

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
            raw = await _llm_call(
                # Generous budget so a heavy reasoner can think AND still have
                # room to emit the JSON afterwards (reasoning tokens come out of
                # this same cap; the server clamps to its own max).
                url, model, msgs,
                temperature=0.1, max_tokens=32768, headers=headers, timeout=180,
                workload=workload,
                reasoning_effort=_effort_for_call(url, model, owner),
            )
        except Exception as e:
            # Don't give up on a transient first-attempt error — let the second
            # (no-think) attempt run before reporting failure.
            last_err = e
            continue
        last_text = (raw or '')
        parsed = parse_run_verdict(raw)
        if parsed is not None:
            return parsed

    if last_err is not None and not last_text:
        return {"verdict": "unknown", "confidence": 0, "summary": f"Review call failed: {last_err}", "issues": []}
    return {"verdict": "unknown", "confidence": 0,
            "summary": "Reviewer returned unparseable output.", "issues": [], "raw": last_text[:300]}


async def evaluate_necessity(skill_md: str, others: list, url: str, model: str,
                                headers: Optional[dict],
                                workload: str = "foreground", owner: Optional[str] = None,
                          *, _llm_call=None, _effort_for_call=None) -> Optional[dict]:
    """Advisory judge: is this skill worth keeping, or is it redundant / trivially
    unnecessary? Sees the OTHER skills' names+descriptions so it can spot
    duplicates. Returns {necessary, redundant_with, reason} or None. Never acts —
    purely a flag the UI surfaces."""
    import json as _json
    import re as _re
    if _llm_call is None:
        from src.llm_core import llm_call_async
        _llm_call = llm_call_async
    _effort_for_call = _effort_for_call or effort_for_call

    catalog = "\n".join(f"- {o.get('name')}: {o.get('description', '')}" for o in others) or "(no other skills)"
    sys_prompt = (
        "You assess whether a reusable AI 'skill' (a saved procedure) is worth keeping. "
        "A skill is UNNECESSARY if it essentially duplicates another skill in the library. "
        "Do NOT call a skill unnecessary merely because it is broad or generic; broad "
        "skills can be worth keeping when they make a capable assistant finish accurately "
        "with fewer turns or fewer tool calls than it would without the skill. A skill IS "
        "necessary if it captures a reusable trigger, procedure, tool sequence, or "
        "hard-won detail that could improve future runs.\n\n"
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
        raw = await _llm_call(
            url, model,
            [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=8192, headers=headers, timeout=120,
            workload=workload,
            reasoning_effort=_effort_for_call(url, model, owner),
        )
    except Exception as e:
        logger.warning(f"Necessity check failed: {e}")
        return None
    text = strip_closed_think_blocks(raw or '')
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


def should_check_retrieval_precision(skill: dict) -> bool:
    """Cheap prefilter for the expensive retrieval-precision judge.

    Skills with broad tags like "network" or "document" are the ones most likely
    to over-inject. Narrow command/vendor tags alone are fine.
    """
    broad = {
        "arch", "arch linux", "linux", "network", "networking", "wifi",
        "installation", "install", "system", "ssh", "document", "documents",
        "search", "email", "calendar", "gpu", "server", "python",
    }
    if not isinstance(skill, dict):
        return False
    tags = {str(t or "").strip().lower() for t in (skill.get("tags") or [])}
    if tags & broad:
        return True
    text = " ".join([
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("when_to_use") or ""),
    ]).lower()
    return sum(1 for t in broad if t in text) >= 2


async def evaluate_retrieval_precision(skill_md: str, others: list,
                                          url: str, model: str,
                                          headers: Optional[dict],
                                          workload: str = "foreground", owner: Optional[str] = None,
                          *, _llm_call=None, _effort_for_call=None) -> Optional[dict]:
    """Advisory judge: would this skill's metadata make retrieval over-select it?

    This is distinct from "does the procedure work?". It asks whether tags,
    description, and when_to_use are specific enough that the skill won't be
    injected into adjacent but wrong tasks.
    """
    import json as _json
    import re as _re
    if _llm_call is None:
        from src.llm_core import llm_call_async
        _llm_call = llm_call_async
    _effort_for_call = _effort_for_call or effort_for_call

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
        raw = await _llm_call(
            url, model,
            [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=4096, headers=headers, timeout=90,
            workload=workload,
            reasoning_effort=_effort_for_call(url, model, owner),
        )
    except Exception as e:
        logger.warning(f"Retrieval precision check failed: {e}")
        return None
    text = strip_closed_think_blocks(raw or '')
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


async def improve_markdown(skill_md: str, verdict: dict, transcript: str, url, model, headers,
                            workload: str = "foreground", owner: Optional[str] = None,
                          *, _llm_call=None, _effort_for_call=None):
    """Have a model rewrite SKILL.md to fix the reviewer's issues. Returns the
    corrected markdown, or None if it couldn't produce a usable change."""
    import re as _re
    if _llm_call is None:
        from src.llm_core import llm_call_async
        _llm_call = llm_call_async
    _effort_for_call = _effort_for_call or effort_for_call
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
        raw = await _llm_call(url, model,
                                   [{"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": user_msg}],
                                   temperature=0.2, max_tokens=16384, headers=headers, timeout=180,
                                   workload=workload,
                                   reasoning_effort=_effort_for_call(url, model, owner))
    except Exception as e:
        logger.warning(f"Audit: improve call failed: {e}")
        return None
    text = strip_closed_think_blocks(raw or '')
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
