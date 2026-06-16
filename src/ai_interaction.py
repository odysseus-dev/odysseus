"""
ai_interaction.py

AI-to-AI interaction tools: chat_with_model, create_session, list_sessions,
send_to_session, pipeline.

These are agent tools — the LLM writes fenced code blocks and they execute
through the standard agent_tools.py pipeline.
"""

import json
import logging
import uuid
import time
from typing import Dict, Optional, Tuple

from src.constants import GENERATED_IMAGES_DIR

logger = logging.getLogger(__name__)

AI_CHAT_TIMEOUT = 120  # seconds for a single LLM call
MAX_DEBATE_ROUNDS = 5
MAX_PIPELINE_STEPS = 10
# Cap on concurrent LLM calls for bulk evolve operations (skill evolve_all,
# memory_evolve_all). Free-tier models share a strict per-minute rate limit;
# firing every item's call at once reliably exceeds it. A small cap spreads
# the requests out instead of bursting them.
_BULK_EVOLVE_CONCURRENCY = 3

# ---------------------------------------------------------------------------
# Global managers (set from app.py, same pattern as _mcp_manager)
# _session_manager is kept as a local cache for performance (avoiding
# repeated get_session_manager_instance() calls). It's synced with
# the authoritative singleton in core.models.
_session_manager = None
_memory_manager = None
_memory_vector = None
_rag_manager = None
_personal_docs_manager = None


def set_session_manager(mgr):
    """Set the global session manager. Syncs local cache + core singleton."""
    global _session_manager
    _session_manager = mgr
    from core.models import set_session_manager_instance
    set_session_manager_instance(mgr)


def get_session_manager():
    """Get the global session manager."""
    return _session_manager


def set_memory_manager(mgr, vector=None):
    global _memory_manager, _memory_vector
    _memory_manager = mgr
    _memory_vector = vector


def _ensure_memory_manager():
    """Return the shared MemoryManager, constructing one on first use.

    app.py's startup normally calls set_memory_manager() before any of this
    runs. But raphael.py (the CLI wrapper) invokes do_raphael()/do_manage_memory()
    in a standalone subprocess that never runs app.py's startup, leaving
    _memory_manager None and every memory_* action silently returning "no
    memories found"/"manager not available" despite memory.json being full.
    Lazily building one here (reading the same data/memory.json) fixes the
    CLI path without changing behaviour when the global is already set.
    """
    global _memory_manager
    if _memory_manager is None:
        from src.memory import MemoryManager
        from src.constants import DATA_DIR
        _memory_manager = MemoryManager(DATA_DIR)
    return _memory_manager


async def _retry_on_rate_limit(coro_fn, max_attempts: int = 3, wait_seconds: float = 65):
    """Retry a call specifically on HTTP 429, waiting long enough to clear a
    per-minute quota window.

    llm_call_async has its own retry loop, but its delay (0.5s) is sized for
    transient blips (502/503/connection hiccups), not for waiting out a
    per-minute rate limit -- retrying that fast just burns the same exhausted
    window again. Used by bulk evolve operations where actually finishing
    matters more than finishing fast.
    """
    import asyncio
    from fastapi import HTTPException

    for attempt in range(max_attempts):
        try:
            return await coro_fn()
        except HTTPException as e:
            if e.status_code == 429 and attempt < max_attempts - 1:
                await asyncio.sleep(wait_seconds)
                continue
            raise


def set_rag_manager(rag_mgr, personal_docs_mgr=None):
    global _rag_manager, _personal_docs_manager
    _rag_manager = rag_mgr
    _personal_docs_manager = personal_docs_mgr


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, resolve_endpoint_runtime


