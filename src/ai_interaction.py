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

# ---------------------------------------------------------------------------
# Global managers (set from app.py, same pattern as _mcp_manager)
# ---------------------------------------------------------------------------
_session_manager = None
_memory_manager = None
_memory_vector = None
_rag_manager = None
_personal_docs_manager = None


def set_session_manager(mgr):
    global _session_manager
    _session_manager = mgr


def get_session_manager():
    return _session_manager


def set_memory_manager(mgr, vector=None):
    global _memory_manager, _memory_vector
    _memory_manager = mgr
    _memory_vector = vector


def set_rag_manager(rag_mgr, personal_docs_mgr=None):
    global _rag_manager, _personal_docs_manager
    _rag_manager = rag_mgr
    _personal_docs_manager = personal_docs_mgr


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url, build_headers, build_models_url


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
            base = _normalize_base(ep.base_url)
            provider = _detect_provider(base)
            headers = build_headers(ep.api_key, base)

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
                    r = httpx.get(build_models_url(base), headers=headers, timeout=5)
                    r.raise_for_status()
                    data = r.json()
                    model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                    if not model_ids:
                        model_ids = [
                            m.get("name") or m.get("model")
                            for m in (data.get("models") or [])
                            if m.get("name") or m.get("model")
                        ]
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
    """Manage memories: list, add, edit, delete, search.

    Content format:
      Line 1: action (list|add|edit|delete|search)
      Line 2+: action-specific params

    Actions:
      list                    — list all memories (optional line 2: category filter)
      add                     — line 2: text, optional line 3: category (fact|event|contact|preference)
      edit                    — line 2: memory_id, line 3: new text
      delete                  — line 2: memory_id
      search                  — line 2: query
    """
    if not _memory_manager:
        return {"error": "Memory manager not available"}

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
        for m in memories[:100]:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            if len(text) > 150:
                text = text[:150] + "..."
            result_lines.append(f"- [{cat}] `{mid}` — {text}")
        if len(memories) > 100:
            result_lines.append(f"... and {len(memories) - 100} more")
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

    else:
        return {"error": f"Unknown action '{action}'. Use: list, add, edit, delete, search"}


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
            base = _normalize_base(ep.base_url)
            provider = _detect_provider(base)
            headers = build_headers(ep.api_key, base)

            model_ids = []
            if provider == "anthropic":
                model_ids = list(ANTHROPIC_MODELS)
            else:
                try:
                    r = httpx.get(build_models_url(base), headers=headers, timeout=5)
                    r.raise_for_status()
                    data = r.json()
                    model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                    if not model_ids:
                        model_ids = [
                            m.get("name") or m.get("model")
                            for m in (data.get("models") or [])
                            if m.get("name") or m.get("model")
                        ]
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
      set_theme <preset>      — Apply a theme preset (dark, light, paper, nord, dracula, gruvbox, gpt, claude, lavender, etc.)
      create_theme <name> <bg> <fg> <panel> <border> <accent> [key=val ...] — Create custom theme. Optional key=val: advanced color overrides AND background effects: bgPattern=<none|dots|synapse|rain|constellations|perlin-flow|petals|sparkles|embers>, bgEffectColor=#RRGGBB, bgEffectIntensity=<num>, bgEffectSize=<num>, frosted=true|false
      open_panel <name>       — Open a panel (documents, gallery, email, sessions, notes, memories, skills, settings, cookbook)
      open_email_reply <uid> [folder] [reply|reply-all|ai-reply] — Open a reply draft document for an email; does not send
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
        reply_parts = lines[0].strip().split()
        uid = reply_parts[1].strip() if len(reply_parts) > 1 else ""
        folder = reply_parts[2].strip() if len(reply_parts) > 2 else "INBOX"
        mode = reply_parts[3].strip().lower() if len(reply_parts) > 3 else "reply"
        if not uid:
            return {"error": "open_email_reply needs: open_email_reply <uid> [folder] [reply|reply-all|ai-reply]"}
        if mode not in ("reply", "reply-all", "ai-reply"):
            mode = "reply"
        return {
            "ui_event": "open_email_reply",
            "uid": uid,
            "folder": folder or "INBOX",
            "mode": mode,
            "results": f"Opening reply draft for email UID {uid}",
        }

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
    from pathlib import Path

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
                try:
                    dl_resp = httpx.get(img["url"], timeout=60)
                    if dl_resp.status_code == 200:
                        img_dir = Path(GENERATED_IMAGES_DIR)
                        img_dir.mkdir(parents=True, exist_ok=True)
                        filename = f"{uuid.uuid4().hex[:12]}.png"
                        img_path = img_dir / filename
                        img_path.write_bytes(dl_resp.content)
                        image_url = f"/api/generated-image/{filename}"
                        image_id = _save_to_gallery(filename)
                    else:
                        image_url = img["url"]  # fallback to external URL
                except Exception as _dl_e:
                    logger.warning(f"Failed to download DALL-E image: {_dl_e}")
                    image_url = img["url"]  # fallback to external URL
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
