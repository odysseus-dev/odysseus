"""Shared helpers for chat routes — context building, post-response tasks, auth resolution."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core.models import ChatMessage
from core.database import SessionLocal
from core.database import Session as DBSession, ModelEndpoint
from src.llm_core import normalize_model_id
from src.context_compactor import maybe_compact, trim_for_context
from src.auth_helpers import get_current_user
from src.prompt_security import untrusted_context_message
from routes.prefs_routes import _load_for_user as load_prefs_for_user

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ── Data containers ────────────────────────────────────────────────────── #

@dataclass
class PresetInfo:
    """Extracted preset parameters."""
    temperature: Optional[float]
    max_tokens: Optional[int]
    system_prompt: Optional[str]
    character_name: Optional[str]


@dataclass
class PreprocessedMessage:
    """Result of chat_handler.preprocess_message."""
    enhanced_message: str
    user_content: Any  # str or list (multimodal)
    text_for_context: str
    youtube_transcripts: list
    attachment_meta: list


@dataclass
class ChatContext:
    """Everything needed to call the LLM after context-building."""
    preface: list
    rag_sources: list
    web_sources: list
    used_memories: list
    messages: list
    context_length: int
    was_compacted: bool
    user: Optional[str]
    uprefs: dict
    preset: PresetInfo
    preprocessed: PreprocessedMessage
    # Documents auto-created server-side during preprocess (e.g. when an
    # attached fillable PDF gets rendered into a markdown editor doc).
    # The chat route emits a doc_update SSE event for each before streaming
    # begins, so the editor pane switches to the new doc immediately.
    auto_opened_docs: list = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────────── #

def _enforce_chat_privileges(request, sess) -> None:
    """Apply the per-user privilege gates (allowed_models + max_messages_per_day)
    that both /api/chat and /api/chat_stream must enforce BEFORE any LLM work.

    Raises HTTPException(403) if the session's model is not in the user's
    allowlist, or HTTPException(429) if the user has hit their daily message
    cap. No-op for unauthenticated callers or when auth_manager is absent
    (single-user mode). Admins receive ADMIN_PRIVILEGES from get_privileges,
    which means empty allowed_models / zero cap → no-op for them.
    """
    try:
        user = get_current_user(request)
    except Exception:
        user = None
    if not user:
        return
    auth_manager = getattr(getattr(request.app, "state", None), "auth_manager", None)
    if not auth_manager:
        return

    privs = auth_manager.get_privileges(user) or {}
    allowed = privs.get("allowed_models") or []
    if allowed and sess.model and sess.model not in allowed:
        raise HTTPException(403, f"Your account is not allowed to use model '{sess.model}'.")

    cap = int(privs.get("max_messages_per_day") or 0)
    if cap <= 0:
        return

    from datetime import datetime as _dt, timedelta as _td
    from core.database import Session as _DbSess, ChatMessage as _Cm
    db = SessionLocal()
    try:
        count = (
            db.query(_Cm)
            .join(_DbSess, _Cm.session_id == _DbSess.id)
            .filter(_DbSess.owner == user,
                    _Cm.role == "user",
                    _Cm.timestamp >= _dt.utcnow() - _td(days=1))
            .count()
        )
    finally:
        db.close()
    if count >= cap:
        raise HTTPException(429, f"Daily message limit reached ({cap}). Try again in 24 hours.")


def needs_auto_name(name: str) -> bool:
    """Check if a session still has its default/placeholder name."""
    if not name:
        return True
    if name.startswith("Chat:") or name == "Chat":
        return True
    # Default frontend name: "modelname HH:MM:SS AM/PM"
    if re.match(r'^.+ \d{1,2}:\d{2}:\d{2}\s*(AM|PM)$', name):
        return True
    return False


async def auto_name_session(session_manager, sess):
    """Generate a short title for a session from its first user message."""
    try:
        from src.llm_core import llm_call_async
        from src.task_endpoint import resolve_task_endpoint

        # Find first user message
        first_msg = ""
        for msg in sess.history:
            if msg.role == "user":
                content = msg.content
                if isinstance(content, list):
                    content = next(
                        (i.get("text", "") for i in content if isinstance(i, dict) and i.get("type") == "text"),
                        "",
                    )
                first_msg = str(content)[:500]
                break

        if not first_msg:
            return

        t_url, t_model, t_headers = resolve_task_endpoint(
            sess.endpoint_url, sess.model, sess.headers,
        )
        if not t_model:
            logger.debug("[auto-name] No model provided, skipping")
            return

        # max_tokens big enough that reasoning models (Minimax M2,
        # DeepSeek R1, QwQ, etc.) have headroom for <think>…</think>
        # plus the actual title — 200 used to clip them mid-reasoning
        # so strip_think left an empty string and no rename happened.
        # Timeout matches: 60s gives slow local reasoners room to finish.
        title = await llm_call_async(
            t_url,
            t_model,
            [
                {"role": "system", "content": "Generate a short title (3-6 words, no quotes) for a conversation that starts with this message. Reply with ONLY the title, nothing else. Do NOT include any thinking, reasoning, or explanation — just the title."},
                {"role": "user", "content": first_msg},
            ],
            temperature=0.3,
            max_tokens=4096,
            headers=t_headers,
            timeout=60,
        )

        title = title.strip().strip('"\'').strip()
        # Strip <think>/<thinking> blocks (closed, dangling, or stray tags)
        # via the central helper.
        from src.text_helpers import strip_think
        title = strip_think(title, prose=False, prompt_echo=False)
        if title and len(title) < 80:
            session_manager.update_session_name(sess.id, title)
            logger.info(f"Auto-named session {sess.id}: {title}")

    except Exception as e:
        import traceback
        logger.error(f"Auto-name failed for {sess.id}: {e}\n{traceback.format_exc()}")


def try_fallback_endpoint(sess, session_id: str) -> dict | None:
    """Find an alternative working endpoint when the current one fails.

    Returns {"model": ..., "endpoint_url": ..., "endpoint_name": ...} or None.
    """
    import requests as _req
    from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, normalize_base

    current_url = sess.endpoint_url or ""
    db = SessionLocal()
    try:
        endpoints = db.query(ModelEndpoint).filter(
            ModelEndpoint.is_enabled == True
        ).all()
    finally:
        db.close()

    for ep in endpoints:
        base = normalize_base(ep.base_url)
        # Skip current endpoint
        if current_url and base in current_url:
            continue
        # Quick ping
        ping_url = build_models_url(base)
        headers = build_headers(ep.api_key, base)
        try:
            r = _req.get(ping_url, headers=headers, timeout=5)
            r.raise_for_status()
            data = r.json()
            models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
            if not models:
                models = [
                    m.get("name") or m.get("model")
                    for m in (data.get("models") or [])
                    if m.get("name") or m.get("model")
                ]
            if not models:
                continue
            # Found a working endpoint — update session
            new_model = models[0]
            chat_url = build_chat_url(base)
            new_headers = build_headers(ep.api_key, base)

            sess.model = new_model
            sess.endpoint_url = chat_url
            sess.headers = new_headers

            # Persist
            _db = SessionLocal()
            try:
                _db.query(DBSession).filter(DBSession.id == session_id).update({
                    "model": new_model,
                    "endpoint_url": chat_url,
                    "headers": json.dumps(new_headers),
                })
                _db.commit()
            finally:
                _db.close()

            logger.info(f"Fallback: switched session {session_id} from {current_url} to {ep.name} ({new_model})")
            return {
                "model": new_model,
                "endpoint_url": chat_url,
                "endpoint_name": ep.name,
            }
        except Exception:
            continue

    return None


def extract_preset(chat_handler, preset_id) -> PresetInfo:
    """Extract preset parameters via chat_handler."""
    temperature, max_tokens, system_prompt, char_name = (
        chat_handler.validate_and_extract_preset(preset_id)
    )
    return PresetInfo(
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        character_name=char_name,
    )


async def preprocess(
    chat_handler, message, att_ids, sess,
    auto_opened_docs: Optional[list] = None,
) -> PreprocessedMessage:
    """Run chat_handler.preprocess_message and wrap the result."""
    enhanced, user_content, text_ctx, yt_transcripts, att_meta = (
        await chat_handler.preprocess_message(
            message, att_ids, sess, auto_opened_docs=auto_opened_docs
        )
    )
    return PreprocessedMessage(
        enhanced_message=enhanced,
        user_content=user_content,
        text_for_context=text_ctx,
        youtube_transcripts=yt_transcripts,
        attachment_meta=att_meta,
    )


def add_user_message(sess, chat_handler, preprocessed: PreprocessedMessage, incognito: bool = False):
    """Add user message to session history and update session name.
    In incognito mode, still add to in-memory history (for conversation context)
    but skip session name update (which would persist)."""
    user_meta = {"attachments": preprocessed.attachment_meta} if preprocessed.attachment_meta else None
    sess.add_message(ChatMessage("user", preprocessed.user_content, metadata=user_meta))
    if not incognito:
        chat_handler.update_session_name_if_needed(sess, preprocessed.text_for_context)


def fire_message_event(request, webhook_manager, session_id: str, sess, message: str, compare_mode: bool = False):
    """Fire webhook and event_bus events for a new user message."""
    if webhook_manager and not compare_mode:
        asyncio.create_task(webhook_manager.fire("chat.message", {
            "session_id": session_id, "model": sess.model, "message": message[:2000],
        }))
    from src.event_bus import fire_event
    user = get_current_user(request)
    fire_event("message_sent", user)


def resolve_session_auth(sess, session_id: str):
    """Ensure session has auth headers — resolve from endpoint DB if missing."""
    has_auth = sess.headers and isinstance(sess.headers, dict) and any(
        k.lower() in ('authorization', 'x-api-key') for k in sess.headers
    )
    if has_auth:
        return

    try:
        from src.endpoint_resolver import build_headers
        db = SessionLocal()
        try:
            domain = sess.endpoint_url.split("//")[1].split("/")[0] if "//" in sess.endpoint_url else ""
            if domain:
                ep = db.query(ModelEndpoint).filter(ModelEndpoint.base_url.contains(domain)).first()
                if ep and ep.api_key:
                    sess.headers = build_headers(ep.api_key, ep.base_url)
                    db.query(DBSession).filter(DBSession.id == session_id).update(
                        {"headers": json.dumps(sess.headers)}
                    )
                    db.commit()
                    logger.info(f"Resolved and persisted auth headers for session {session_id} from endpoint {ep.name}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to resolve session headers: {e}")


async def build_chat_context(
    sess,
    request,
    chat_handler,
    chat_processor,
    message: str,
    session_id: str,
    preset_id=None,
    att_ids: list = None,
    use_web=None,
    use_rag=None,
    use_research=None,
    time_filter=None,
    incognito: bool = False,
    no_memory: bool = False,
    search_context: str = None,
    compare_mode: bool = False,
    webhook_manager=None,
    use_enhanced_message: bool = False,
    agent_mode: bool = False,
) -> ChatContext:
    """Build the full context (preface + messages) for an LLM call.

    This is the shared logic between /chat and /chat_stream — preset extraction,
    message preprocessing, memory/RAG/web injection, compaction, normalization.
    """
    # Preset
    preset = extract_preset(chat_handler, preset_id)

    # Preprocess message (CoT, YouTube, VL images, build content). The
    # auto_opened_docs collector captures any docs created server-side
    # (e.g. fillable PDF → markdown editor doc) so the chat route can
    # announce them to the frontend before streaming.
    auto_opened_docs: list = []
    preprocessed = await preprocess(
        chat_handler, message, att_ids or [], sess,
        auto_opened_docs=auto_opened_docs,
    )

    # Add user message to history
    add_user_message(sess, chat_handler, preprocessed, incognito=incognito)

    # Fire events
    if not incognito:
        fire_message_event(request, webhook_manager, session_id, sess, message, compare_mode)

    # Resolve user prefs
    user = get_current_user(request)
    uprefs = load_prefs_for_user(user)

    # Memory enabled?
    mem_enabled = not incognito and not no_memory and uprefs.get("memory_enabled", True)
    # Skills injection respects its own enable toggle (mirrors memory_enabled).
    # When off, the "Available skills" index is not added to the prompt.
    skills_enabled = not incognito and uprefs.get("skills_enabled", True)
    logger.debug(
        "Memory enabled=%s for user=%s (incognito=%s, no_memory=%s, pref=%s)",
        mem_enabled, user, incognito, no_memory, uprefs.get("memory_enabled", "NOT_SET"),
    )

    # Use RAG?
    use_rag_val = (str(use_rag).lower() != "false") if use_rag is not None else True
    if incognito:
        use_rag_val = False

    # If pre-fetched search context was provided (compare mode), skip live web search
    skip_web = bool(search_context)

    # Build context preface
    # The stream path uses enhanced_message (with CoT/preprocessing applied),
    # the sync path uses text_for_context.
    _ctx_msg = preprocessed.enhanced_message if use_enhanced_message else preprocessed.text_for_context
    _preface_kwargs = dict(
        message=_ctx_msg,
        session=sess,
        use_web=use_web and not skip_web,
        use_memory=mem_enabled,
        time_filter=time_filter,
        preset_system_prompt=preset.system_prompt,
        owner=user,
        character_name=preset.character_name,
        agent_mode=agent_mode,
        incognito=incognito,
        use_skills=skills_enabled,
    )
    if use_rag is not None:
        _preface_kwargs["use_rag"] = use_rag_val
    preface, rag_sources, web_sources = chat_processor.build_context_preface(**_preface_kwargs)

    # Capture used memories immediately
    used_memories = getattr(chat_processor, '_last_used_memories', [])

    # Inject pre-fetched search context (compare mode)
    if search_context:
        preface.append(untrusted_context_message("prefetched search context", search_context))

    # YouTube transcripts
    for transcript in preprocessed.youtube_transcripts:
        preface.append(untrusted_context_message("youtube transcript", transcript))

    # Normalize model ID
    norm = normalize_model_id(sess.endpoint_url, sess.model)
    if norm:
        sess.model = norm

    # Build messages
    messages = preface + sess.get_context_messages()

    # Auto-compact
    messages, context_length, was_compacted = await maybe_compact(
        sess, sess.endpoint_url, sess.model, messages, sess.headers,
    )
    messages = trim_for_context(messages, context_length)

    return ChatContext(
        preface=preface,
        rag_sources=rag_sources,
        web_sources=web_sources,
        used_memories=used_memories,
        messages=messages,
        context_length=context_length,
        was_compacted=was_compacted,
        user=user,
        uprefs=uprefs,
        preset=preset,
        preprocessed=preprocessed,
        auto_opened_docs=auto_opened_docs,
    )


def accumulate_token_usage(session_id: str, metrics: dict):
    """Add input/output token counts to the session's running totals."""
    in_t = metrics.get("input_tokens", 0)
    out_t = metrics.get("output_tokens", 0)
    if not (in_t or out_t):
        return
    db = SessionLocal()
    try:
        db_s = db.query(DBSession).filter(DBSession.id == session_id).first()
        if db_s:
            db_s.total_input_tokens = (db_s.total_input_tokens or 0) + in_t
            db_s.total_output_tokens = (db_s.total_output_tokens or 0) + out_t
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _normalize_thinking(text: str) -> str:
    """Wrap inline thinking patterns in <think> tags so they persist on reload.

    Handles:
    - "Thinking Process:" (Qwen3.5)
    - Gemma-style inline reasoning ("The user said/asked...", "I should/need to...")
    - Garbled <think> tags (reasoning before the tag, unclosed tags)
    """
    import re
    if not text:
        return text
    reasoning_prefix_re = re.compile(
        r'^\s*(?:thinking(?:\s+process)?\s*:|the user |i need |i should |i will |they are |the question |i can )',
        re.IGNORECASE,
    )
    thinking_prefix_re = re.compile(r'^thinking(?:\s+process)?\s*:\s*', re.IGNORECASE)

    # Handle garbled <think> tags: reasoning text followed by <think> as separator
    # e.g. "The user said...I should respond.\n<think>Hey! What's up?"
    garbled = re.match(
        r'^([\s\S]+?)\n*<think(?:ing)?>\s*([\s\S]*?)(?:</think(?:ing)?>)?\s*$',
        text, re.IGNORECASE
    )
    if garbled:
        before = garbled.group(1).strip()
        after = garbled.group(2).strip()
        # Only treat as garbled if the part before <think> looks like reasoning
        reasoning_starts = (
            'The user ', 'I need ', 'I should ', 'I will ',
            'They are ', 'The question ', 'I can ',
            'Thinking Process', 'Thinking:',
        )
        stripped_before = before.lstrip()
        if any(stripped_before.startswith(p) for p in reasoning_starts) or reasoning_prefix_re.match(stripped_before):
            # Strip "Thinking:" prefix from the thinking content
            stripped_before = thinking_prefix_re.sub('', stripped_before)
            return '<think>' + stripped_before + '</think>\n' + after

    if '<think' in text.lower():
        return text  # already has proper think tags

    # Qwen3.5: "Thinking Process:" or "Thinking:" prefix
    if thinking_prefix_re.match(text.lstrip()):
        # Try clean boundary first
        m = re.match(
            r'^(Thinking(?:\s+Process)?:[\s\S]*?)(\n\n(?=[A-Z]|Hey|Yo|Hi|Sure|I |What|Here|Let|The |This |OK|Ok|Yes|No |So |Well |Thank|Alright|Of course|Absolutely|Great|Hello|As ))',
            text, re.IGNORECASE | re.MULTILINE
        )
        if m:
            think = thinking_prefix_re.sub('', m.group(1)).strip()
            return '<think>' + think + '</think>' + text[m.end()-2:]
        # Fallback: find last non-indented paragraph as reply
        parts = text.split('\n\n')
        for i in range(len(parts) - 1, 0, -1):
            line = parts[i].strip()
            if line and not re.match(r'^[\d*\-\s(]', line) and len(line) > 5:
                think = thinking_prefix_re.sub('', '\n\n'.join(parts[:i])).strip()
                reply = '\n\n'.join(parts[i:])
                return '<think>' + think + '</think>\n\n' + reply
        # Last resort: look for a quoted final response inside the thinking
        # Qwen often drafts the reply as "Option: ..." or * "reply text"
        last_quote = re.findall(r'["\u201c]([^"\u201d]{10,})["\u201d]', text)
        if last_quote:
            reply = last_quote[-1].strip()
            think = thinking_prefix_re.sub('', text).strip()
            return '<think>' + think + '</think>\n\n' + reply
        # Truly no reply found
        think = thinking_prefix_re.sub('', text).strip()
        return '<think>' + think + '</think>'

    # Gemma-style: starts with reasoning ("The user", "I need", "I should", etc.)
    stripped_text = text.lstrip()
    first_line = stripped_text.split('\n')[0].strip()
    reasoning_starts = (
        'The user ', 'I need ', 'I should ', 'I will ',
        'They are ', 'The question ', 'I can ',
    )
    reply_starts = (
        'Hey', 'Hi ', 'Hi!', 'Hello', 'Sure', 'Yes', 'No ', 'No,', 'Yo', 'OK',
        'Here', 'Absolutely', 'Of course', 'Great', 'Alright',
        'Thanks', 'Welcome', 'Good ', "I'm happy", "I'd be",
    )
    if any(first_line.startswith(p) for p in reasoning_starts):
        # Try line-by-line split first
        lines = stripped_text.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if i > 0 and any(stripped.startswith(p) for p in reply_starts):
                think = '\n'.join(lines[:i])
                reply = '\n'.join(lines[i:])
                return '<think>' + think + '</think>\n' + reply

        # Try within-line split — model mashed thinking + reply on one line
        # Look for reply pattern after a period or sentence end
        for p in reply_starts:
            # Match: "...reasoning text.Reply text" or "...reasoning text. Reply text"
            pattern = r'([.!?])\s*(' + re.escape(p) + r')'
            m = re.search(pattern, stripped_text)
            if m and m.start() > 20:  # at least 20 chars of reasoning before
                think = stripped_text[:m.start() + 1]  # include the period
                reply = stripped_text[m.start() + 1:].lstrip()
                return '<think>' + think + '</think>\n' + reply

        # Last resort: find last non-reasoning line
        for i in range(len(lines) - 1, 0, -1):
            stripped = lines[i].strip()
            if stripped and not any(stripped.startswith(p) for p in reasoning_starts) and not stripped.startswith('*') and len(stripped) > 3:
                think = '\n'.join(lines[:i])
                reply = '\n'.join(lines[i:])
                return '<think>' + think + '</think>\n' + reply

    return text


