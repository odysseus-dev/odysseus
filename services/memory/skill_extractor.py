"""
skill_extractor.py

Background auto-extraction of skills from complex agent runs.
When the agent takes >= 2 rounds or >= 2 tool calls to complete a task,
we ask the LLM to distill the approach into a reusable skill.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SKILL_EXTRACT_PROMPT = (
    "You are analyzing an AI agent's work session. The agent took {rounds} rounds "
    "and {tool_count} tool calls to complete the task.\n\n"
    "Extract a reusable 'skill' ONLY IF the session contains a concrete, "
    "repeatable procedure the agent could follow to solve a similar problem "
    "ON THE COMPUTER next time (e.g. a sequence of shell commands, code, file "
    "edits, API calls, or tool usage).\n\n"
    "Return null (the bare word, no JSON) when the session is NOT a reusable "
    "computer procedure, including:\n"
    "- The real work happened OUTSIDE the computer (the user did something "
    "physically, in person, on another device, or by hand) and the agent only "
    "discussed or advised it.\n"
    "- A one-off, personal, or context-specific task that won't recur "
    "(personal errands, a specific person/place/date, casual conversation).\n"
    "- A pure question/answer or explanation with no transferable method.\n"
    "- The agent failed, gave up, or the approach is not worth repeating.\n\n"
    "When (and only when) a genuine reusable procedure exists, return a JSON "
    "object with:\n"
    '- "title": short name (under 10 words)\n'
    '- "problem": what was the challenge (1-2 sentences)\n'
    '- "solution": what worked (1-2 sentences)\n'
    '- "steps": array of step-by-step instructions (3-7 short steps)\n'
    '- "tags": array of relevant keywords (3-5 tags)\n'
    '- "confidence": 0.0-1.0 how reliable AND reusable this procedure is\n\n'
    "Be conservative: if in doubt, return null.\n"
    "Return ONLY valid JSON (or the bare word null), no markdown fences."
)

# Skills the model is unsure about (or that read as one-offs) add clutter —
# drop anything below this confidence.
MIN_CONFIDENCE = 0.6

# How many recent messages to include
CONTEXT_WINDOW = 12


def _skill_dicts(skills):
    for skill in skills or []:
        if isinstance(skill, dict):
            yield skill


def _has_duplicate_title(skills, title: str) -> bool:
    wanted = title.lower()
    for skill in _skill_dicts(skills):
        existing = skill.get("title", "")
        if isinstance(existing, str) and existing.lower() == wanted:
            return True
    return False


async def maybe_extract_skill(
    session,
    skills_manager,
    endpoint_url: str,
    model: str,
    headers: dict,
    round_count: int,
    tool_count: int,
    owner: Optional[str] = None,
):
    """Extract a skill if the agent run was complex enough."""
    if not model:
        logger.debug("[skill-extract] No model provided, skipping")
        return None

    # Quiet by default; flip to DEBUG when chasing extractor issues.
    logger.debug(
        "[skill-extract] start: rounds=%d tools=%d model=%s owner=%s",
        round_count, tool_count, model, owner,
    )
    if round_count < 2 and tool_count < 2:
        logger.debug("[skill-extract] BELOW threshold (need rounds>=2 or tools>=2)")
        return None

    try:
        from src.llm_core import llm_call_async

        # Get recent messages
        history = session.get_context_messages()
        recent = history[-CONTEXT_WINDOW:] if len(history) > CONTEXT_WINDOW else history
        if not recent:
            logger.debug("[skill-extract] no recent messages, skipping")
            return None

        # Strip media (images/audio) from messages
        stripped_recent = []
        for msg in recent:
            content = msg.get("content", "")
            if isinstance(content, list):
                text_only = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if not text_only and content:
                    continue
                content = text_only
            stripped_recent.append({"role": msg.get("role"), "content": content})

        if not stripped_recent:
            return None

        # Build conversation summary for extraction
        conv_lines = []
        for msg in stripped_recent:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            # Truncate long messages
            if len(content) > 500:
                content = content[:500] + "..."
            conv_lines.append(f"[{role}] {content}")

        conversation = "\n".join(conv_lines)

        prompt = SKILL_EXTRACT_PROMPT.format(rounds=round_count, tool_count=tool_count)

        import time as _time
        _t0 = _time.monotonic()
        logger.debug(
            "[skill-extract] calling LLM (endpoint=%s, ctx=%d msgs, timeout=30s)",
            endpoint_url, len(recent),
        )
        response = await llm_call_async(
            endpoint_url,
            model,
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Conversation:\n{conversation}"},
            ],
            headers=headers,
            timeout=30,
        )
        logger.debug(
            "[skill-extract] LLM returned in %.1fs (len=%d, head=%r)",
            _time.monotonic() - _t0, len(response or ""), (response or "")[:80],
        )

        if not response or response.strip().lower() == "null":
            logger.debug(
                "[skill-extract] LLM declined (returned null/empty) — "
                "session deemed not a reusable procedure"
            )
            return None

        # Some models (MiniMax, Qwen-Thinker, DeepSeek-R1) emit their
        # chain-of-thought BEFORE the JSON output even when asked for
        # raw JSON. `strip_think(prose=True, prompt_echo=True)` removes
        # <think>…</think> tags AND prose-style "Let me analyze this…"
        # preambles. Without it, json.loads bombed on character 0 every
        # time and the silent-bail looked like "extractor doesn't work".
        try:
            from src.text_helpers import strip_think as _strip_think
            response = _strip_think(response, prose=True, prompt_echo=True)
        except Exception:
            pass

        # Parse JSON
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # After strip_think, the JSON may still be embedded inside surrounding
        # commentary — slice from the first '{' to the matching last '}'.
        if text and text[0] != "{":
            _start = text.find("{")
            _end = text.rfind("}")
            if 0 <= _start < _end:
                text = text[_start : _end + 1]

        data = json.loads(text)
        if not data or not isinstance(data, dict):
            logger.debug("[skill-extract] parsed JSON not a dict, dropping")
            return None

        title = data.get("title", "").strip()
        if not title:
            logger.debug("[skill-extract] LLM returned object with no title, dropping")
            return None

        # Honour the model's own reliability/reusability estimate — low-
        # confidence extractions are usually one-offs or shaky procedures.
        try:
            _conf = float(data.get("confidence", 0.7))
        except (TypeError, ValueError):
            _conf = 0.7
        if _conf < MIN_CONFIDENCE:
            logger.debug(
                "[skill-extract] '%s' below confidence floor (%.2f < %.2f) — dropped",
                title, _conf, MIN_CONFIDENCE,
            )
            return None

        # Check for duplicate skills
        existing = skills_manager.load(owner=owner)
        if _has_duplicate_title(existing, title):
            logger.debug("[skill-extract] '%s' already exists — dropped as duplicate", title)
            return None

        entry = skills_manager.add_skill(
            title=title,
            problem=data.get("problem", ""),
            solution=data.get("solution", ""),
            steps=data.get("steps", []),
            tags=data.get("tags", []),
            source="learned",
            confidence=data.get("confidence", 0.7),
            session_id=getattr(session, "session_id", None),
            owner=owner,
        )
        try:
            from src.event_bus import fire_event
            fire_event("skill_added", owner)
        except Exception:
            logger.debug("skill_added event dispatch failed", exc_info=True)
        logger.info("Auto-extracted skill: %s (id=%s)", title, entry["id"])
        return entry

    except json.JSONDecodeError as e:
        logger.debug("[skill-extract] non-JSON LLM response, dropping: %s", e)
        return None
    except Exception as e:
        # Real exceptions stay INFO+warning so they don't get lost when
        # users only have default log level. `exc_info=True` ships the
        # full traceback so timeouts vs auth vs import errors are
        # distinguishable from outside.
        logger.warning("[skill-extract] FAILED: %s", e, exc_info=True)
        return None