def _resolve_model(spec: str, owner: Optional[str] = None) -> Tuple[str, str, Dict]:
    """Resolve a model specifier to (endpoint_url, model_id, headers).

    Accepts:
      "model_name"              — searches all configured endpoints
      "model_name@endpoint_name" — looks up specific endpoint by display name

    Raises ValueError if model not found.
    """
    import httpx
    from src.database import SessionLocal, ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS
    from src.auth_helpers import owner_filter

    spec = spec.strip()
    target_endpoint_name = None

    if "@" in spec:
        model_name, target_endpoint_name = spec.rsplit("@", 1)
        model_name = model_name.strip()
        target_endpoint_name = target_endpoint_name.strip()
    else:
        model_name = spec

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if target_endpoint_name:
            query = query.filter(ModelEndpoint.name.ilike(f"%{target_endpoint_name}%"))
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        endpoints = query.all()

        if not endpoints:
            raise ValueError("No enabled endpoints found" +
                             (f" matching '{target_endpoint_name}'" if target_endpoint_name else ""))

        for ep in endpoints:
            try:
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            provider = _detect_provider(base)
            headers = build_headers(api_key, base)

            if provider == "anthropic":
                # Anthropic: match against hardcoded model list
                matched = None
                for am in ANTHROPIC_MODELS:
                    if model_name.lower() in am.lower() or am.lower() in model_name.lower():
                        matched = am
                        break
                if matched:
                    return build_chat_url(base), matched, headers
            else:
                # OpenAI-compatible and native Ollama: probe the provider's model list.
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
                        data = r.json()
                        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                        if not model_ids:
                            model_ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                    else:
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    model_ids = []

                # Exact match first
                for mid in model_ids:
                    if mid.lower() == model_name.lower():
                        return build_chat_url(base), mid, headers

                # Partial match
                for mid in model_ids:
                    if model_name.lower() in mid.lower() or mid.lower() in model_name.lower():
                        return build_chat_url(base), mid, headers

        raise ValueError(f"Model '{spec}' not found on any configured endpoint")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def do_chat_with_model(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Send a message to a specific model and return its response.

    Content format:
      Line 1: model_name (or model_name@endpoint_name)
      Line 2+: the message to send
    """
    from src.llm_core import llm_call_async

    lines = content.strip().split("\n", 1)
    if not lines or not lines[0].strip():
        return {"error": "First line must be the model name"}

    model_spec = lines[0].strip()
    message = lines[1].strip() if len(lines) > 1 else ""
    if not message:
        return {"error": "No message provided (line 2+ is the message)"}

    try:
        url, model, headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    try:
        response = await llm_call_async(
            url, model,
            [{"role": "user", "content": message}],
            headers=headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        # Truncate very long responses
        if len(response) > 10000:
            response = response[:10000] + "\n... (truncated)"
        return {"model": model, "response": response}
    except Exception as e:
        logger.error(f"chat_with_model failed: {e}")
        return {"error": f"Failed to get response from {model_spec}: {e}"}


_TEACHER_SYSTEM_PROMPT = (
    "You are a senior AI mentor. A less capable model is stuck on a problem and asking for help. "
    "Provide clear, actionable guidance:\n"
    "1. Brief analysis of the problem\n"
    "2. Recommended approach (step by step)\n"
    "3. Key things to watch out for\n\n"
    "Be concise and practical. No preamble."
)


async def do_ask_teacher(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Ask a more capable model for help.

    Content format:
      Line 1: model_name (or 'auto')
      Line 2+: the problem description
    """
    from src.llm_core import llm_call_async
    from src.settings import get_setting

    lines = content.strip().split("\n", 1)
    model_spec = lines[0].strip() if lines else "auto"
    problem = lines[1].strip() if len(lines) > 1 else ""

    if not problem:
        return {"error": "No problem description provided"}

    if model_spec.lower() in ("auto", ""):
        model_spec = get_setting("teacher_model", "")
        if not model_spec:
            return {"error": "No teacher model configured. Specify a model name or set teacher_model in settings."}

    try:
        url, model, headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    try:
        response = await llm_call_async(
            url, model,
            [
                {"role": "system", "content": _TEACHER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Problem:\n{problem}"},
            ],
            headers=headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        if len(response) > 8000:
            response = response[:8000] + "\n... (truncated)"
        return {"model": model, "response": response, "teacher": True}
    except Exception as e:
        logger.error(f"ask_teacher failed: {e}")
        return {"error": f"Teacher call failed ({model_spec}): {e}"}


async def do_second_opinion(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Get a second opinion from another model, then have the original model
    evaluate the feedback and produce a unified version.

    Content format:
      Line 1: model_name (or model_name@endpoint_name)
      Line 2+ (optional): specific question or focus area

    Flow:
      1. Pull recent conversation context
      2. Send to reviewer model → get honest feedback
      3. Send feedback back to the session's own model → evaluate & unify
      4. Return both the review and the unified response
    """
    from src.llm_core import llm_call_async

    lines = content.strip().split("\n", 1)
    if not lines or not lines[0].strip():
        return {"error": "First line must be the model name"}

    model_spec = lines[0].strip()
    focus = lines[1].strip() if len(lines) > 1 else ""

    try:
        reviewer_url, reviewer_model, reviewer_headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    # Pull recent conversation context from current session
    context_text = ""
    sess = None
    if session_id and _session_manager:
        sess = _session_manager.get_session(session_id)
        if sess:
            messages = sess.get_context_messages()
            recent = messages[-15:] if len(messages) > 15 else messages
            parts = []
            for m in recent:
                role = m.get("role", "unknown").upper()
                text = m.get("content", "")
                if isinstance(text, list):
                    text = " ".join(
                        p.get("text", "") for p in text if isinstance(p, dict)
                    )
                if text:
                    parts.append(f"[{role}]: {text[:2000]}")
            context_text = "\n\n".join(parts)

    if not context_text:
        return {"error": "No conversation context found to review"}

    # ── Step 1: Get the reviewer's feedback ──
    reviewer_system = (
        "You are giving a second opinion on a conversation between a user and an AI assistant. "
        "Your job is to be genuinely helpful and honest — not a yes-man, but not a contrarian either.\n\n"
        "Guidelines:\n"
        "- If the plan/idea is solid, say so clearly. Don't manufacture problems that aren't there.\n"
        "- If you spot a real flaw, blind spot, or simpler approach — call it out directly.\n"
        "- Be practical. Don't over-engineer or over-analyze. Real-world tradeoffs matter.\n"
        "- If there's a meaningfully better way to do something, suggest it concretely.\n"
        "- Give credit where it's due — highlight what's working well.\n"
        "- Keep it concise and actionable. No fluff.\n"
        "- You're a second pair of eyes, not a professor grading a paper."
    )

    reviewer_message = f"Here's the conversation so far:\n\n{context_text}"
    if focus:
        reviewer_message += f"\n\n---\nSpecifically, I want your take on: {focus}"
    else:
        reviewer_message += "\n\n---\nGive me your honest second opinion on what's being discussed."

    try:
        review = await llm_call_async(
            reviewer_url, reviewer_model,
            [
                {"role": "system", "content": reviewer_system},
                {"role": "user", "content": reviewer_message},
            ],
            headers=reviewer_headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        if len(review) > 8000:
            review = review[:8000] + "\n... (truncated)"
    except Exception as e:
        logger.error(f"second_opinion reviewer call failed: {e}")
        return {"error": f"Failed to get second opinion from {model_spec}: {e}"}

    # ── Step 2: Send review back to session's own model for evaluation ──
    unified = ""
    original_model = "unknown"
    if sess:
        original_url = sess.endpoint_url
        original_model = sess.model
        original_headers = getattr(sess, "headers", None) or {}

        unify_system = (
            "Another AI model just reviewed the conversation you've been having with the user. "
            "Read their feedback carefully, then respond with:\n\n"
            "1. **What you agree with** — acknowledge valid points honestly.\n"
            "2. **What you disagree with** — explain why, briefly.\n"
            "3. **Unified version** — produce an updated/refined version of whatever was being discussed, "
            "incorporating the feedback you found valid. Don't accept every note blindly — "
            "use your judgment on what actually improves things vs what's unnecessary.\n\n"
            "Be concise and practical. The user wants a better result, not a meta-discussion."
        )

        unify_message = (
            f"Here's the conversation context:\n\n{context_text}\n\n"
            f"---\n\n"
            f"**Review from {reviewer_model}:**\n\n{review}\n\n"
            f"---\n\n"
            f"Evaluate this feedback and produce a unified improved version."
        )

        try:
            unified = await llm_call_async(
                original_url, original_model,
                [
                    {"role": "system", "content": unify_system},
                    {"role": "user", "content": unify_message},
                ],
                headers=original_headers,
                timeout=AI_CHAT_TIMEOUT,
            )
            if len(unified) > 10000:
                unified = unified[:10000] + "\n... (truncated)"
        except Exception as e:
            logger.error(f"second_opinion unify call failed: {e}")
            unified = f"(Failed to get unified response: {e})"

    # Build combined result
    combined = (
        f"## Second Opinion from {reviewer_model}\n\n{review}"
        f"\n\n---\n\n"
        f"## {original_model}'s Response\n\n{unified}"
    )

    return {
        "model": reviewer_model,
        "response": combined,
        "instruction": "Present these results to the user exactly as they are. Do NOT call second_opinion again. The user can continue the conversation from here.",
    }


async def do_create_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Create a new chat session.

    Content format:
      Line 1: session name
      Line 2: model_name (or model_name@endpoint_name)
    """
    if not _session_manager:
        return {"error": "Session manager not available"}

    lines = content.strip().split("\n")
    if len(lines) < 2:
        return {"error": "Need 2 lines: session name, then model spec"}

    name = lines[0].strip()
    model_spec = lines[1].strip()

    if not name:
        return {"error": "Session name cannot be empty"}

    try:
        url, model, headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    sid = str(uuid.uuid4())[:8]
    try:
        _session_manager.create_session(
            session_id=sid,
            name=name,
            endpoint_url=url,
            model=model,
            rag=False,
            owner=owner,
        )
        # Store headers on session for future calls
        sess = _session_manager.get_session(sid)
        if sess and headers:
            sess.headers = headers
        try:
            from src.event_bus import fire_event
            fire_event("session_created", owner)
        except Exception:
            logger.debug("session_created event dispatch failed", exc_info=True)

        return {"session_id": sid, "name": name, "model": model, "endpoint_url": url}
    except Exception as e:
        logger.error(f"create_session failed: {e}")
        return {"error": f"Failed to create session: {e}"}


async def do_list_sessions(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List sessions sorted by most-recently-active first.

    Output includes a relative "last active" timestamp per row so the
    agent can answer "open my last chat" without guessing from titles.
    The most-recent session is always first in the list.

    Content = optional filter keyword (matches session name).
    """
    if not _session_manager:
        return {"error": "Session manager not available"}

    keyword = content.strip().lower() if content.strip() else None

    try:
        from core.database import SessionLocal, Session as DbSession
        from datetime import datetime, timezone

        # Pull every session's last_accessed from the DB so we can sort
        # by recency. In-memory sessions hold name + model + msg_count;
        # the DB row holds the timestamps.
        db = SessionLocal()
        try:
            db_rows = {r.id: r for r in db.query(DbSession).all()}
        finally:
            db.close()

        # SECURITY: scope to the caller's sessions. Passing None returned
        # every user's sessions, which the agent tool then exposed via the
        # "list my chats" reply.
        sessions = _session_manager.get_sessions_for_user(owner)
        rows = []
        for sid, sess in sessions.items():
            if keyword and keyword not in (sess.name or "").lower():
                continue
            db_row = db_rows.get(sid)
            # Prefer last_accessed; fall back to updated_at, then created_at.
            ts = None
            if db_row:
                ts = getattr(db_row, 'last_accessed', None) or getattr(db_row, 'updated_at', None) or getattr(db_row, 'created_at', None)
            rows.append((ts, sid, sess))

        # Sort by timestamp DESC; rows without a timestamp sink to the bottom.
        rows.sort(key=lambda r: r[0] or datetime.min, reverse=True)

        def _rel(ts):
            if not ts:
                return 'never'
            now = datetime.utcnow()
            try:
                if ts.tzinfo is not None:
                    now = datetime.now(timezone.utc)
                diff = (now - ts).total_seconds()
            except Exception:
                return 'unknown'
            if diff < 60: return 'just now'
            if diff < 3600: return f'{int(diff / 60)}m ago'
            if diff < 86400: return f'{int(diff / 3600)}h ago'
            if diff < 86400 * 7: return f'{int(diff / 86400)}d ago'
            return ts.strftime('%Y-%m-%d')

        lines = []
        for i, (ts, sid, sess) in enumerate(rows):
            if i >= 50:
                lines.append(f"... and {len(rows) - 50} more (showing first 50)")
                break
            safe_name = (sess.name or "Untitled").replace("[", "\\[").replace("]", "\\]")
            msg_count = getattr(sess, "message_count", 0) or 0
            model = getattr(sess, "model", "unknown")
            marker = " ← most recent" if i == 0 else ""
            lines.append(f"- **[{safe_name}](#session-{sid})** (id: `{sid}`, model: {model}, {msg_count} msgs, last active {_rel(ts)}){marker}")

        if not lines:
            return {"results": "No sessions found" + (f" matching '{keyword}'" if keyword else "") + "."}

        return {
            "results": (
                f"Found {len(rows)} session(s), sorted most-recent first:\n"
                + "\n".join(lines)
                + "\n\nAssistant: when replying to the user, preserve the chat-title markdown links exactly as shown, e.g. `[Chat](#session-id)`. Do not rewrite this as a plain, non-clickable table."
            )
        }
    except Exception as e:
        logger.error(f"list_sessions failed: {e}")
        return {"error": str(e)}


async def do_send_to_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Send a message to an existing session and get a response.

    Content format:
      Line 1: session_id
      Line 2+: message
    """
    from src.llm_core import llm_call_async
    from core.models import ChatMessage

    if not _session_manager:
        return {"error": "Session manager not available"}

    lines = content.strip().split("\n", 1)
    if len(lines) < 2:
        return {"error": "Need 2 lines: session_id, then message"}

    target_sid = lines[0].strip()
    message = lines[1].strip()

    sess = _session_manager.get_session(target_sid)
    if not sess:
        return {"error": f"Session '{target_sid}' not found"}

    # Owner-scope: reject access to another user's session
    if owner and getattr(sess, "owner", None) and sess.owner != owner:
        return {"error": f"Session '{target_sid}' not found"}

    if not message:
        return {"error": "No message provided"}

    try:
        # Build context from session history
        context = sess.get_context_messages()
        context.append({"role": "user", "content": message})

        response = await llm_call_async(
            sess.endpoint_url, sess.model, context,
            headers=sess.headers,
            timeout=AI_CHAT_TIMEOUT,
        )

        # Save both messages to session
        sess.add_message(ChatMessage("user", message))
        sess.add_message(ChatMessage("assistant", response))

        # Truncate for tool output
        if len(response) > 10000:
            response = response[:10000] + "\n... (truncated)"

        return {
            "session_id": target_sid,
            "session_name": sess.name,
            "response": response,
        }
    except Exception as e:
        logger.error(f"send_to_session failed: {e}")
        return {"error": f"Failed to send to session: {e}"}


async def stream_ai_tool(tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None):
    """Dispatcher for streaming AI tools. Yields events as async generator."""
    # Fallback: run non-streaming and yield final result
    desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    yield {"_final": True, "desc": desc, "result": result}


async def do_pipeline(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Execute a multi-step pipeline where each model's output feeds the next.

    Content format (JSON):
      {"steps": [
        {"model": "model_a", "instruction": "Draft an essay about X"},
        {"model": "model_b", "instruction": "Critique the following draft"},
        {"model": "model_a", "instruction": "Revise based on this critique"}
      ]}

    Or line format:
      Line 1: step1_model | step1_instruction
      Line 2: step2_model | step2_instruction
      ...
    """
    from src.llm_core import llm_call_async

    # Try JSON parse first
    steps = None
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict) and "steps" in data:
            steps = data["steps"]
        elif isinstance(data, list):
            steps = data
    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to line format: model | instruction
    if not steps:
        steps = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                parts = line.split("|", 1)
                steps.append({"model": parts[0].strip(), "instruction": parts[1].strip()})
            else:
                return {"error": "Each line must be: model | instruction (or use JSON format)"}

    if not steps:
        return {"error": "No pipeline steps provided"}
    if len(steps) > MAX_PIPELINE_STEPS:
        return {"error": f"Maximum {MAX_PIPELINE_STEPS} steps allowed"}

    # Resolve all models first (fail fast)
    resolved = []
    for i, step in enumerate(steps):
        model_spec = step.get("model", "").strip()
        instruction = step.get("instruction", "").strip()
        if not model_spec or not instruction:
            return {"error": f"Step {i + 1}: both 'model' and 'instruction' are required"}
        try:
            url, model, headers = _resolve_model(model_spec, owner=owner)
            resolved.append((url, model, headers, instruction))
        except ValueError as e:
            return {"error": f"Step {i + 1}: {e}"}

    # Execute pipeline
    step_outputs = []
    previous_output = None

    try:
        for i, (url, model, headers, instruction) in enumerate(resolved):
            if previous_output:
                user_content = (
                    f"Previous step's output:\n\n{previous_output}\n\n"
                    f"Your task: {instruction}"
                )
            else:
                user_content = instruction

            messages = [
                {"role": "system", "content": f"You are step {i + 1} in a processing pipeline. {instruction}"},
                {"role": "user", "content": user_content},
            ]

            response = await llm_call_async(
                url, model, messages, headers=headers, timeout=AI_CHAT_TIMEOUT
            )

            step_outputs.append({
                "step": i + 1,
                "model": model,
                "instruction": instruction,
                "output": response[:5000] if len(response) > 5000 else response,
            })

            previous_output = response

        # Build readable result
        result_lines = [f"# Pipeline Results ({len(resolved)} steps)\n"]
        for so in step_outputs:
            result_lines.append(f"## Step {so['step']}: {so['model']}")
            result_lines.append(f"*Instruction: {so['instruction']}*\n")
            result_lines.append(so["output"])
            result_lines.append("\n---\n")

        return {
            "results": "\n".join(result_lines),
            "steps": step_outputs,
            "final_output": previous_output,
        }
    except Exception as e:
        logger.error(f"pipeline failed at step {len(step_outputs) + 1}: {e}")
        return {"error": f"Pipeline failed at step {len(step_outputs) + 1}: {e}"}


# ---------------------------------------------------------------------------
# Session management tool
# ---------------------------------------------------------------------------

async def do_manage_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Manage sessions: rename, archive, delete, important, truncate, fork.

    Content format:
      Line 1: action (rename|archive|unarchive|delete|important|unimportant|truncate|fork)
      Line 2: target session_id (or "current" to use the active session)
      Line 3+: action-specific params (e.g. new name for rename, keep_count for truncate)
    """
    if not _session_manager:
        return {"error": "Session manager not available"}

    from src.database import SessionLocal, Session as DbSession

    # Accept BOTH the structured JSON args the tool schema advertises
    # ({action, session_id, value}) AND the legacy line-based format
    # (line1=action, line2=session_id, line3=value). Native function-calling
    # models send JSON; fenced-block callers send lines. Previously only the
    # line format was parsed, so a model that followed the schema (JSON) got
    # "Need at least 2 lines" / "Rename needs line 3" and couldn't drive it.
    _raw = (content or "").strip()
    action = ""
    target_sid = ""
    value = None      # the action param: new name (rename) / keep_count (truncate, fork)
    _list_filter = ""
    _parsed = None
    if _raw.startswith("{"):
        try:
            _parsed = json.loads(_raw)
        except Exception:
            _parsed = None
    if isinstance(_parsed, dict):
        action = str(_parsed.get("action") or "").strip().lower()
        target_sid = str(_parsed.get("session_id") or _parsed.get("session") or _parsed.get("id") or "").strip()
        _v = _parsed.get("value")
        if _v is None:
            _v = (_parsed.get("name") or _parsed.get("new_name")
                  or _parsed.get("title") or _parsed.get("keep_count"))
        value = None if _v is None else str(_v).strip()
        _list_filter = str(_parsed.get("filter") or "").strip()
    else:
        lines = _raw.split("\n")
        if not lines or not lines[0].strip():
            return {"error": "Missing action (rename|archive|delete|important|truncate|fork|list|switch)"}
        action = lines[0].strip().lower()
        target_sid = lines[1].strip() if len(lines) >= 2 else ""
        value = lines[2].strip() if len(lines) >= 3 else None
        _list_filter = "\n".join(lines[1:]).strip()

    if not action:
        return {"error": "Missing action (rename|archive|delete|important|truncate|fork|list|switch)"}

    # `list` alias — dispatch to do_list_sessions so the agent's natural
    # first guess (every other manage_* tool has a `list` action) works.
    if action == "list":
        return await do_list_sessions(_list_filter, session_id, owner=owner)

    if not target_sid:
        return {"error": "Need a session_id (or 'current' for the active chat)"}

    # Allow "current" to refer to the active session
    if target_sid.lower() == "current" and session_id:
        target_sid = session_id

    # `switch` / `open` / `select` / `view` — the agent reaches for
    # these when the user asks to "open" or "switch to" a session.
    # There's no server-side way to make the browser navigate, so we
    # just return a clickable anchor link the user can click. The
    # frontend's chat-history click delegate routes `#session-<id>`
    # to selectSession(). The agent's reply naturally embeds this
    # result so the user sees a single clickable line.
    def _session_query(db):
        query = db.query(DbSession).filter(DbSession.id == target_sid)
        if owner is not None:
            query = query.filter(DbSession.owner == owner)
        return query

    if action in ("switch", "open", "select", "view"):
        db = SessionLocal()
        try:
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            name = db_sess.name or target_sid
        finally:
            db.close()
        return {
            "action": action,
            "session_id": target_sid,
            "name": name,
            "results": f"[{name}](#session-{target_sid}) — click to open.",
        }

    db = SessionLocal()
    try:
        if action == "rename":
            if not value:
                return {"error": "rename needs a new name (the `value` arg, or line 3 in the legacy format)"}
            new_name = value
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            db_sess.name = new_name
            db.commit()
            _session_manager.update_session_name(target_sid, new_name)
            return {"action": "rename", "session_id": target_sid, "name": new_name,
                    "results": f"Session renamed to '{new_name}'"}

        elif action == "archive":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            db_sess.archived = True
            db.commit()
            return {"action": "archive", "session_id": target_sid,
                    "results": f"Session '{db_sess.name}' archived"}

        elif action == "unarchive":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            db_sess.archived = False
            db.commit()
            return {"action": "unarchive", "session_id": target_sid,
                    "results": f"Session '{db_sess.name}' unarchived"}

        elif action == "delete":
            if target_sid == session_id:
                return {"error": "Cannot delete the current session while chatting in it. Delete other sessions first."}
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Refusing to delete an unknown chat id; use the exact id from list_sessions."}
            if db_sess and db_sess.is_important:
                return {"error": f"Session '{db_sess.name}' is starred/favorited. Unstar it first before deleting."}
            try:
                ok = _session_manager.delete_session(target_sid)
                if not ok:
                    return {"error": f"Session '{target_sid}' was not deleted because it no longer exists."}
                return {"action": "delete", "session_id": target_sid,
                        "results": f"Session '{db_sess.name or target_sid}' deleted"}
            except Exception as e:
                return {"error": f"Failed to delete session: {e}"}

        elif action in ("important", "unimportant"):
            is_important = action == "important"
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            # Prevent AI from unstarring sessions — only the user can do that manually
            if not is_important and db_sess.is_important:
                return {"error": f"Session '{db_sess.name}' is starred by the user. Only the user can unstar sessions manually."}
            db_sess.is_important = is_important
            db.commit()
            status = "marked as important" if is_important else "unmarked as important"
            return {"action": action, "session_id": target_sid,
                    "results": f"Session '{db_sess.name}' {status}"}

        elif action == "truncate":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            keep_count = 10
            if value:
                try:
                    keep_count = int(value)
                except ValueError:
                    pass
            success = _session_manager.truncate_messages(target_sid, keep_count)
            if success:
                return {"action": "truncate", "session_id": target_sid,
                        "results": f"Session truncated to last {keep_count} messages"}
            return {"error": f"Failed to truncate session '{target_sid}'"}

        elif action == "fork":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            keep_count = 0  # 0 = all messages
            if value:
                try:
                    keep_count = int(value)
                except ValueError:
                    pass

            source = _session_manager.get_session(target_sid)
            if not source:
                return {"error": f"Session '{target_sid}' not found"}

            new_sid = str(uuid.uuid4())[:8]
            _session_manager.create_session(
                session_id=new_sid,
                name=f"Fork: {source.name}",
                endpoint_url=source.endpoint_url,
                model=source.model,
                rag=False,
                owner=owner,
            )
            # Copy messages
            history = source.get_context_messages()
            if keep_count > 0:
                history = history[:keep_count]
            from core.models import ChatMessage as InMemoryMsg
            new_sess = _session_manager.get_session(new_sid)
            for msg in history:
                new_sess.add_message(InMemoryMsg(msg["role"], msg["content"]))
            try:
                from src.event_bus import fire_event
                fire_event("session_created", owner)
            except Exception:
                logger.debug("session_created event dispatch failed", exc_info=True)

            return {"action": "fork", "session_id": new_sid,
                    "source_session": target_sid, "messages_copied": len(history),
                    "results": f"Forked session '{source.name}' -> new session {new_sid} ({len(history)} messages)"}

        else:
            return {"error": f"Unknown action '{action}'. Use: list, switch, rename, archive, unarchive, delete, important, unimportant, truncate, fork"}
    except Exception as e:
        logger.error(f"manage_session failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Memory management tool
# ---------------------------------------------------------------------------

async def do_manage_memory(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Manage memories: list, add, edit, delete, search, pin, audit.

    Content format:
      Line 1: action (list|add|edit|delete|search|pin|audit)
      Line 2+: action-specific params

    Actions:
      list                    — list all memories (optional line 2: category filter)
      add                     — line 2: text, optional line 3: category (fact|event|contact|preference)
      edit                    — line 2: memory_id, line 3: new text
      delete                  — line 2: memory_id
      search                  — line 2: query
      pin                     — line 2: memory_id  (sets category=identity, shows as [PINNED])
      audit                   — scan all memories, flag duplicates/stale/vague entries
    """
    _ensure_memory_manager()

    lines = content.strip().split("\n")
    if not lines:
        return {"error": "Need at least 1 line: action"}

    action = lines[0].strip().lower()

    if action == "list":
        category_filter = lines[1].strip().lower() if len(lines) > 1 and lines[1].strip() else None
        memories = _memory_manager.load(owner=owner)
        if category_filter:
            memories = [m for m in memories if m.get("category", "").lower() == category_filter]
        if not memories:
            return {"results": "No memories found" + (f" in category '{category_filter}'" if category_filter else "") + "."}

        result_lines = [f"Found {len(memories)} memory entries:\n"]
        for m in memories:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            if len(text) > 150:
                text = text[:150] + "..."
            result_lines.append(f"- [{cat}] `{mid}` — {text}")
        return {"results": "\n".join(result_lines)}

    elif action == "add":
        if len(lines) < 2:
            return {"error": "Add needs line 2: memory text"}
        text = lines[1].strip()
        category = lines[2].strip().lower() if len(lines) > 2 and lines[2].strip() else "fact"
        if not text:
            return {"error": "Memory text cannot be empty"}

        entry = _memory_manager.add_entry(text, source="ai_agent", category=category, owner=owner)
        memories = _memory_manager.load_all()
        memories.append(entry)
        _memory_manager.save(memories)

        # Update vector index if available
        if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            try:
                _memory_vector.add(entry["id"], text)
            except Exception:
                pass
        try:
            from src.event_bus import fire_event
            fire_event("memory_added", owner)
        except Exception:
            logger.debug("memory_added event dispatch failed", exc_info=True)

        return {"action": "add", "memory_id": entry["id"],
                "results": f"Memory added: [{category}] {text}"}

    elif action == "edit":
        if len(lines) < 3:
            return {"error": "Edit needs line 2: memory_id, line 3: new text"}
        memory_id = lines[1].strip()
        new_text = lines[2].strip()
        if not new_text:
            return {"error": "New text cannot be empty"}

        memories = _memory_manager.load_all()
        found = False
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                # Verify ownership
                if owner and m.get("owner") != owner:
                    return {"error": f"Memory '{memory_id}' not found"}
                m["text"] = new_text
                m["timestamp"] = int(time.time())
                found = True
                full_id = m["id"]
                break
        if not found:
            return {"error": f"Memory '{memory_id}' not found"}
        _memory_manager.save(memories)

        # Update vector index
        if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            try:
                _memory_vector.add(full_id, new_text)
            except Exception:
                pass

        return {"action": "edit", "memory_id": memory_id,
                "results": f"Memory updated: {new_text}"}

    elif action == "delete":
        if len(lines) < 2:
            return {"error": "Delete needs line 2: memory_id"}
        memory_id = lines[1].strip()

        memories = _memory_manager.load_all()
        original_len = len(memories)
        full_id = None
        delete_id = None
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                # Verify ownership
                if owner and m.get("owner") != owner:
                    return {"error": f"Memory '{memory_id}' not found"}
                full_id = m["id"]
                delete_id = m["id"]
                break
        memories = [m for m in memories if m.get("id") != delete_id]
        if len(memories) == original_len:
            return {"error": f"Memory '{memory_id}' not found"}
        _memory_manager.save(memories)

        # Remove from vector index
        if _memory_vector and full_id and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            try:
                _memory_vector.remove(full_id)
            except Exception:
                pass

        return {"action": "delete", "memory_id": memory_id,
                "results": f"Memory '{memory_id}' deleted"}

    elif action == "search":
        if len(lines) < 2:
            return {"error": "Search needs line 2: query"}
        query = lines[1].strip()
        memories = _memory_manager.load(owner=owner)

        if hasattr(_memory_manager, 'get_relevant_memories'):
            results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
        else:
            # Fallback: simple text search
            query_lower = query.lower()
            results = [m for m in memories if query_lower in m.get("text", "").lower()][:20]

        if not results:
            return {"results": f"No memories found matching '{query}'."}
        result_lines = [f"Found {len(results)} matching memories:\n"]
        for m in results:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            result_lines.append(f"- [{cat}] `{mid}` — {text}")
        return {"results": "\n".join(result_lines)}

    elif action == "pin":
        if len(lines) < 2:
            return {"error": "Pin needs line 2: memory_id"}
        memory_id = lines[1].strip()
        memories = _memory_manager.load_all()
        found = False
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                if owner and m.get("owner") != owner:
                    return {"error": f"Memory '{memory_id}' not found"}
                m["category"] = "identity"
                m["pinned"] = True
                found = True
                pinned_text = m.get("text", "")[:80]
                break
        if not found:
            return {"error": f"Memory '{memory_id}' not found"}
        _memory_manager.save(memories)
        return {"action": "pin", "memory_id": memory_id,
                "results": f"Memory pinned (always-load): {pinned_text}"}

    elif action == "audit":
        import time as _time
        memories = _memory_manager.load(owner=owner)
        if not memories:
            return {"results": "No memories found."}

        try:
            from src.memory import AUTO_PIN_THRESHOLD as _APT
        except Exception:
            _APT = 10

        now = _time.time()
        seen_texts: dict = {}
        flags: dict = {}

        for m in memories:
            mid = m.get("id", "?")[:8]
            text = m.get("text", "").strip()
            uses = int(m.get("uses", 0) or 0)
            age_days = (now - int(m.get("timestamp", now) or now)) / 86400

            # duplicate detection on first 60 normalised chars
            text_key = " ".join(text.lower().split())[:60]
            if text_key in seen_texts:
                flags.setdefault(mid, []).append(f"DUPLICATE of {seen_texts[text_key]}")
                flags.setdefault(seen_texts[text_key], []).append(f"DUPLICATE (see {mid})")
            else:
                seen_texts[text_key] = mid

            if len(text) < 15:
                flags.setdefault(mid, []).append("VAGUE (too short)")

            stale_kw = ["is researching", "researching", "studying", "looking into", "investigating", "for a project"]
            if any(kw in text.lower() for kw in stale_kw):
                flags.setdefault(mid, []).append("POSSIBLY STALE (project/research reference)")

            if uses == 0 and age_days > 7:
                flags.setdefault(mid, []).append(f"NEVER USED ({int(age_days)}d old — consider deleting)")

        flagged_ids = set(flags.keys())
        dirty = [(m, flags[m.get("id", "?")[:8]]) for m in memories if m.get("id", "?")[:8] in flagged_ids]
        clean = [m for m in memories if m.get("id", "?")[:8] not in flagged_ids]

        # sort clean: pinned first, then by uses desc
        clean_sorted = sorted(clean, key=lambda m: (-int(m.get("pinned", False)), -int(m.get("uses", 0) or 0)))

        out = [f"## Memory Audit — {len(memories)} entries (auto-pin at {_APT} uses)\n"]

        if dirty:
            out.append(f"### Flagged ({len(dirty)})\n")
            for m, issues in dirty:
                mid = m.get("id", "?")[:8]
                cat = m.get("category", "fact")
                uses = int(m.get("uses", 0) or 0)
                text = m.get("text", "")[:100]
                out.append(f"- `{mid}` [{cat}] ×{uses} {text}")
                for issue in issues:
                    out.append(f"  ⚠ {issue}")
            out.append("")

        out.append(f"### Clean ({len(clean_sorted)})\n")
        for m in clean_sorted:
            mid = m.get("id", "?")[:8]
            cat = m.get("category", "fact")
            uses = int(m.get("uses", 0) or 0)
            pinned = m.get("pinned") or cat == "identity"
            pin_tag = " [PINNED]" if pinned else ""
            near_tag = f" ⬆ {_APT - uses} uses to auto-pin" if not pinned and uses >= (_APT // 2) else ""
            text = m.get("text", "")[:100]
            out.append(f"- `{mid}` [{cat}]{pin_tag} ×{uses}{near_tag} {text}")

        out.append("\n---")
        out.append('To pin:    raphael {"action":"memory_pin","id":"<id>"}')
        out.append('To delete: raphael {"action":"memory_delete","id":"<id>"}')

        return {"results": "\n".join(out)}

    else:
        return {"error": f"Unknown action '{action}'. Use: list, add, edit, delete, search, pin, audit"}


# ---------------------------------------------------------------------------
# Save research to brain tool
# ---------------------------------------------------------------------------

async def do_save_research_to_brain(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Extract key facts from a deep research report and save them to brain.

    Args (JSON): {"id": "<research_id>", "max_facts": 5}
    Omit id to use the most recent research for the owner.
    """
    import json as _json
    import re as _re
    from pathlib import Path as _Path
    from src.constants import DEEP_RESEARCH_DIR
    from src.settings import get_setting
    from src.llm_core import llm_call_async

    _ensure_memory_manager()

    args = {}
    stripped = (content or "").strip()
    if stripped.startswith("{"):
        try:
            args = _json.loads(stripped)
        except Exception:
            pass

    rid = (args.get("id") or args.get("session_id") or "").strip()
    try:
        max_facts = max(1, min(10, int(args.get("max_facts", 5))))
    except (TypeError, ValueError):
        max_facts = 5

    data_dir = _Path(DEEP_RESEARCH_DIR)

    # Find the research file
    if rid:
        if not _re.fullmatch(r"[A-Za-z0-9_-]+", rid):
            return {"error": "Invalid research id."}
        p = data_dir / f"{rid}.json"
        if not p.exists():
            return {"error": f"Research '{rid}' not found."}
        d = _json.loads(p.read_text(encoding="utf-8"))
    else:
        items = []
        if data_dir.exists():
            for fp in data_dir.glob("*.json"):
                try:
                    dat = _json.loads(fp.read_text(encoding="utf-8"))
                    if owner and dat.get("owner") and dat.get("owner") != owner:
                        continue
                    items.append((dat.get("completed_at", 0) or 0, fp, dat))
                except Exception:
                    continue
        if not items:
            return {"error": "No research found. Run a research first, or provide a research id."}
        items.sort(reverse=True)
        _, p, d = items[0]
        rid = p.stem

    query = d.get("query", "(untitled)")
    report = d.get("result") or d.get("raw_report") or ""
    if not report:
        return {"error": f"No report content in research '{rid}'."}

    # Use LLM to extract key facts
    facts = []
    try:
        model_spec = get_setting("research_model", "").strip()
        if model_spec:
            url, model, headers = _resolve_model(model_spec, owner=owner)
        else:
            from src.database import SessionLocal, ModelEndpoint
            from src.endpoint_resolver import build_chat_url, build_headers, resolve_endpoint_runtime
            from src.auth_helpers import owner_filter as _owner_filter
            db = SessionLocal()
            try:
                q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
                if owner:
                    q = _owner_filter(q, ModelEndpoint, owner)
                ep = q.first()
                if not ep:
                    raise ValueError("No enabled endpoint found")
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                models = _json.loads(ep.cached_models or "[]")
                model = models[0] if models and isinstance(models[0], str) else (models[0].get("id", "") if models else "")
                if not model:
                    raise ValueError("No cached model on endpoint")
                url, headers = build_chat_url(base), build_headers(api_key, base)
            finally:
                db.close()

        prompt = (
            f"Extract the {max_facts} most important facts or findings from this research report.\n"
            f"Research question: {query}\n\n"
            f"Report:\n{report[:6000]}\n\n"
            f"Return ONLY a JSON array of {max_facts} concise strings. "
            f"Each = one key finding. Be specific; include numbers, names, dates where relevant."
        )
        response = await llm_call_async(
            url, model,
            [{"role": "user", "content": prompt}],
            headers=headers,
            temperature=0.1,
            max_tokens=1024,
            timeout=45,
            max_retries=1,
        )
        clean = response.strip()
        if clean.startswith("```"):
            clean = _re.sub(r'^```(?:json)?\s*', '', clean)
            clean = _re.sub(r'\s*```$', '', clean)
        match = _re.search(r'\[[\s\S]*\]', clean)
        if match:
            parsed = _json.loads(match.group())
            if isinstance(parsed, list):
                facts = [f.strip() for f in parsed if isinstance(f, str) and f.strip()]
    except Exception as e:
        logger.warning(f"save_research_to_brain: LLM extraction failed: {e}")
        facts = []

    if not facts:
        facts = [f"Research on '{query}': {report[:600].strip()}"]

    # Save to brain — single load, derive owner slice from it
    all_entries = _memory_manager.load_all()
    owner_entries = [e for e in all_entries if not owner or e.get("owner") == owner]
    saved = []
    for fact in facts:
        if not fact:
            continue
        if _memory_manager.find_duplicates(fact, owner_entries):
            continue
        entry = _memory_manager.add_entry(fact, source="research", category="research", owner=owner)
        all_entries.append(entry)
        owner_entries.append(entry)
        saved.append((entry["id"], fact))

    if saved:
        _memory_manager.save(all_entries)
        if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
            for mid, text in saved:
                try:
                    _memory_vector.add(mid, text)
                except Exception:
                    pass
        try:
            from src.event_bus import fire_event
            fire_event("memory_added", owner)
        except Exception:
            pass

    if not saved:
        return {"output": "All extracted facts already exist in brain — nothing new saved.", "exit_code": 0}

    lines = "\n".join(f"- {f}" for _, f in saved)
    return {
        "output": f"Saved {len(saved)} fact(s) from '{query}' to brain:\n{lines}",
        "exit_code": 0,
    }



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Auto-evolve helper — rewrites a single memory after it gets auto-pinned
# ---------------------------------------------------------------------------

async def auto_evolve_memory(memory_id: str, old_text: str, owner: Optional[str] = None) -> None:
    """Rewrite a memory entry after it crosses the auto-pin threshold.

    Accepts old_text directly (caller already has it) to skip a load_all scan.
    Fires as asyncio.ensure_future so it never blocks the triggering chat response.
    """
    if not old_text.strip():
        return
    _ensure_memory_manager()
    try:
        from src.llm_core import llm_call_async
        from src.settings import get_setting
        prompt = (
            "Rewrite this memory entry to be more concise, precise, and useful as a persistent fact.\n"
            "Keep all key information. Remove filler words. Target: under 120 characters if possible.\n"
            f"Return ONLY the rewritten memory text, nothing else.\n\nMemory: {old_text}"
        )
        model_spec = get_setting("research_model", "").strip() or get_setting("default_model", "").strip()
        url, model, headers = _resolve_model(model_spec, owner=owner)
        response = await llm_call_async(
            url, model, [{"role": "user", "content": prompt}],
            headers=headers, max_tokens=150, timeout=45, max_retries=3,
        )
        new_text = response.strip().strip('"').strip("'")
        if not new_text or new_text == old_text:
            return
        # Load all entries once to apply the update and save
        memories = _memory_manager.load_all()
        target = next((m for m in memories if m.get("id") == memory_id), None)
        if target:
            target["text"] = new_text
            _memory_manager.save(memories)
            logger.info("Auto-evolved memory %s: %r → %r", memory_id[:8], old_text[:60], new_text[:60])
    except Exception as e:
        logger.warning("auto_evolve_memory failed for %s: %s", memory_id[:8], e)


# Raphael -- skill auditor / merger
# ---------------------------------------------------------------------------

async def do_raphael(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Audit, merge, or delete Odysseus skills."""
    import json as _json
    import re as _re
    import shutil as _shutil
    from pathlib import Path as _Path

    from src.constants import DATA_DIR as _DATA_DIR
    SKILLS_ROOT = _Path(_DATA_DIR) / "skills"

    args = {}
    stripped = (content or "").strip()
    if stripped.startswith("{"):
        try:
            args = _json.loads(stripped)
        except Exception as _e:
            return {"error": f"Invalid JSON: {_e}", "exit_code": 1}

    action = (args.get("action") or "audit").lower()
    dry_run = bool(args.get("dry_run", False))

    def _find_skill(slug: str):
        if not slug or ".." in slug or "/" in slug or "\\" in slug:
            return None, None
        for cat_dir in SKILLS_ROOT.iterdir():
            if not cat_dir.is_dir():
                continue
            md = cat_dir / slug / "SKILL.md"
            if md.exists():
                return md, cat_dir.name
        return None, None

    def _read_skill(md_path) -> dict:
        try:
            text = md_path.read_text(encoding="utf-8")
            name_m = _re.search(r"^name:\s*(.+)$", text, _re.MULTILINE)
            desc_m = _re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', text, _re.MULTILINE)
            stat_m = _re.search(r"^status:\s*(.+)$", text, _re.MULTILINE)
            return {
                "path": md_path,
                "slug": md_path.parent.name,
                "category": md_path.parent.parent.name,
                "name": name_m.group(1).strip() if name_m else md_path.parent.name,
                "description": desc_m.group(1).strip().strip('"\'') if desc_m else "",
                "status": stat_m.group(1).strip() if stat_m else "unknown",
                "raw": text,
            }
        except Exception as e:
            return {
                "path": md_path, "slug": md_path.parent.name,
                "category": md_path.parent.parent.name, "name": md_path.parent.name,
                "description": f"(read error: {e})", "status": "unknown", "raw": "",
            }

    _TIER_ORDER = ["common", "extra", "unique", "ultimate"]
    _THRESHOLDS = {"common": 10, "extra": 50, "unique": 150}

    # audit
    if action == "audit":
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR as _DATA_DIR
        _sm = SkillsManager(str(_DATA_DIR))
        skills = _sm.load(owner=owner) if owner else _sm.load_all()
        if not skills:
            return {"output": "No skills found.", "exit_code": 0}
        by_cat: dict = {}
        for s in skills:
            by_cat.setdefault(s.get("category", "general"), []).append(s)
        lines = [f"## Skill Audit -- {len(skills)} skills\n"]
        # Evolution-ready section
        evo_ready = []
        for s in skills:
            t = s.get("tier", "common")
            u = int(s.get("uses", 0))
            if t in _THRESHOLDS and u >= _THRESHOLDS[t]:
                nxt = _TIER_ORDER[_TIER_ORDER.index(t) + 1]
                evo_ready.append((s["name"], t, nxt, u))
        if evo_ready:
            lines.append("### ⬆ Ready to Evolve\n")
            for slug, cur, nxt, u in evo_ready:
                lines.append(f"- **{slug}**: {cur} → {nxt} ({u} uses)")
            lines.append("")
        for cat, cat_skills in sorted(by_cat.items()):
            lines.append(f"### {cat} ({len(cat_skills)})")
            for s in cat_skills:
                tier = (s.get("tier") or "common").upper()
                uses = int(s.get("uses", 0))
                flag = ""
                if s.get("status") == "draft":
                    flag = " [DRAFT]"
                elif not s.get("description") or len(s.get("description", "")) < 10:
                    flag = " [NO DESCRIPTION]"
                lines.append(f"- **{s['name']}** [{tier}] ×{uses}{flag}: {s.get('description','')[:100]}")
            lines.append("")
        # Suggestions section
        lines.append("---")
        lines.append("## 🎯 Suggested Actions\n")
        # Top evolve suggestions
        evo_sorted = sorted(evo_ready, key=lambda x: x[3], reverse=True)
        if evo_sorted:
            lines.append("**Evolve these skills (highest use → next tier):**")
            for slug, cur, nxt, u in evo_sorted[:3]:
                lines.append(f'- `raphael {{"action":"evolve","target":"{slug}"}}` — {cur}→{nxt} ({u} uses)')
            lines.append("")
        # Dead skills (0 uses, not system)
        dead = [s for s in skills if int(s.get("uses", 0)) == 0 and s.get("category") not in ("system",) and s["name"] not in ("raphael",)]
        if dead:
            lines.append("**Delete unused skills (0 uses):**")
            for s in dead[:5]:
                lines.append(f'- `raphael {{"action":"delete","target":"{s["name"]}"}}`')
            lines.append("")
        # Top used skills not yet evolved
        top_used = sorted([s for s in skills if int(s.get("uses", 0)) > 0 and s["name"] not in [x[0] for x in evo_ready]], key=lambda x: x.get("uses", 0), reverse=True)
        if top_used:
            lines.append("**Most-used skills (not yet evolution-ready):**")
            for s in top_used[:3]:
                t = s.get("tier", "common")
                thresh = _THRESHOLDS.get(t, "?")
                lines.append(f'- **{s["name"]}** ×{s.get("uses",0)} (needs {thresh} for next tier)')
            lines.append("")
        lines.append("---")
        lines.append('Actions: raphael {"action":"evolve","target":"slug"} | {"action":"merge","skills":["a","b"],"target":"c","category":"cat"} | {"action":"delete","target":"slug"}')
        lines.append('Ingest new content: beelzebub {"action":"absorb","content":"..."}')
        lines.append('Add "dry_run":true to preview without writing.')
        return {"output": "\n".join(lines), "exit_code": 0}

    # merge
    if action == "merge":
        skill_slugs = args.get("skills", [])
        target_slug = (args.get("target") or (skill_slugs[0] if skill_slugs else "")).strip()
        target_cat = (args.get("category") or "general").strip()
        if not skill_slugs or len(skill_slugs) < 2:
            return {"error": "merge requires at least 2 skill slugs in 'skills'.", "exit_code": 1}
        if not target_slug:
            return {"error": "merge requires 'target' slug.", "exit_code": 1}
        sources = []
        missing = []
        for slug in skill_slugs:
            md_path, _ = _find_skill(slug)
            if md_path:
                sources.append(_read_skill(md_path))
            else:
                missing.append(slug)
        if missing:
            return {"error": f"Skills not found: {missing}", "exit_code": 1}
        combined_raw = "\n\n---\n\n".join(
            f"# Source: {s['slug']} ({s['category']}/)\n{s['raw']}" for s in sources
        )
        prompt = (
            f"Merge {len(sources)} Odysseus skill files into one comprehensive master skill.\n"
            f"Target slug: {target_slug}, category: {target_cat}\n\n"
            f"Rules:\n"
            f"- Preserve ALL useful procedures from every source -- nothing valuable is lost\n"
            f"- Write a single clean SKILL.md with proper YAML frontmatter\n"
            f"- The merged skill must be more complete than any individual source\n"
            f"- Output ONLY the raw SKILL.md content, no explanation\n\n"
            f"Sources:\n{combined_raw[:8000]}"
        )
        merged_content = None
        try:
            from src.settings import get_setting
            from src.llm_core import llm_call_async
            model_spec = get_setting("research_model", "").strip() or get_setting("default_model", "").strip()
            url, model, headers = _resolve_model(model_spec, owner=owner)
            response = await llm_call_async(
                url, model,
                [{"role": "user", "content": prompt}],
                headers=headers, temperature=0.2, max_tokens=4096, timeout=60, max_retries=1,
            )
            clean = response.strip()
            if clean.startswith("```"):
                clean = _re.sub(r"^```(?:markdown)?\s*", "", clean)
                clean = _re.sub(r"\s*```$", "", clean)
            merged_content = clean.strip()
        except Exception as e:
            logger.warning(f"do_raphael merge: LLM failed: {e}")
            merged_content = (
                f"---\nname: {target_slug}\ndescription: Merged skill combining "
                + ", ".join(s["slug"] for s in sources)
                + f"\nversion: 1.0.0\ncategory: {target_cat}\nstatus: published\n---\n\n"
                + "\n\n".join(s["raw"] for s in sources)
            )
        if dry_run:
            return {"output": f"DRY RUN -- merged preview:\n\n{merged_content[:3000]}", "exit_code": 0}
        target_dir = SKILLS_ROOT / target_cat / target_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(merged_content, encoding="utf-8")
        deleted = []
        for s in sources:
            if s["slug"] == target_slug and s["category"] == target_cat:
                continue
            try:
                _shutil.rmtree(s["path"].parent)
                deleted.append(f"{s['category']}/{s['slug']}")
            except Exception as e:
                logger.warning(f"do_raphael: could not delete {s['slug']}: {e}")
        out = f"Merged {len(sources)} skills into {target_cat}/{target_slug}."
        if deleted:
            out += f"\nAbsorbed and removed: {deleted}"
        return {"output": out, "exit_code": 0}

    # delete
    if action == "delete":
        target = (args.get("target") or "").strip()
        if not target:
            return {"error": "delete requires 'target' slug.", "exit_code": 1}
        md_path, cat = _find_skill(target)
        if not md_path:
            return {"error": f"Skill '{target}' not found.", "exit_code": 1}
        if dry_run:
            return {"output": f"DRY RUN -- would delete {cat}/{target}", "exit_code": 0}
        _shutil.rmtree(md_path.parent)
        return {"output": f"Deleted {cat}/{target}.", "exit_code": 0}

    # evolve — rewrite a skill at the next tier using the LLM
    # evolve_all — evolve every ready skill in one pass
    if action == "evolve_all":
        import asyncio as _asyncio
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR as _DATA_DIR
        _sm = SkillsManager(str(_DATA_DIR))
        skills = _sm.load(owner=owner) if owner else _sm.load_all()
        ready = [
            s for s in skills
            if s.get("tier", "common") in _THRESHOLDS
            and int(s.get("uses", 0)) >= _THRESHOLDS[s.get("tier", "common")]
        ]
        if not ready:
            return {"output": "No skills are ready to evolve.", "exit_code": 0}

        # Cap concurrent LLM calls -- firing all of them at once reliably blows
        # through free-tier OpenRouter's per-minute rate limit (each item then
        # permanently fails with no retry budget left). Capping concurrency
        # spreads the requests out instead of bursting them.
        _evolve_sem = _asyncio.Semaphore(_BULK_EVOLVE_CONCURRENCY)

        async def _evolve_one(slug: str) -> str:
            async with _evolve_sem:
                r = await do_raphael(
                    f'{{"action":"evolve","target":"{slug}"}}',
                    session_id=session_id, owner=owner,
                )
            status = r.get("output") or r.get("error") or "?"
            return f"- **{slug}**: {status[:120]}"

        results = list(await _asyncio.gather(*[_evolve_one(s["name"]) for s in ready]))
        return {"output": f"evolve_all: {len(ready)} skills processed\n" + "\n".join(results), "exit_code": 0}

    if action == "evolve":
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR as _DATA_DIR
        from src.settings import get_setting
        from src.llm_core import llm_call_async

        target_slug = (args.get("target") or "").strip()
        force = bool(args.get("force", False))
        if not target_slug:
            return {"error": "evolve requires 'target' (skill slug).", "exit_code": 1}

        _sm = SkillsManager(str(_DATA_DIR))
        skill_dicts = _sm.load(owner=owner) if owner else _sm.load_all()
        skill_data = next((s for s in skill_dicts if s["name"] == target_slug), None)
        if not skill_data:
            return {"error": f"Skill '{target_slug}' not found.", "exit_code": 1}

        current_tier = (skill_data.get("tier") or "common")
        if current_tier not in _TIER_ORDER:
            current_tier = "common"
        tier_idx = _TIER_ORDER.index(current_tier)
        if tier_idx >= len(_TIER_ORDER) - 1:
            return {"output": f"'{target_slug}' is already at ultimate tier — highest possible.", "exit_code": 0}
        next_tier = _TIER_ORDER[tier_idx + 1]

        uses = int(skill_data.get("uses", 0))
        threshold = _THRESHOLDS.get(current_tier, 9999)
        if uses < threshold and not force:
            return {
                "output": (
                    f"'{target_slug}' not ready to evolve yet.\n"
                    f"  Tier: {current_tier} → {next_tier}\n"
                    f"  Uses: {uses}/{threshold}\n"
                    f"  Add force:true to evolve anyway."
                ),
                "exit_code": 0,
            }

        md_path, _ = _find_skill(target_slug)
        if not md_path:
            return {"error": f"Skill file for '{target_slug}' not found on disk.", "exit_code": 1}
        raw = md_path.read_text(encoding="utf-8")

        if dry_run:
            return {
                "output": (
                    f"[DRY RUN] Would evolve '{target_slug}': {current_tier} → {next_tier}\n"
                    f"Uses: {uses}/{threshold}"
                ),
                "exit_code": 0,
            }

        tier_desc = {
            "extra":    "proven and reliable — more detailed steps, handles common edge cases",
            "unique":   "comprehensive and battle-tested — covers all known failure modes, full verification",
            "ultimate": "mastered and exhaustive — authoritative, injected into the agent's core context, always followed",
        }
        prompt = (
            f"You are evolving an Odysseus skill file from tier '{current_tier}' to '{next_tier}'.\n"
            f"This skill has been applied {uses} times and earned elevation.\n\n"
            f"Target tier '{next_tier}' means: {tier_desc.get(next_tier, next_tier)}\n\n"
            f"Rules:\n"
            f"- Make the skill MORE comprehensive than the current version\n"
            f"- Expand procedure steps to be more precise and complete\n"
            f"- Add more pitfalls based on real-world failure modes\n"
            f"- Set tier: {next_tier} in frontmatter\n"
            f"- Bump version (increment minor number)\n"
            f"- Set confidence: {0.97 if next_tier in ('unique','ultimate') else 0.93}\n"
            f"- Output ONLY the raw SKILL.md content, no explanation\n\n"
            f"Current SKILL.md:\n{raw[:6000]}"
        )
        try:
            model_spec = get_setting("research_model", "").strip() or get_setting("default_model", "").strip()
            url, model, headers = _resolve_model(model_spec, owner=owner)
            response = await _retry_on_rate_limit(lambda: llm_call_async(
                url, model,
                [{"role": "user", "content": prompt}],
                headers=headers, temperature=0.3, max_tokens=4096, timeout=60, max_retries=1,
            ))
            evolved = response.strip()
            if evolved.startswith("```"):
                evolved = _re.sub(r"^```(?:markdown)?\s*", "", evolved)
                evolved = _re.sub(r"\n```\s*$", "", evolved)
            evolved = evolved.strip()
        except Exception as e:
            return {"error": f"LLM call failed during evolve: {e}", "exit_code": 1}

        md_path.write_text(evolved, encoding="utf-8")
        return {
            "output": (
                f"Skill '{target_slug}' evolved: {current_tier} → {next_tier}\n"
                f"Uses at evolution: {uses}"
                + ("\nThis skill is now injected into the agent's core context." if next_tier == "ultimate" else "")
            ),
            "exit_code": 0,
        }

    # absorb — synthesize external content into a new skill (Predator mechanic)
    if action == "absorb":
        from src.settings import get_setting
        from src.llm_core import llm_call_async

        raw_content = (args.get("content") or "").strip()
        target_cat = (args.get("category") or "general").strip()
        if not raw_content:
            return {"error": "absorb requires 'content' (text, workflow, or URL content to absorb).", "exit_code": 1}

        prompt = (
            f"You are synthesizing external knowledge into an Odysseus SKILL.md file.\n"
            f"Analyze the provided content and extract a reusable procedure or skill from it.\n\n"
            f"Rules:\n"
            f"- Identify the core repeatable skill or workflow in the content\n"
            f"- Write a clean SKILL.md with proper YAML frontmatter\n"
            f"- Set tier: common (newly absorbed skills start at common tier)\n"
            f"- Set status: published\n"
            f"- Set category: {target_cat}\n"
            f"- Set confidence: 0.80\n"
            f"- Set source: absorbed\n"
            f"- Write clear When to Use, Procedure, and Pitfalls sections\n"
            f"- Output ONLY the raw SKILL.md content, no explanation\n\n"
            f"Content to absorb:\n{raw_content[:6000]}"
        )
        try:
            model_spec = get_setting("research_model", "").strip() or get_setting("default_model", "").strip()
            url, model, headers = _resolve_model(model_spec, owner=owner)
            response = await llm_call_async(
                url, model,
                [{"role": "user", "content": prompt}],
                headers=headers, temperature=0.3, max_tokens=4096, timeout=60, max_retries=1,
            )
            skill_md = response.strip()
            if skill_md.startswith("```"):
                skill_md = _re.sub(r"^```(?:markdown)?\s*", "", skill_md)
                skill_md = _re.sub(r"\n```\s*$", "", skill_md)
            skill_md = skill_md.strip()
        except Exception as e:
            return {"error": f"LLM call failed during absorb: {e}", "exit_code": 1}

        if dry_run:
            return {"output": f"[DRY RUN] Absorbed skill preview:\n\n{skill_md[:2000]}", "exit_code": 0}

        # Extract slug from the generated frontmatter
        name_m = _re.search(r"^name:\s*(.+)$", skill_md, _re.MULTILINE)
        slug = name_m.group(1).strip().strip('"\'') if name_m else "absorbed-skill"
        # Sanitize slug
        slug = _re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:60] or "absorbed-skill"

        skill_dir = SKILLS_ROOT / target_cat / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

        return {
            "output": f"Absorbed new skill '{slug}' into {target_cat}/.\nIt starts at common tier and will evolve with use.",
            "exit_code": 0,
        }

    # memory actions — delegate to do_manage_memory
    if action in ("memory_audit", "memory_list", "memory_pin", "memory_delete", "memory_add"):
        sub = action[len("memory_"):]  # "audit", "list", "pin", "delete", "add"
        if sub == "pin":
            memory_id = (args.get("id") or "").strip()
            if not memory_id:
                return {"error": "memory_pin requires 'id'", "exit_code": 1}
            content_for_memory = f"pin\n{memory_id}"
        elif sub == "delete":
            memory_id = (args.get("id") or "").strip()
            if not memory_id:
                return {"error": "memory_delete requires 'id'", "exit_code": 1}
            content_for_memory = f"delete\n{memory_id}"
        elif sub == "add":
            text = (args.get("text") or "").strip()
            category = (args.get("category") or "fact").strip()
            if not text:
                return {"error": "memory_add requires 'text'", "exit_code": 1}
            content_for_memory = f"add\n{text}\n{category}"
        else:
            content_for_memory = sub  # "audit" or "list"
        result = await do_manage_memory(content_for_memory, session_id=session_id, owner=owner)
        output = result.get("results") or result.get("error") or str(result)
        return {"output": output, "exit_code": 0 if "error" not in result else 1}

    # memory_evolve / memory_evolve_all — rewrite memories to be more concise and precise
    if action in ("memory_evolve", "memory_evolve_all"):
        from src.llm_core import llm_call_async
        memories = _ensure_memory_manager().load(owner=owner)
        if not memories:
            return {"output": "No memories found.", "exit_code": 0}

        if action == "memory_evolve":
            memory_id = (args.get("id") or "").strip()
            if not memory_id:
                return {"error": "memory_evolve requires 'id'", "exit_code": 1}
            targets = [m for m in memories if m.get("id", "").startswith(memory_id)]
            if not targets:
                return {"error": f"Memory '{memory_id}' not found.", "exit_code": 1}
        else:
            targets = memories

        import asyncio as _asyncio
        from src.settings import get_setting

        _EVOLVE_PROMPT = (
            "Rewrite this memory entry to be more concise, precise, and useful as a persistent fact.\n"
            "Keep all key information. Remove filler words. Target: under 120 characters if possible.\n"
            "Return ONLY the rewritten memory text, nothing else.\n\nMemory: {text}"
        )

        try:
            model_spec = get_setting("research_model", "").strip() or get_setting("default_model", "").strip()
            _url, _model, _headers = _resolve_model(model_spec, owner=owner)
        except Exception as e:
            return {"error": f"No model available to evolve memories: {e}", "exit_code": 1}

        _rewrite_sem = _asyncio.Semaphore(_BULK_EVOLVE_CONCURRENCY)

        async def _rewrite_one(m: dict):
            old_text = m.get("text", "").strip()
            if not old_text:
                return m["id"], None, None
            try:
                async with _rewrite_sem:
                    response = await _retry_on_rate_limit(lambda: llm_call_async(
                        _url, _model, [{"role": "user", "content": _EVOLVE_PROMPT.format(text=old_text)}],
                        headers=_headers, max_tokens=150, timeout=45, max_retries=1,
                    ))
                return m["id"], old_text, response.strip().strip('"').strip("'")
            except Exception as e:
                return m["id"], old_text, None

        rewrite_results = await _asyncio.gather(*[_rewrite_one(m) for m in targets if m.get("text", "").strip()])

        by_id = {m["id"]: m for m in targets}
        results = []
        for mid, old_text, new_text in rewrite_results:
            if old_text is None:
                continue
            short = mid[:8]
            if new_text and new_text != old_text:
                by_id[mid]["text"] = new_text
                results.append(f"- `{short}` {old_text[:60]}… → {new_text[:80]}")
            elif new_text is None:
                results.append(f"- `{short}` error")
            else:
                results.append(f"- `{short}` unchanged")

        if _memory_manager:
            _memory_manager.save(memories)

        return {
            "output": f"memory_evolve: {len(results)} memories processed\n" + "\n".join(results),
            "exit_code": 0,
        }

    return {"error": f"Unknown action '{action}'. Use: audit, evolve, evolve_all, merge, delete, absorb, memory_audit, memory_list, memory_pin, memory_delete, memory_add, memory_evolve, memory_evolve_all.", "exit_code": 1}


# ---------------------------------------------------------------------------
# List models tool
# ---------------------------------------------------------------------------

async def do_list_models(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List all available models across configured endpoints.

    Content = optional filter keyword.
    """
    import httpx
    from src.database import SessionLocal, ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS
    from src.auth_helpers import owner_filter

    keyword = content.strip().lower() if content.strip() else None

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        endpoints = query.all()
        if not endpoints:
            return {"results": "No enabled model endpoints configured."}

        result_lines = []
        total_models = 0

        for ep in endpoints:
            try:
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            provider = _detect_provider(base)
            headers = build_headers(api_key, base)

            model_ids = []
            if provider == "anthropic":
                model_ids = list(ANTHROPIC_MODELS)
            else:
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
                        data = r.json()
                        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                        if not model_ids:
                            model_ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                    else:
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    model_ids = ["(endpoint offline)"]

            if keyword:
                model_ids = [m for m in model_ids if keyword in m.lower() or keyword in (ep.name or "").lower()]

            if model_ids:
                result_lines.append(f"\n**{ep.name or base}** ({provider}):")
                for mid in model_ids:
                    result_lines.append(f"  - `{mid}`")
                    total_models += 1

        if not result_lines:
            return {"results": "No models found" + (f" matching '{keyword}'" if keyword else "") + "."}

        header = f"Available models ({total_models} total):"
        return {"results": header + "\n".join(result_lines)}
    except Exception as e:
        logger.error(f"list_models failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# RAG management tool
# ---------------------------------------------------------------------------

async def do_manage_rag(content: str, session_id: Optional[str] = None) -> Dict:
    """Manage RAG indexed documents: list, add_directory, remove_directory.

    Content format:
      Line 1: action (list|add_directory|remove_directory)
      Line 2: directory path (for add/remove)
    """
    lines = content.strip().split("\n")
    if not lines:
        return {"error": "No action specified"}
    action = lines[0].strip().lower()

    if action == "list":
        if not _personal_docs_manager:
            return {"results": "Personal docs manager not available. RAG may not be configured."}
        try:
            files = []
            if hasattr(_personal_docs_manager, 'index'):
                files = _personal_docs_manager.index or []
            dirs = []
            if hasattr(_personal_docs_manager, 'get_indexed_directories'):
                dirs = _personal_docs_manager.get_indexed_directories()

            result_lines = []
            if dirs:
                result_lines.append(f"**Indexed directories ({len(dirs)}):**")
                for d in dirs:
                    result_lines.append(f"  - `{d}`")
            if files:
                result_lines.append(f"\n**Indexed files ({len(files)}):**")
                for f in files[:50]:
                    name = f.get("name", str(f)) if isinstance(f, dict) else str(f)
                    result_lines.append(f"  - {name}")
                if len(files) > 50:
                    result_lines.append(f"  ... and {len(files) - 50} more")

            if not result_lines:
                return {"results": "No files or directories indexed in RAG."}
            return {"results": "\n".join(result_lines)}
        except Exception as e:
            return {"error": str(e)}

    elif action == "add_directory":
        if len(lines) < 2:
            return {"error": "add_directory needs line 2: directory path"}
        directory = lines[1].strip()

        import os
        directory = os.path.expanduser(directory)
        if not os.path.isdir(directory):
            return {"error": f"Directory not found: {directory}"}

        if not _rag_manager:
            return {"error": "RAG manager not available"}

        try:
            result = _rag_manager.index_personal_documents(directory)
            indexed = result.get("indexed", 0) if isinstance(result, dict) else 0
            return {"action": "add_directory", "directory": directory,
                    "results": f"Directory '{directory}' added to RAG index ({indexed} files indexed)"}
        except Exception as e:
            return {"error": f"Failed to index directory: {e}"}

    elif action == "remove_directory":
        if len(lines) < 2:
            return {"error": "remove_directory needs line 2: directory path"}
        directory = lines[1].strip()

        if not _personal_docs_manager:
            return {"error": "Personal docs manager not available"}

        try:
            if hasattr(_personal_docs_manager, 'remove_directory'):
                # Performs a targeted per-directory delete (#1660). The previous
                # unconditional _rag_manager.rebuild_index() here wiped the whole
                # collection on every remove (even for untracked dirs) and has
                # been removed.
                _personal_docs_manager.remove_directory(directory)
            return {"action": "remove_directory", "directory": directory,
                    "results": f"Directory '{directory}' removed from RAG index"}
        except Exception as e:
            return {"error": f"Failed to remove directory: {e}"}

    else:
        return {"error": f"Unknown action '{action}'. Use: list, add_directory, remove_directory"}


# ---------------------------------------------------------------------------
# UI control tool (returns events for frontend to apply)
# ---------------------------------------------------------------------------

async def do_ui_control(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Control frontend UI: toggle settings, switch model, change theme.

    Content format:
      Line 1: action
      Line 2+: action-specific params

    Actions:
      toggle <name> <on|off>  — Toggle a setting (web, bash, rag, research, incognito, document_editor)
      set_mode <agent|chat>   — Switch between agent and chat mode
      switch_model <model>    — Change the model for the current session
      set_theme <preset>      — Apply a built-in theme preset (dark, light, midnight, paper, cyberpunk, retrowave, forest, ocean, ume, copper, terminal, organs, lavender, gpt, claude, cute)
      create_theme <name> <bg> <fg> <panel> <border> <accent> [key=val ...] — Create custom theme. Optional key=val: advanced color overrides AND background effects: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num>, bgEffectSize=<num>, frosted=true|false
      open_panel <name>       — Open a panel (documents, gallery, email, sessions, notes, memories, skills, settings, cookbook)
      open_email_reply <uid> [folder] [reply|reply-all|ai-reply] [body text] — Open a reply draft document for an email; does not send. ALWAYS append the body text when the user told you what to say (one-shot draft); only omit body when the user just asked to "open a reply" without content.
      get_toggles             — Return current toggle states (server-side knowledge)
    """
    lines = content.strip().split("\n")
    if not lines:
        return {"error": "No action specified"}

    parts = lines[0].strip().split(None, 2)
    action = parts[0].lower()

    if action == "toggle":
        if len(parts) < 3:
            return {"error": "toggle needs: toggle <name> <on|off>"}
        toggle_name = parts[1].lower()
        state = parts[2].lower() in ("on", "true", "1", "yes", "enable", "enabled")
        # Friendly aliases — users say "shell" / "search" naturally.
        _toggle_aliases = {
            "shell": "bash",
            "terminal": "bash",
            "search": "web",
            "websearch": "web",
            "web_search": "web",
            "deepresearch": "research",
            "deep_research": "research",
            "documents": "document_editor",
            "doc": "document_editor",
            "docs": "document_editor",
            "private": "incognito",
        }
        toggle_name = _toggle_aliases.get(toggle_name, toggle_name)
        valid_toggles = {"web", "bash", "rag", "research", "incognito", "document_editor"}
        if toggle_name not in valid_toggles:
            return {"error": f"Unknown toggle '{toggle_name}'. Valid: {', '.join(sorted(valid_toggles))}"}
        return {
            "ui_event": "toggle",
            "toggle_name": toggle_name,
            "state": state,
            "results": f"Toggle '{toggle_name}' set to {'on' if state else 'off'}",
        }

    elif action == "set_mode":
        if len(parts) < 2:
            return {"error": "set_mode needs: set_mode <agent|chat>"}
        mode = parts[1].lower()
        if mode not in ("agent", "chat"):
            return {"error": f"Invalid mode '{mode}'. Use: agent, chat"}
        return {
            "ui_event": "set_mode",
            "mode": mode,
            "results": f"Mode changed to '{mode}'",
        }

    elif action == "switch_model":
        model_spec = " ".join(parts[1:]) if len(parts) > 1 else ""
        if not model_spec:
            model_spec = lines[1].strip() if len(lines) > 1 else ""
        if not model_spec:
            return {"error": "switch_model needs a model name"}

        # Resolve the model to validate it exists
        try:
            url, model_id, headers = _resolve_model(model_spec, owner=owner)
        except ValueError as e:
            return {"error": str(e)}

        # Update current session's model if we have a session
        if session_id and _session_manager:
            from src.database import SessionLocal as SL2, Session as DbSess2
            db2 = SL2()
            try:
                db_s = db2.query(DbSess2).filter(DbSess2.id == session_id).first()
                if db_s:
                    db_s.endpoint_url = url
                    db_s.model = model_id
                    db2.commit()
            finally:
                db2.close()

            sess = _session_manager.get_session(session_id)
            if sess:
                sess.endpoint_url = url
                sess.model = model_id
                if headers:
                    sess.headers = headers

        return {
            "ui_event": "switch_model",
            "model": model_id,
            "endpoint_url": url,
            "results": f"Model switched to '{model_id}'",
        }

    elif action == "set_theme":
        theme_name = parts[1].lower() if len(parts) > 1 else ""
        # Theme colors are defined in static/js/theme.js on the frontend.
        # We pass the name; the frontend looks it up from presets + custom themes.
        # Also check user's custom themes stored in prefs.
        # Must match the THEMES keys in static/js/theme.js.
        known_presets = [
            "dark", "light", "midnight", "paper", "cyberpunk", "retrowave",
            "forest", "ocean", "ume", "copper", "terminal", "organs",
            "lavender", "gpt", "claude", "cute",
        ]
        custom_themes = {}
        try:
            from routes.prefs_routes import _load as _load_prefs
            custom_themes = _load_prefs().get("custom-themes", {}) or {}
        except Exception:
            pass
        all_known = set(known_presets) | set(custom_themes.keys())
        if theme_name not in all_known:
            custom_label = f" | Custom: {', '.join(sorted(custom_themes.keys()))}" if custom_themes else ""
            return {"error": f"Unknown theme '{theme_name}'. Available: {', '.join(sorted(known_presets))}{custom_label}"}
        return {
            "ui_event": "set_theme",
            "theme_name": theme_name,
            "results": f"Theme changed to '{theme_name}'",
        }

    elif action == "create_theme":
        # Re-split without limit to get all parts
        parts = lines[0].strip().split()
        # create_theme <name> <bg> <fg> <panel> <border> <accent> [key=value ...]
        if len(parts) < 7:
            return {"error": "create_theme needs: create_theme <name> <bg> <fg> <panel> <border> <accent> (all hex colors). Optional advanced color key=value pairs (userBubbleBg, aiBubbleBg, bubbleBorder, sidebarBg, sectionAccent, brandColor, inputBg, inputBorder, sendBtnBg, sendBtnHover, codeBg, codeFg, toggleBg, toggleActive, accentPrimary, accentError). Optional background EFFECTS: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num e.g. 1>, bgEffectSize=<num e.g. 1>, frosted=true|false"}
        name = parts[1].lower().replace(" ", "-")
        colors = {"bg": parts[2], "fg": parts[3], "panel": parts[4], "border": parts[5], "red": parts[6]}
        # Validate base hex colors
        import re as _re
        for k, v in colors.items():
            if not _re.match(r'^#[0-9a-fA-F]{6}$', v):
                return {"error": f"Invalid hex color for {k}: '{v}'. Use format #RRGGBB"}
        # Parse optional advanced key=value pairs
        adv_keys = {
            "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
            "sectionAccent", "brandColor", "inputBg", "inputBorder",
            "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
            "toggleBg", "toggleActive", "accentPrimary", "accentError",
        }
        advanced = {}
        # Background-effect fields (animated pattern + frosted glass). Different
        # value types than the hex-only advanced keys, so parse separately.
        _BG_PATTERNS = {"none", "dots", "synapse", "rain", "constellations",
                        "perlin-flow", "petals", "sparkles", "embers"}
        bg = {}
        for part in parts[7:]:
            if "=" not in part:
                continue
            ak, av = part.split("=", 1)
            if ak in adv_keys:
                if not _re.match(r'^#[0-9a-fA-F]{6}$', av):
                    return {"error": f"Invalid hex color for advanced key {ak}: '{av}'. Use format #RRGGBB"}
                advanced[ak] = av
            elif ak == "bgPattern":
                if av not in _BG_PATTERNS:
                    return {"error": f"Invalid bgPattern '{av}'. Use one of: {', '.join(sorted(_BG_PATTERNS))}"}
                bg["pattern"] = av
            elif ak == "bgEffectColor":
                if not _re.match(r'^#[0-9a-fA-F]{6}$', av):
                    return {"error": f"Invalid hex color for bgEffectColor: '{av}'. Use format #RRGGBB"}
                bg["effectColor"] = av
            elif ak in ("bgEffectIntensity", "bgEffectSize"):
                try:
                    bg["effectIntensity" if ak == "bgEffectIntensity" else "effectSize"] = float(av)
                except ValueError:
                    return {"error": f"Invalid number for {ak}: '{av}'"}
            elif ak == "frosted":
                bg["frosted"] = av.lower() in ("true", "1", "yes", "on")
        if advanced:
            colors["advanced"] = advanced
        return {
            "ui_event": "create_theme",
            "theme_name": name,
            "colors": colors,
            "bg": bg or None,
            "results": f"Custom theme '{name}' created and applied"
                       + (f" with {len(advanced)} advanced overrides" if advanced else "")
                       + (f" + background effect ({bg.get('pattern', 'frosted' if bg.get('frosted') else 'custom')})" if bg else ""),
        }

    elif action == "highlight":
        selector = parts[1] if len(parts) > 1 else ""
        label = " ".join(parts[2:]) if len(parts) > 2 else ""
        if not selector:
            return {"error": "highlight needs: highlight <css-selector> [label]"}
        return {
            "ui_event": "highlight",
            "selector": selector,
            "label": label,
            "results": f"Highlighting '{selector}'",
        }

    elif action == "clear_highlight":
        return {
            "ui_event": "clear_highlight",
            "results": "Highlights cleared",
        }

    elif action == "open_panel":
        # Open a top-level panel/modal: documents/library, gallery,
        # email, sessions, notes, memories, skills, settings, cookbook.
        panel = parts[1].lower() if len(parts) > 1 else ""
        _panel_aliases = {
            "documents": "documents",
            "document": "documents",
            "doc": "documents",
            "docs": "documents",
            "library": "documents",
            "doclib": "documents",
            "gallery": "gallery",
            "images": "gallery",
            "email": "email",
            "emails": "email",
            "inbox": "email",
            "mail": "email",
            "sessions": "sessions",
            "chats": "sessions",
            "history": "sessions",
            "notes": "notes",
            "note": "notes",
            "todo": "notes",
            "todos": "notes",
            "memories": "memories",
            "memory": "memories",
            "brain": "memories",
            "skills": "skills",
            "settings": "settings",
            "preferences": "settings",
            "cookbook": "cookbook",
            "models": "cookbook",
            "llm": "cookbook",
            "serve": "cookbook",
            "serving": "cookbook",
        }
        target = _panel_aliases.get(panel)
        if not target:
            return {"error": f"Unknown panel '{panel}'. Valid: documents, gallery, email, sessions, notes, memories, skills, settings, cookbook."}
        return {
            "ui_event": "open_panel",
            "panel": target,
            "results": f"Opening {target} panel",
        }

    elif action == "open_email_reply":
        # Two forms supported:
        #   open_email_reply <uid> [folder] [reply|reply-all|ai-reply]
        #   open_email_reply <uid> [folder] [reply|reply-all|ai-reply]
        #     <body text on subsequent lines or after the mode token>
        # The body text (if any) gets pre-filled into the reply draft so the
        # agent can compose-and-open in one tool call instead of opening an
        # empty draft and leaving the user to wonder what happened.
        first_line = lines[0].strip()
        parts = first_line.split(maxsplit=4)
        uid = parts[1].strip() if len(parts) > 1 else ""
        folder = parts[2].strip() if len(parts) > 2 else "INBOX"
        mode = parts[3].strip().lower() if len(parts) > 3 else "reply"
        # Body: everything on the first line after the mode token, plus any
        # subsequent lines. Allows multi-line bodies.
        inline_body = parts[4] if len(parts) > 4 else ""
        rest_lines = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        body = (inline_body + ("\n" + rest_lines if rest_lines else "")).strip()
        if not uid:
            return {"error": "open_email_reply needs: open_email_reply <uid> [folder] [reply|reply-all|ai-reply] [body text]"}
        if mode not in ("reply", "reply-all", "ai-reply"):
            mode = "reply"
        # Body is REQUIRED for the agent path. Opening an empty draft is what
        # users do by clicking the Reply button — they don't ask the agent
        # for that. Every agent invocation of open_email_reply MUST include
        # the body. Reject empty so the agent retries with the content the
        # user asked for. Exception: ai-reply mode triggers the existing
        # AI-Reply path on the frontend which generates its own body.
        if not body and mode != "ai-reply":
            return {
                "error": (
                    "open_email_reply called without body. The agent path REQUIRES a body — "
                    "opening an empty draft is the wrong response when the user asked you to write. "
                    "Re-call with the reply text included: "
                    f"`open_email_reply {uid} {folder or 'INBOX'} {mode} <your reply text here>`. "
                    "Compose the reply now based on the open email's content and the user's request, "
                    "then call this tool again with the body. Do NOT call create_document instead."
                ),
            }
        result = {
            "ui_event": "open_email_reply",
            "uid": uid,
            "folder": folder or "INBOX",
            "mode": mode,
            "results": f"Opening reply draft for email UID {uid}" + (" with pre-filled body" if body else ""),
        }
        if body:
            result["body"] = body
        return result

    elif action == "get_toggles":
        return {
            "results": (
                "Toggle states are managed client-side in localStorage. "
                "Available toggles: web, bash, rag, research, incognito, document_editor. "
                "Use 'toggle <name> <on|off>' to change them."
            )
        }

    else:
        return {"error": f"Unknown action '{action}'. Use: toggle, set_mode, switch_model, set_theme, highlight, clear_highlight, get_toggles"}


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

async def do_generate_image(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Generate an image using an image-capable model (e.g. gpt-image-1).

    Content format:
      Line 1: prompt describing the image
      Line 2: model name (optional, default auto-detects: prefers gpt-image-1.5 > gpt-image-1)
      Line 3: size (optional, defaults to 1024x1024)
      Line 4: quality (optional, defaults to medium — options: low, medium, high, auto)
    """
    import base64
    import httpx
    import os
    from pathlib import Path
    from src.url_safety import check_outbound_url

    lines = content.strip().split("\n")
    prompt = lines[0].strip() if lines else ""
    model_spec = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
    size = lines[2].strip() if len(lines) > 2 and lines[2].strip() else "1024x1024"
    quality = lines[3].strip() if len(lines) > 3 and lines[3].strip() else "medium"

    if not prompt:
        return {"error": "Image prompt is required (line 1)"}

    # Load admin settings for defaults
    try:
        from src.settings import load_settings
        _settings = load_settings()
    except Exception:
        _settings = {}

    # Use admin-configured model/quality if not specified by the tool call
    if not model_spec:
        model_spec = _settings.get("image_model", "")
    if quality == "medium" and _settings.get("image_quality"):
        quality = _settings["image_quality"]

    # Auto-detect best available image model if still not set
    if not model_spec:
        for candidate in ("gpt-image-1.5", "gpt-image-1", "dall-e-3"):
            try:
                _resolve_model(candidate, owner=owner)
                model_spec = candidate
                break
            except ValueError:
                continue
        # Fallback: find any locally registered image-type endpoint
        if not model_spec:
            try:
                from src.database import SessionLocal, ModelEndpoint
                from src.auth_helpers import owner_filter
                import httpx as _req
                _idb = SessionLocal()
                try:
                    _img_q = _idb.query(ModelEndpoint).filter(
                        ModelEndpoint.is_enabled == True,
                        ModelEndpoint.model_type == "image",
                    )
                    if owner:
                        _img_q = owner_filter(_img_q, ModelEndpoint, owner)
                    _img_eps = _img_q.all()
                    for _iep in _img_eps:
                        _ibase = _iep.base_url.rstrip("/")
                        if not _ibase.endswith("/v1"):
                            _ibase += "/v1"
                        try:
                            _r = _req.get(_ibase + "/models", timeout=3)
                            _r.raise_for_status()
                            _mids = [m.get("id") for m in (_r.json().get("data") or []) if m.get("id")]
                            if _mids:
                                model_spec = _mids[0]
                                break
                        except Exception:
                            continue
                finally:
                    _idb.close()
            except Exception:
                pass
        if not model_spec:
            return {"error": "No image model found. Configure one in Admin → Image Generation."}

    # Resolve the model to find the right endpoint
    try:
        url, model_id, headers = _resolve_model(model_spec, owner=owner)
    except ValueError:
        return {"error": f"No endpoint found with image model '{model_spec}'. "
                "Configure an OpenAI-compatible endpoint with image generation support."}

    # Detect if this is a GPT image model vs DALL-E vs local diffusion
    is_gpt_image = "gpt-image" in model_id.lower()
    is_dalle = "dall-e" in model_id.lower()
    is_local_diffusion = not is_gpt_image and not is_dalle

    # Build the images endpoint URL from the chat completions URL
    base_url = url.replace("/chat/completions", "").replace("/v1/messages", "").rstrip("/")
    images_url = base_url + "/images/generations"

    # Validate size for cloud image models (local diffusion accepts any WxH)
    valid_gpt_sizes = {"1024x1024", "1024x1536", "1536x1024", "auto"}
    valid_dalle3_sizes = {"1024x1024", "1024x1792", "1792x1024"}
    if is_gpt_image and size not in valid_gpt_sizes:
        size = "1024x1024"
    elif is_dalle and size not in valid_dalle3_sizes:
        size = "1024x1024"

    payload = {
        "model": model_id,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }

    # GPT image models and local diffusion support quality; DALL-E does not
    if is_gpt_image or is_local_diffusion:
        if quality in ("low", "medium", "high", "auto"):
            payload["quality"] = quality
        else:
            payload["quality"] = "medium"

    logger.info(f"Image generation: model={model_id}, size={size}, quality={quality}, prompt={prompt[:80]}")

    try:
        # GPT image models can take 30-120s+ depending on quality
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)) as client:
            resp = await client.post(images_url, json=payload, headers=headers)

            if resp.status_code != 200:
                error_text = resp.text[:500]
                try:
                    err_json = resp.json()
                    error_text = err_json.get("error", {}).get("message", error_text) if isinstance(err_json.get("error"), dict) else str(err_json.get("error", error_text))
                except Exception:
                    pass
                return {"error": f"Image generation failed ({resp.status_code}): {error_text}"}

            data = resp.json()
            images = data.get("data", [])
            if not images:
                return {"error": "No images returned from API"}

            img = images[0]
            image_url = None
            image_id = None

            def _save_to_gallery(filename: str) -> str:
                """Insert a GalleryImage row and return the new id (or '')."""
                try:
                    from src.database import SessionLocal as _GallerySL, GalleryImage
                    new_id = str(uuid.uuid4())
                    _gdb = _GallerySL()
                    _gdb.add(GalleryImage(
                        id=new_id,
                        filename=filename,
                        prompt=prompt,
                        model=model_id,
                        size=size,
                        quality=payload.get("quality", "medium"),
                        session_id=session_id,
                        owner=owner,
                    ))
                    _gdb.commit()
                    _gdb.close()
                    return new_id
                except Exception as _ge:
                    logger.warning(f"Failed to save gallery record: {_ge}")
                    return ""

            # GPT image models always return b64_json; DALL-E may return url
            if img.get("b64_json"):
                img_dir = Path(GENERATED_IMAGES_DIR)
                img_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{uuid.uuid4().hex[:12]}.png"
                img_path = img_dir / filename
                img_path.write_bytes(base64.b64decode(img.get("b64_json")))
                image_url = f"/api/generated-image/{filename}"
                image_id = _save_to_gallery(filename)

            elif img.get("url"):
                # Download external URL and save locally (DALL-E returns temp URLs)
                result_url = img["url"]
                ok, reason = check_outbound_url(
                    result_url,
                    block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
                )
                if not ok:
                    return {"error": f"Image API returned unsafe image URL: {reason}"}
                try:
                    dl_resp = httpx.get(result_url, timeout=60)
                    if dl_resp.status_code == 200:
                        img_dir = Path(GENERATED_IMAGES_DIR)
                        img_dir.mkdir(parents=True, exist_ok=True)
                        filename = f"{uuid.uuid4().hex[:12]}.png"
                        img_path = img_dir / filename
                        img_path.write_bytes(dl_resp.content)
                        image_url = f"/api/generated-image/{filename}"
                        image_id = _save_to_gallery(filename)
                    else:
                        image_url = result_url  # fallback to external URL
                except Exception as _dl_e:
                    logger.warning(f"Failed to download DALL-E image: {_dl_e}")
                    image_url = result_url  # fallback to external URL
            else:
                return {"error": "Image API returned unexpected format (no b64_json or url)"}

            return {
                "results": f"Generated image for: {prompt[:100]}",
                "image_url": image_url,
                "image_id": image_id,
                "image_prompt": prompt,
                "image_model": model_id,
                "image_size": size,
                "image_quality": payload.get("quality", "medium"),
            }

    except httpx.TimeoutException:
        return {"error": "Image generation timed out (300s). The model may be overloaded — try again or use quality=low."}
    except Exception as e:
        return {"error": f"Image generation error: {str(e)}"}


# ---------------------------------------------------------------------------
# Dispatcher (called from agent_tools.execute_tool_block)
# ---------------------------------------------------------------------------

async def dispatch_ai_tool(
    tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None
) -> Tuple[str, Dict]:
    """Dispatch an AI interaction tool. Returns (description, result_dict)."""

    if tool == "chat_with_model":
        model_spec = content.split("\n")[0].strip()[:60]
        desc = f"chat_with_model: {model_spec}"
        result = await do_chat_with_model(content, session_id, owner=owner)

    elif tool == "create_session":
        name = content.split("\n")[0].strip()[:60]
        desc = f"create_session: {name}"
        result = await do_create_session(content, session_id, owner=owner)

    elif tool == "list_sessions":
        keyword = content.strip()[:40]
        desc = f"list_sessions{': ' + keyword if keyword else ''}"
        result = await do_list_sessions(content, session_id, owner=owner)

    elif tool == "send_to_session":
        sid = content.split("\n")[0].strip()[:20]
        desc = f"send_to_session: {sid}"
        result = await do_send_to_session(content, session_id, owner=owner)

    elif tool == "pipeline":
        desc = "pipeline: running steps"
        result = await do_pipeline(content, session_id, owner=owner)

    elif tool == "manage_session":
        action = content.split("\n")[0].strip()[:40]
        desc = f"manage_session: {action}"
        result = await do_manage_session(content, session_id, owner=owner)

    elif tool == "manage_memory":
        action = content.split("\n")[0].strip()[:40]
        desc = f"manage_memory: {action}"
        result = await do_manage_memory(content, session_id, owner=owner)

    elif tool == "list_models":
        keyword = content.strip()[:40]
        desc = f"list_models{': ' + keyword if keyword else ''}"
        result = await do_list_models(content, session_id, owner=owner)

    elif tool == "ui_control":
        action = content.split("\n")[0].strip()[:60]
        desc = f"ui_control: {action}"
        result = await do_ui_control(content, session_id, owner=owner)

    elif tool == "ask_teacher":
        problem = content.split("\n", 1)[-1].strip()[:60]
        desc = f"ask_teacher: {problem}"
        result = await do_ask_teacher(content, session_id, owner=owner)

    else:
        desc = f"unknown ai tool: {tool}"
        result = {"error": f"Unknown AI interaction tool: {tool}"}

    return desc, result