def _extract_thinking_meta(text: str) -> dict | None:
    """Extract thinking content into metadata, return {thinking, reply, time} or None."""
    import re
    if not text:
        return None

    # Check for <think> tags (native or injected)
    time_match = re.search(r'<think(?:ing)?\s+time="([\d.]+)"', text)
    think_time = time_match.group(1) if time_match else None
    # Strip time attr for parsing
    clean = re.sub(r'<think(?:ing)?\s+time="[\d.]+"', '<think', text)

    think_match = re.match(r'^[\s]*<think(?:ing)?>([\s\S]*?)</think(?:ing)?>\s*([\s\S]*)', clean, re.IGNORECASE)
    if think_match:
        thinking = think_match.group(1).strip()
        reply = think_match.group(2).strip()
        # Only strip the thinking out into metadata when there's an actual reply
        # left over. If reply is empty (model hit max_tokens inside <think>, or
        # the turn was reasoning-only), keep the raw text as content — otherwise
        # the saved message has empty content and the bubble looks blank on
        # reload. The renderer's processWithThinking still extracts the <think>
        # block visually at display time, so nothing changes for the normal case.
        if thinking and reply:
            return {"thinking": thinking, "reply": reply, "time": think_time}

    # Detect Thinking Process: or Gemma-style reasoning
    normalized = _normalize_thinking(text)
    if '<think>' in normalized:
        think_match2 = re.match(r'^[\s]*<think(?:ing)?>([\s\S]*?)</think(?:ing)?>\s*([\s\S]*)', normalized, re.IGNORECASE)
        if think_match2:
            thinking = think_match2.group(1).strip()
            reply = think_match2.group(2).strip()
            if thinking and reply:
                return {"thinking": thinking, "reply": reply, "time": think_time}

    return None


