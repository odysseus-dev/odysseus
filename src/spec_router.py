"""Odysseus spec: small-model tool router (JEFF-style) for the agent harness.

The router (a 0.8B decision model served by jeff-serve) reads the user's
message and scores offered tools in one forward pass. The harness may narrow
the existing turn contract, but cannot add authority or drop its obligations.
On an error, timeout or low-confidence answer it retains the existing contract.

Env:
  ODYSSEUS_SPEC_ROUTER_URL    jeff-serve base URL, e.g. http://ajax:8765  (unset = disabled)
  ODYSSEUS_SPEC_ROUTER_MODEL  adapter name (default: odysseus-s60)
  ODYSSEUS_SPEC_ROUTER_TIMEOUT seconds (default: 0.5)
  ODYSSEUS_SPEC_ROUTER_MIN_CONF minimum top-1 probability to act (default: 0.3)
  ODYSSEUS_SPEC_ROUTER_SECOND minimum 2nd-choice probability to also add it (default: 0.25)
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import asyncio
import urllib.request
import urllib.error
from contextvars import ContextVar
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

_HEAD = {
    "answer_directly": "No tool is needed: answer the user directly from the conversation",
    "ask_user": "A tool is needed but required information is missing: ask the user first",
}
_INSTRUCTIONS = (
    "Which tool should the agent call next to handle the user's latest message? "
    "Use the conversation for context. If no tool is needed, choose answer directly. "
    "If a tool is needed but information it requires is missing, choose ask the user."
)
_AGENT = "Odysseus, a personal tool-using assistant."
_LAST_DECISION: ContextVar[Optional[Dict[str, Any]]] = ContextVar('spec_router_decision', default=None)


def latest_decision() -> Optional[Dict[str, Any]]:
    """Return the router decision in the current request context."""
    return _LAST_DECISION.get()


def clear_decision() -> None:
    _LAST_DECISION.set(None)
_MCP_BROWSER_TOOLS = (
    "mcp__builtin_browser__browser_navigate",
    "mcp__builtin_browser__browser_snapshot",
    "mcp__builtin_browser__browser_click",
    "mcp__builtin_browser__browser_type",
    "mcp__builtin_browser__browser_press_key",
    "mcp__builtin_browser__browser_wait_for",
    "mcp__builtin_browser__browser_take_screenshot",
)


def _fn(schema: Dict[str, Any]) -> Dict[str, Any]:
    return schema.get("function") if isinstance(schema.get("function"), dict) else schema


def tool_line(schema: Dict[str, Any]) -> str:
    fn = _fn(schema)
    name = str(fn.get("name") or "")
    props = ((fn.get("parameters") or {}).get("properties") or {})
    if os.getenv("ODYSSEUS_SPEC_TOOL_FORMAT", "").strip().lower() == "names":
        return name + "(" + ", ".join(props) + ")"  # names + parameters only (spec_v1n adapters are trained on this)
    desc = " ".join(str(fn.get("description") or "").split())
    if len(desc) > 220:
        desc = desc[:217] + "..."
    return f"{name}({', '.join(props)}): {desc}"


def prior_tool_names(messages: Optional[Iterable[Dict[str, Any]]]) -> Set[str]:
    """Tools already called in this conversation (kept offered: no 'forgetting')."""
    seen: Set[str] = set()
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            name = (tc.get("function") or tc).get("name") if isinstance(tc, dict) else None
            if name:
                seen.add(str(name))
    return seen


def _conversation(messages: Optional[Iterable[Dict[str, Any]]], limit: int = 4) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in list(messages or []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
            out.append({"role": m["role"], "content": m["content"][:600]})
    # the last user message is passed separately as user_message
    if out and out[-1]["role"] == "user":
        out = out[:-1]
    return out[-limit:]


def enabled() -> bool:
    return bool(os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip())


def spec_route_tools(
    user_message: str,
    schemas: Iterable[Dict[str, Any]],
    disabled_tools: Optional[Set[str]] = None,
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    return_decision: bool = False,
):
    """Return tool names the router wants offered (plus tools already used).

    With return_decision=True returns (tools, decided) where decided is True when
    the router answered confidently (a tool pick, or answer_directly)."""
    added: Set[str] = set(prior_tool_names(messages))
    decided = False
    clear_decision()
    url = os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip().rstrip("/")
    if not url or not (user_message or "").strip():
        return (added, False) if return_decision else added
    t0 = time.time()
    decision: Dict[str, Any] = {"ts": time.time(), "message": user_message[:160], "sticky": sorted(added)}
    try:
        disabled = set(disabled_tools or ())
        lines: Dict[str, str] = {}
        for s in schemas:
            n = str(_fn(s).get("name") or "")
            if n and n not in disabled and n != "ask_user" and n not in lines:
                lines[n] = tool_line(s)
        names = sorted(lines)
        criteria = dict(_HEAD)
        for i, n in enumerate(names, 1):
            criteria[f"t{i}"] = lines[n]
        req = {
            "model": os.getenv("ODYSSEUS_SPEC_ROUTER_MODEL", "odysseus-s60"),
            "state": {"agent": _AGENT, "conversation": _conversation(messages), "user_message": user_message},
            "questions": {"tool": {"type": "choice", "instructions": _INSTRUCTIONS, "criteria": criteria}},
        }
        http = urllib.request.Request(
            url + "/v1/systemone", data=json.dumps(req).encode(), headers={"content-type": "application/json"}
        )
        timeout = float(os.getenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "0.5"))
        deadline = time.time() + timeout
        while True:
            try:
                out = json.load(urllib.request.urlopen(http, timeout=max(0.2, deadline - time.time())))["answers"]["tool"]
                break
            except urllib.error.HTTPError as busy:
                # 529 = router busy with another request; retry within budget.
                if busy.code != 529 or time.time() + 0.15 >= deadline:
                    raise
                decision["retries"] = decision.get("retries", 0) + 1
                time.sleep(0.1)
        probs = out.get("probabilities") or {}
        choice = out.get("choice")
        if not isinstance(probs, dict) or choice not in criteria:
            raise ValueError("invalid router choice")
        for key, probability in probs.items():
            if key not in criteria or isinstance(probability, bool):
                raise ValueError("invalid router probability key")
            number = float(probability)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError("invalid router probability")
        probs = {key: float(value) for key, value in probs.items()}
        min_conf = float(os.getenv("ODYSSEUS_SPEC_ROUTER_MIN_CONF", "0.3"))
        second_min = float(os.getenv("ODYSSEUS_SPEC_ROUTER_SECOND", "0.25"))
        ranked = sorted(
            ((p, k) for k, p in probs.items() if k.startswith("t") and k[1:].isdigit()), reverse=True
        )
        picks: List[str] = []
        if choice and choice.startswith("t") and choice[1:].isdigit() and float(probs.get(choice, 0.0)) >= min_conf:
            picks.append(names[int(choice[1:]) - 1])
            for p, k in ranked[:3]:
                nm = names[int(k[1:]) - 1]
                if nm not in picks and p >= second_min:
                    picks.append(nm)
        _bmode = os.getenv("ODYSSEUS_SPEC_BROWSER_FALLBACK", "0")
        if _bmode != "0" and "private_browser" in picks:
            # This build disables page execution in private_browser (see
            # src/browser_identity.py). "mcp": use the connected Playwright MCP
            # browser (no run_code/evaluate); "web": fetch/search only.
            picks = [n for n in picks if n != "private_browser"]
            extra = list(_MCP_BROWSER_TOOLS) if _bmode == "mcp" else []
            for n in extra + ["web_search", "web_fetch"]:
                if n not in picks:
                    picks.append(n)
        added.update(picks)
        decided = bool(picks) or (choice == "answer_directly" and float(probs.get("answer_directly", 0.0)) >= min_conf)
        decision.update(choice=choice, tools=picks, ms=round((time.time() - t0) * 1000, 1),
                        top=[(names[int(k[1:]) - 1], round(p, 3)) for p, k in ranked[:3]],
                        answer_directly=round(float(probs.get("answer_directly", 0.0)), 3),
                        ask_user=round(float(probs.get("ask_user", 0.0)), 3))
    except Exception as exc:  # fail open
        added = set(prior_tool_names(messages))
        decided = False
        decision.update(error=str(exc)[:200], ms=round((time.time() - t0) * 1000, 1))
        logger.warning("spec router failed open: %s", exc)
    decision["decided"] = decided
    _LAST_DECISION.set(decision)
    logger.info("spec router: %s", {key: value for key, value in decision.items() if key != "message"})
    return (added, decided) if return_decision else added


_ORIGINAL_SPEC_ROUTE_TOOLS = spec_route_tools


async def spec_route_tools_async(
    user_message: str,
    schemas: Iterable[Dict[str, Any]],
    disabled_tools: Optional[Set[str]] = None,
    messages: Optional[Iterable[Dict[str, Any]]] = None,
    return_decision: bool = False,
):
    """Asynchronous non-blocking tool routing.

    Preserves request-local ContextVar semantics, decision propagation,
    cancellation behavior, timeout limits, and safe fallback on router failure.
    """
    if spec_route_tools is not _ORIGINAL_SPEC_ROUTE_TOOLS:
        return spec_route_tools(
            user_message,
            schemas,
            disabled_tools=disabled_tools,
            messages=messages,
            return_decision=return_decision,
        )

    added: Set[str] = set(prior_tool_names(messages))
    decided = False
    clear_decision()
    url = os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip().rstrip("/")
    if not url or not (user_message or "").strip():
        return (added, False) if return_decision else added
    t0 = time.time()
    decision: Dict[str, Any] = {"ts": time.time(), "message": user_message[:160], "sticky": sorted(added)}
    try:
        disabled = set(disabled_tools or ())
        lines: Dict[str, str] = {}
        for s in schemas:
            n = str(_fn(s).get("name") or "")
            if n and n not in disabled and n != "ask_user" and n not in lines:
                lines[n] = tool_line(s)
        names = sorted(lines)
        criteria = dict(_HEAD)
        for i, n in enumerate(names, 1):
            criteria[f"t{i}"] = lines[n]
        req = {
            "model": os.getenv("ODYSSEUS_SPEC_ROUTER_MODEL", "odysseus-s60"),
            "state": {"agent": _AGENT, "conversation": _conversation(messages), "user_message": user_message},
            "questions": {"tool": {"type": "choice", "instructions": _INSTRUCTIONS, "criteria": criteria}},
        }
        timeout = float(os.getenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "0.5"))
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                remaining = max(0.2, deadline - time.time())
                try:
                    resp = await client.post(
                        url + "/v1/systemone",
                        json=req,
                        headers={"content-type": "application/json"},
                        timeout=remaining,
                    )
                    if resp.status_code == 529:
                        if time.time() + 0.15 >= deadline:
                            resp.raise_for_status()
                        decision["retries"] = decision.get("retries", 0) + 1
                        await asyncio.sleep(0.1)
                        continue
                    resp.raise_for_status()
                    out = resp.json()["answers"]["tool"]
                    break
                except httpx.HTTPStatusError as busy:
                    if busy.response.status_code != 529 or time.time() + 0.15 >= deadline:
                        raise
                    decision["retries"] = decision.get("retries", 0) + 1
                    await asyncio.sleep(0.1)
        probs = out.get("probabilities") or {}
        choice = out.get("choice")
        if not isinstance(probs, dict) or choice not in criteria:
            raise ValueError("invalid router choice")
        for key, probability in probs.items():
            if key not in criteria or isinstance(probability, bool):
                raise ValueError("invalid router probability key")
            number = float(probability)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError("invalid router probability")
        probs = {key: float(value) for key, value in probs.items()}
        min_conf = float(os.getenv("ODYSSEUS_SPEC_ROUTER_MIN_CONF", "0.3"))
        second_min = float(os.getenv("ODYSSEUS_SPEC_ROUTER_SECOND", "0.25"))
        ranked = sorted(
            ((p, k) for k, p in probs.items() if k.startswith("t") and k[1:].isdigit()), reverse=True
        )
        picks: List[str] = []
        if choice and choice.startswith("t") and choice[1:].isdigit() and float(probs.get(choice, 0.0)) >= min_conf:
            picks.append(names[int(choice[1:]) - 1])
            for p, k in ranked[:3]:
                nm = names[int(k[1:]) - 1]
                if nm not in picks and p >= second_min:
                    picks.append(nm)
        _bmode = os.getenv("ODYSSEUS_SPEC_BROWSER_FALLBACK", "0")
        if _bmode != "0" and "private_browser" in picks:
            picks = [n for n in picks if n != "private_browser"]
            extra = list(_MCP_BROWSER_TOOLS) if _bmode == "mcp" else []
            for n in extra + ["web_search", "web_fetch"]:
                if n not in picks:
                    picks.append(n)
        added.update(picks)
        decided = bool(picks) or (choice == "answer_directly" and float(probs.get("answer_directly", 0.0)) >= min_conf)
        decision.update(choice=choice, tools=picks, ms=round((time.time() - t0) * 1000, 1),
                        top=[(names[int(k[1:]) - 1], round(p, 3)) for p, k in ranked[:3]],
                        answer_directly=round(float(probs.get("answer_directly", 0.0)), 3),
                        ask_user=round(float(probs.get("ask_user", 0.0)), 3))
    except Exception as exc:  # fail open
        added = set(prior_tool_names(messages))
        decided = False
        decision.update(error=str(exc)[:200], ms=round((time.time() - t0) * 1000, 1))
        logger.warning("spec router failed open: %s", exc)
    decision["decided"] = decided
    _LAST_DECISION.set(decision)
    logger.info("spec router: %s", {key: value for key, value in decision.items() if key != "message"})
    return (added, decided) if return_decision else added


# ---- Ajax Spec: system prompt trimmed to the tools actually offered ----------
_CORE_PROMPT = (
    "You are Ajax, the assistant in Odysseus. Emit one native tool call, then stop. "
    "After a successful tool result, answer from it; never repeat the same tool with the same arguments."
)
_CODING_PROMPT = (
    "HARD CODING ORDER: for code changes call read_file, then edit_file or apply_patch, then host_shell for tests. "
    "Never run tests before the edit succeeds; after a read, your next call is the edit. "
    "edit_file JSON is {path,old_string,new_string}; write_file only for new files or full rewrites."
)
_TOOL_NOTES = {
    "manage_notes": "manage_notes: notes/checklists.",
    "manage_documents": "manage_documents: document library; actions list/read/delete/tidy, use list+search to find by title/content.",
    "create_document": "create_document: create editor-panel documents with title/content.",
    "edit_document": "edit_document: edit the active editor document using command=<<<FIND>>>old<<<REPLACE>>>new<<<END>>> or edits=[{find,replace}].",
    "update_document": "update_document: full active-document rewrite only.",
    "manage_calendar": "manage_calendar: calendar events; actions create_event, update_event, delete_event, list_events.",
    "manage_tasks": "manage_tasks: scheduled/recurring tasks; create uses action=create, name, prompt, task_type, schedule, scheduled_time, output_target; pause/resume/delete use task_id, so list/search first when only the name is known. Recurring reminders use manage_tasks.",
    "manage_memory": "manage_memory: saved memories.",
    "manage_contact": "manage_contact: address-book contacts.",
    "manage_research": "manage_research: saved research reports.",
    "manage_settings": "manage_settings: app settings.",
    "manage_endpoints": "manage_endpoints: configured API endpoints.",
    "list_models": "list_models: available models.",
    "manage_mcp": "manage_mcp: MCP servers.",
    "manage_skills": "manage_skills: skills.",
    "manage_webhooks": "manage_webhooks: webhooks.",
    "manage_bg_jobs": "manage_bg_jobs: running/background jobs.",
    "search_chats": "search_chats: past chats.",
    "web_search": "web_search: public web lookup, URLs, links.",
    "web_fetch": "web_fetch: fetch a concrete URL/domain.",
    "private_browser": "private_browser: rendered site interaction. Make separate calls: first action=open with the url, then action=snapshot; read/scroll/find as needed. Do not use action=batch. Only click/fill element refs returned by snapshot (for example @e12), never guessed labels.",
    "mcp__builtin_browser__browser_navigate": "Browser: browser_navigate {url} opens a page; then browser_snapshot returns the page with element refs; browser_click/browser_type take ref values from the latest snapshot, never guessed labels.",
    "youtube_tool": "youtube_tool: YouTube comments, transcripts, metadata, and latest channel video.",
    "read_file": "read_file: workspace text, PDF, or Office document path.",
    "edit_file": "edit_file: exact replacement in an existing workspace file.",
    "write_file": "write_file: create or fully rewrite a workspace file.",
    "apply_patch": "apply_patch: related multi-file workspace edits.",
    "host_shell": "host_shell: run tests, builds, and verification in the active workspace.",
}
_EMAIL_NOTES = (
    "Email: latest email/emails/inbox -> mcp__email__list_emails {folder:INBOX,max_results:1,unread_only:false}; "
    "account list -> list_email_accounts; subject find+read -> mcp__email__list_emails {folder:INBOX,max_results:20}; "
    "sender/topic search -> mcp__email__search_emails {max_results:10}; known id -> mcp__email__read_email; "
    "new draft -> mcp__email__draft_email; attachment -> mcp__email__download_attachment {uid,index,folder,account}; "
    "reply draft -> mcp__email__draft_email_reply {uid,account,body}; only explicit send-now -> mcp__email__reply_to_email {uid,account,body}; "
    "archive/delete/mark -> mcp__email__archive_email/mcp__email__delete_email/mcp__email__mark_email_read. Never emit manage_email."
)
_AJAX_PROMPT_START = "You are Ajax, the assistant in Odysseus."


def trimmed_system_prompt(offered: Iterable[str]) -> str:
    offered = set(offered or ())
    parts = [_CORE_PROMPT]
    if offered & {"edit_file", "apply_patch", "host_shell"}:
        parts.append(_CODING_PROMPT)
    parts += [_TOOL_NOTES[n] for n in sorted(offered) if n in _TOOL_NOTES]
    if any(n.startswith("mcp__email__") or n in {"list_email_accounts"} for n in offered):
        parts.append(_EMAIL_NOTES)
    return " ".join(parts)


def trim_system_prompt(messages: List[Dict[str, Any]], offered: Iterable[str]) -> List[Dict[str, Any]]:
    """Swap the generic Ajax tool manual for one covering only the offered tools."""
    out: List[Dict[str, Any]] = []
    for m in messages:
        if (
            isinstance(m, dict)
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and m["content"].startswith(_AJAX_PROMPT_START)
        ):
            new = dict(m)
            new["content"] = trimmed_system_prompt(offered)
            logger.info("[spec-harness] system prompt trimmed %d -> %d chars", len(m["content"]), len(new["content"]))
            out.append(new)
        else:
            out.append(m)
    return out


def drop_skills_index(messages: List[Dict[str, Any]], offered: Iterable[str]) -> List[Dict[str, Any]]:
    """Ajax Spec: omit the ~1000-token skills index unless the skills tool is offered."""
    if "manage_skills" in set(offered or ()):
        return messages
    out = [
        m for m in messages
        if not (isinstance(m, dict) and (m.get("metadata") or {}).get("source") == "available skills index")
    ]
    if len(out) != len(messages):
        logger.info("[spec-harness] dropped skills index (%d chars)", sum(len(str(m.get("content") or "")) for m in messages if m not in out))
    return out


# --- Router-decided thinking (progressive thinking trigger) -------------------------------------------------
# The router classifies the user's request as THINK (needs reasoning: math/logic, tricky planning, subtle explanation or
# weighing options, first-instinct-likely-wrong questions) or DIRECT (plain doing, lookup, chit-chat).
# Zero-shot on 143 DeepSeek-labelled app requests (2026-10-13): odysseus-agentic-v1f acc 0.916, think recall 0.985,
# think precision 0.859, ~33 ms. Off unless ODYSSEUS_SPEC_THINK_ROUTER=1. Never raises; returns None on any error.
_THINK_DEF = ("This request needs genuine reasoning or figuring something out before answering: math and logic puzzles; "
              "planning a tricky multi-step approach; explaining something subtle or weighing options; or questions where "
              "a quick first instinct is likely wrong.")
_DIRECT_DEF = ("This request is plain doing, lookup, or chit-chat that can be answered directly without reasoning: simple "
               "tool actions (set a reminder, search email, open a page, rename a file), simple facts, or small talk.")
_THINK_INSTRUCTIONS = ("Classify whether this single user request needs the assistant to THINK (deep reasoning / figuring "
                       "something out) before answering, or can be answered DIRECTLY (plain doing, lookup, or chit-chat). "
                       "Answer with exactly one of the two options.")


# 5-way request kind, exactly as trained into adapter thinkclass5 (2026-10-13; held-out 143: think/direct 96.5%,
# think precision 98.8%, recall 95.2%). Enabled with ODYSSEUS_SPEC_THINK_QUESTION=kind5 + ODYSSEUS_SPEC_THINK_MODEL=thinkclass5.
_KIND5_QUESTION = {"type": "choice", "instructions": "Read the user's request and classify it into exactly one of five kinds: math_logic, tricky_planning, explain_or_weigh, trap_question, or direct. Pick the option whose description fits best.", "criteria": {"math_logic": "Mathematical or logical computation/reasoning: arithmetic, word problems, logic puzzles, counting, proofs.", "tricky_planning": "Planning a multi-step approach with real constraints: schedules, itineraries, sequencing, resource or budget allocation, multi-part procedures.", "explain_or_weigh": "Explaining something subtle or weighing options/tradeoffs: comparisons, 'should I', pros and cons, recommendations with reasoning, nuanced why-explanations.", "trap_question": "A question where the obvious or first-instinct answer is likely wrong: riddles, trick questions, common misconceptions, counterintuitive facts.", "direct": "Plain doing, lookup, or chit-chat: simple tool actions, simple facts, small talk — no deep reasoning needed."}}


def think_router_enabled() -> bool:
    return os.getenv("ODYSSEUS_SPEC_THINK_ROUTER", "0").strip() not in ("", "0", "false", "off")


def route_thinking(user_message: str) -> Optional[Dict[str, Any]]:
    """Return {'think': bool, 'p_think': float, 'ms': float} or None (disabled / error / empty message)."""
    if not think_router_enabled():
        return None
    url = os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip().rstrip("/")
    text = (user_message or "").strip()
    if not url or not text:
        return None
    kind5 = os.getenv("ODYSSEUS_SPEC_THINK_QUESTION", "binary").strip().lower() == "kind5"
    question = _KIND5_QUESTION if kind5 else {"type": "choice", "instructions": _THINK_INSTRUCTIONS,
                                              "criteria": {"think": _THINK_DEF, "direct": _DIRECT_DEF}}
    req = {
        "model": os.getenv("ODYSSEUS_SPEC_THINK_MODEL", os.getenv("ODYSSEUS_SPEC_ROUTER_MODEL", "odysseus-s60")),
        "state": {"agent": _AGENT, "conversation": [], "user_message": text[:4000]},
        "questions": {"thinking": question},
    }
    t0 = time.time()
    try:
        timeout = float(os.getenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "3.0"))
        http = urllib.request.Request(url + "/v1/systemone", data=json.dumps(req).encode(),
                                      headers={"content-type": "application/json"})
        ans = json.load(urllib.request.urlopen(http, timeout=timeout))["answers"]["thinking"]
        probs = ans.get("probabilities") or {}
        # kind5: think = any of the four reasoning kinds (trained adapter thinkclass5); binary: P(think).
        p = float(1.0 - probs.get("direct", 1.0)) if kind5 else float(probs.get("think", 0.0))
        min_p = float(os.getenv("ODYSSEUS_SPEC_THINK_MIN_P", "0.5"))
        out = {"think": p >= min_p, "p_think": round(p, 4), "ms": round((time.time() - t0) * 1000, 1)}
        if kind5:
            out["kind"] = ans.get("choice")
        logger.info("spec think router: %s", out)
        return out
    except Exception as exc:  # fail open: the existing progressive-thinking rules still apply
        logger.warning("spec think router failed (%s); falling back to rule-based thinking", exc)
        return None


_ORIGINAL_ROUTE_THINKING = route_thinking


async def route_thinking_async(user_message: str) -> Optional[Dict[str, Any]]:
    """Asynchronous non-blocking route_thinking."""
    if route_thinking is not _ORIGINAL_ROUTE_THINKING:
        return route_thinking(user_message)
    if not think_router_enabled():
        return None
    url = os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip().rstrip("/")
    text = (user_message or "").strip()
    if not url or not text:
        return None
    kind5 = os.getenv("ODYSSEUS_SPEC_THINK_QUESTION", "binary").strip().lower() == "kind5"
    question = _KIND5_QUESTION if kind5 else {"type": "choice", "instructions": _THINK_INSTRUCTIONS,
                                              "criteria": {"think": _THINK_DEF, "direct": _DIRECT_DEF}}
    req = {
        "model": os.getenv("ODYSSEUS_SPEC_THINK_MODEL", os.getenv("ODYSSEUS_SPEC_ROUTER_MODEL", "odysseus-s60")),
        "state": {"agent": _AGENT, "conversation": [], "user_message": text[:4000]},
        "questions": {"thinking": question},
    }
    t0 = time.time()
    try:
        timeout = float(os.getenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "3.0"))
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url + "/v1/systemone",
                json=req,
                headers={"content-type": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            ans = resp.json()["answers"]["thinking"]
        probs = ans.get("probabilities") or {}
        p = float(1.0 - probs.get("direct", 1.0)) if kind5 else float(probs.get("think", 0.0))
        min_p = float(os.getenv("ODYSSEUS_SPEC_THINK_MIN_P", "0.5"))
        out = {"think": p >= min_p, "p_think": round(p, 4), "ms": round((time.time() - t0) * 1000, 1)}
        if kind5:
            out["kind"] = ans.get("choice")
        logger.info("spec think router: %s", out)
        return out
    except Exception as exc:
        logger.warning("spec think router failed (%s); falling back to rule-based thinking", exc)
        return None


# ---- Ajax Spec driver: the router picks every step --------------------------------------------------------
def next_action(user_message: str, latest_result: str, schemas: Iterable[Dict[str, Any]],
                messages: Optional[Iterable[Dict[str, Any]]] = None,
                disabled_tools: Optional[Set[str]] = None) -> Optional[Dict[str, Any]]:
    """Top-1 next action at any confidence: {'kind': 'tool'|'answer_directly'|'ask_user', 'name', 'p'}.

    Same request as training (build_agentic_rows.py): sorted tool lines, and mid-turn the latest tool
    result appended to the user message as "\\n\\nLatest tool result:\\n...". Returns None on any error."""
    url = os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip().rstrip("/")
    if not url:
        return None
    t0 = time.time()
    disabled = set(disabled_tools or ())
    lines: Dict[str, str] = {}
    for s in schemas:
        n = str(_fn(s).get("name") or "")
        if n and n not in disabled and n != "ask_user" and n not in lines:
            lines[n] = tool_line(s)
    names = sorted(lines)
    criteria = dict(_HEAD)
    for i, n in enumerate(names, 1):
        criteria[f"t{i}"] = lines[n]
    um = (user_message or "")[-3000:]
    if latest_result:
        um += "\n\nLatest tool result:\n" + latest_result[-1500:]
    req = {
        "model": os.getenv("ODYSSEUS_SPEC_ROUTER_MODEL", "odysseus-agentic-v4"),
        "state": {"agent": _AGENT, "conversation": _conversation(messages), "user_message": um},
        "questions": {"tool": {"type": "choice", "instructions": _INSTRUCTIONS, "criteria": criteria}},
    }
    try:
        http = urllib.request.Request(url + "/v1/systemone", data=json.dumps(req).encode(),
                                      headers={"content-type": "application/json"})
        timeout = float(os.getenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "3.0"))
        deadline = time.time() + timeout
        while True:
            try:
                out = json.load(urllib.request.urlopen(http, timeout=max(0.2, deadline - time.time())))["answers"]["tool"]
                break
            except urllib.error.HTTPError as busy:
                if busy.code != 529 or time.time() + 0.15 >= deadline:
                    raise
                time.sleep(0.1)
        probs = out.get("probabilities") or {}
        choice = str(out.get("choice") or "")
        if choice.startswith("t") and choice[1:].isdigit():
            action = {"kind": "tool", "name": names[int(choice[1:]) - 1]}
        elif choice in ("answer_directly", "ask_user"):
            action = {"kind": choice, "name": choice}
        else:
            return None
        ranked = sorted(((float(v), k) for k, v in probs.items() if k.startswith("t") and k[1:].isdigit()), reverse=True)
        action.update(p=round(float(probs.get(choice, 0.0)), 3), ms=round((time.time() - t0) * 1000, 1),
                      top=[(names[int(k[1:]) - 1], round(v, 3)) for v, k in ranked[:6]])
        logger.info("spec next action: %s", action)
        return action
    except Exception as exc:
        logger.warning("spec next action failed: %s", exc)
        return None


_ORIGINAL_NEXT_ACTION = next_action


async def next_action_async(user_message: str, latest_result: str, schemas: Iterable[Dict[str, Any]],
                            messages: Optional[Iterable[Dict[str, Any]]] = None,
                            disabled_tools: Optional[Set[str]] = None) -> Optional[Dict[str, Any]]:
    """Asynchronous non-blocking next_action."""
    if next_action is not _ORIGINAL_NEXT_ACTION:
        return next_action(user_message, latest_result, schemas, messages=messages, disabled_tools=disabled_tools)
    url = os.getenv("ODYSSEUS_SPEC_ROUTER_URL", "").strip().rstrip("/")
    if not url:
        return None
    t0 = time.time()
    disabled = set(disabled_tools or ())
    lines: Dict[str, str] = {}
    for s in schemas:
        n = str(_fn(s).get("name") or "")
        if n and n not in disabled and n != "ask_user" and n not in lines:
            lines[n] = tool_line(s)
    names = sorted(lines)
    criteria = dict(_HEAD)
    for i, n in enumerate(names, 1):
        criteria[f"t{i}"] = lines[n]
    um = (user_message or "")[-3000:]
    if latest_result:
        um += "\n\nLatest tool result:\n" + latest_result[-1500:]
    req = {
        "model": os.getenv("ODYSSEUS_SPEC_ROUTER_MODEL", "odysseus-agentic-v4"),
        "state": {"agent": _AGENT, "conversation": _conversation(messages), "user_message": um},
        "questions": {"tool": {"type": "choice", "instructions": _INSTRUCTIONS, "criteria": criteria}},
    }
    try:
        timeout = float(os.getenv("ODYSSEUS_SPEC_ROUTER_TIMEOUT", "3.0"))
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                remaining = max(0.2, deadline - time.time())
                try:
                    resp = await client.post(
                        url + "/v1/systemone",
                        json=req,
                        headers={"content-type": "application/json"},
                        timeout=remaining,
                    )
                    if resp.status_code == 529:
                        if time.time() + 0.15 >= deadline:
                            resp.raise_for_status()
                        await asyncio.sleep(0.1)
                        continue
                    resp.raise_for_status()
                    out = resp.json()["answers"]["tool"]
                    break
                except httpx.HTTPStatusError as busy:
                    if busy.response.status_code != 529 or time.time() + 0.15 >= deadline:
                        raise
                    await asyncio.sleep(0.1)
        probs = out.get("probabilities") or {}
        choice = str(out.get("choice") or "")
        if choice.startswith("t") and choice[1:].isdigit():
            action = {"kind": "tool", "name": names[int(choice[1:]) - 1]}
        elif choice in ("answer_directly", "ask_user"):
            action = {"kind": choice, "name": choice}
        else:
            return None
        ranked = sorted(((float(v), k) for k, v in probs.items() if k.startswith("t") and k[1:].isdigit()), reverse=True)
        action.update(p=round(float(probs.get(choice, 0.0)), 3), ms=round((time.time() - t0) * 1000, 1),
                      top=[(names[int(k[1:]) - 1], round(v, 3)) for v, k in ranked[:6]])
        logger.info("spec next action: %s", action)
        return action
    except Exception as exc:
        logger.warning("spec next action failed: %s", exc)
        return None