def clean_thinking_for_save(content: str, metadata: dict | None = None) -> tuple[str, dict]:
    """Extract thinking from content into metadata. Use for save paths that bypass save_assistant_response."""
    md = dict(metadata) if metadata else {}
    info = _extract_thinking_meta(content)
    if info:
        md["thinking"] = info["thinking"]
        if info.get("time"):
            md["thinking_time"] = info["time"]
        return info["reply"], md
    return content, md


def save_assistant_response(
    sess,
    session_manager,
    session_id: str,
    full_response: str,
    last_metrics: dict | None,
    *,
    character_name: str = None,
    web_sources: list = None,
    rag_sources: list = None,
    research_sources: list = None,
    used_memories: list = None,
    do_research: bool = False,
    tool_events: list = None,
    incognito: bool = False,
):
    """Add assistant response to session history. In incognito mode, keeps in-memory context but skips DB persistence."""
    md = dict(last_metrics) if last_metrics else {}
    md["model"] = sess.model
    if character_name:
        md["character_name"] = character_name
    if web_sources:
        md["web_sources"] = web_sources
    if rag_sources:
        md["rag_sources"] = rag_sources
    if research_sources:
        md["research_sources"] = research_sources
    if used_memories:
        md["memories_used"] = used_memories
    if do_research and not research_sources:
        md["research_clarification"] = True
    if tool_events:
        md["tool_events"] = tool_events

    # Extract thinking into metadata (don't pollute message content with <think> tags)
    _think_info = _extract_thinking_meta(full_response)
    if _think_info:
        md["thinking"] = _think_info["thinking"]
        md["thinking_time"] = _think_info.get("time")
        _content = _think_info["reply"]
    else:
        _content = full_response
    sess.add_message(ChatMessage("assistant", _content, metadata=md))

    if not incognito:
        from core.database import update_session_last_accessed
        update_session_last_accessed(session_id)
        session_manager.save_sessions()

    # Return the persisted message's DB id so the stream can wire it onto the
    # freshly-rendered bubble — lets the user edit/delete a just-streamed reply
    # without reloading. Incognito returns None: those messages are ephemeral,
    # so we don't hand out an edit/delete handle for them.
    if incognito:
        return None
    try:
        _last = sess.history[-1]
        _meta = getattr(_last, "metadata", None)
        if isinstance(_meta, dict):
            return _meta.get("_db_id")
    except (IndexError, AttributeError):
        pass
    return None


def run_post_response_tasks(
    sess,
    session_manager,
    session_id: str,
    message: str,
    full_response: str,
    last_metrics: dict | None,
    uprefs: dict,
    memory_manager,
    memory_vector,
    webhook_manager,
    *,
    incognito: bool = False,
    compare_mode: bool = False,
    character_name: str = None,
    agent_rounds: int = 0,
    agent_tool_calls: int = 0,
    skills_manager=None,
    owner: str = None,
    extract_skills: bool = True,
):
    """Fire background tasks after a completed response: memory extraction, webhooks, auto-name, skill extraction."""
    # Memory extraction — only every 4th message pair to avoid excess LLM calls
    _msg_count = len(sess.history) if hasattr(sess, 'history') else 0
    _should_extract = (_msg_count >= 4) and (_msg_count % 4 == 0)
    if not incognito and not compare_mode and _should_extract and uprefs.get("auto_memory", True):
        from services.memory.memory_extractor import extract_and_store
        from src.task_endpoint import resolve_task_endpoint
        t_url, t_model, t_headers = resolve_task_endpoint(
            sess.endpoint_url, sess.model, sess.headers,
        )
        asyncio.create_task(extract_and_store(
            sess, memory_manager, memory_vector,
            t_url, t_model, t_headers,
        ))

    # Skill extraction from complex agent runs. Only when the user actually
    # chose agent mode — not a chat we auto-escalated for a notes/calendar
    # intent, and never in incognito/compare.
    auto_skills_enabled = bool(uprefs.get("auto_skills", True))
    # Quiet by default — full gate/dispatch/start trace runs at DEBUG so
    # users can re-enable diagnostics with LOG_LEVEL=DEBUG when something
    # silently breaks. INFO-level only shows the outcome inside
    # maybe_extract_skill (Auto-extracted / dropped / failed).
    logger.debug(
        "[skill-extract] gate: extract_skills=%s auto_skills=%s incognito=%s "
        "compare=%s rounds=%d tools=%d skills_manager=%s",
        extract_skills, auto_skills_enabled, incognito, compare_mode,
        agent_rounds, agent_tool_calls, "set" if skills_manager else "MISSING",
    )
    if (
        extract_skills
        and auto_skills_enabled
        and not incognito
        and not compare_mode
        and (agent_rounds >= 2 or agent_tool_calls >= 2)
    ):
        if skills_manager is None:
            logger.warning(
                "[skill-extract] gate PASSED but skills_manager is None — "
                "extraction skipped. (Bug: caller didn't pass skills_manager.)"
            )
        else:
            from services.memory.skill_extractor import maybe_extract_skill
            from src.task_endpoint import resolve_task_endpoint
            s_url, s_model, s_headers = resolve_task_endpoint(
                sess.endpoint_url, sess.model, sess.headers,
            )
            logger.debug("[skill-extract] dispatching extractor (model=%s)", s_model)
            asyncio.create_task(maybe_extract_skill(
                sess, skills_manager,
                s_url, s_model, s_headers,
                agent_rounds, agent_tool_calls,
                owner=owner,
            ))

    # Token accumulation
    if last_metrics:
        accumulate_token_usage(session_id, last_metrics)

    # Webhook
    if webhook_manager and not compare_mode:
        asyncio.create_task(webhook_manager.fire("chat.completed", {
            "session_id": session_id, "model": sess.model,
            "user_message": message, "response": full_response[:2000],
        }))

    # Auto-name
    if needs_auto_name(sess.name):
        asyncio.create_task(auto_name_session(session_manager, sess))
