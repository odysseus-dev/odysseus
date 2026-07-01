"""Shared helpers for chat routes — context building, post-response tasks, auth resolution."""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core.models import ChatMessage
from core.database import SessionLocal
from core.database import Session as DBSession, ModelEndpoint
from src.llm_core import normalize_model_id
from src.endpoint_resolver import normalize_base
from src.context_compactor import maybe_compact, trim_for_context
from src.auth_helpers import effective_user
from src.prompt_security import untrusted_context_message
from routes.prefs_routes import _load_for_user as load_prefs_for_user

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_CASUAL_OPENING_RE = re.compile(
    r"^\s*(?:h+i+|hey+|hello+|yo+|sup+|what'?s up|wass?up|hiya|howdy|"
    r"lol|lmao|haha+|hehe+|thanks?|thank you|ty|idk|dunno|meh|bruh|bro)\b(?P<tail>.*)$",
    re.IGNORECASE,
)
_CASUAL_BLOCKLIST_RE = re.compile(
    r"\b(?:cookbook|serve|serving|launch|start|vllm|sglang|llama\.?cpp|ollama|"
    r"download|model|email|document|doc|note|calendar|task|search|web|research|"
    r"file|folder|repo|git|settings?|endpoint|api|token|mcp)\b",
    re.IGNORECASE,
)


def _is_casual_low_signal(text: str) -> bool:
    """Short greetings/slang should not pull memory, skills, RAG, or docs."""
    s = str(text or "").strip()
    m = _CASUAL_OPENING_RE.match(s)
    if not m:
        return False
    tail = m.group("tail") or ""
    if _CASUAL_BLOCKLIST_RE.search(tail):
        return False
    tail_words = re.findall(r"[A-Za-z0-9_'-]+", tail)
    return len(tail_words) <= 2


# Strong references to in-flight fire-and-forget tasks scheduled from this
# module. asyncio only keeps weak references to tasks created via
# create_task, so without this the GC can collect a task mid-execution and
# the background work (extraction, auto-naming) silently never runs.
# Mirrors WebhookManager._spawn_tracked from src/webhook_manager.py.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn_bg(coro) -> asyncio.Task:
    """Schedule a background task and hold a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


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
    # Uploads attached to this user turn, resolved and owner-checked for the
    # agent's private context. This is not emitted to the browser.
    uploaded_files: list = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────────── #

def _enforce_chat_privileges(request, sess) -> None:
    """Apply the per-user privilege gates (allowed_models + max_messages_per_day)
    that both /api/chat and /api/chat_stream must enforce BEFORE any LLM work.

    Raises HTTPException(403) if the session's model is not in the user's
    allowlist, or HTTPException(429) if the user has hit their daily message
    cap. No-op for unauthenticated callers or when auth_manager is absent
    (single-user mode). Admins receive ADMIN_PRIVILEGES from get_privileges,
    which means unrestricted allowed_models / zero cap -> no-op for them.
    """
    try:
        user = effective_user(request)
    except Exception:
        user = None
    if not user:
        return
    auth_manager = getattr(getattr(request.app, "state", None), "auth_manager", None)
    if not auth_manager:
        return

    privs = auth_manager.get_privileges(user) or {}

    # Explicit "block everything" sentinel takes precedence over the
    # allowlist — it's the only way to distinguish "user clicked [None]"
    # (block all) from "user clicked [All]" (no restriction), since both
    # otherwise produce an empty `allowed_models` list.
    if privs.get("block_all_models"):
        raise HTTPException(403, f"Your account is not allowed to use model '{sess.model}'.")

    allowed_raw = privs.get("allowed_models")
    allowed = allowed_raw if isinstance(allowed_raw, list) else []
    restricted = bool(privs.get("allowed_models_restricted")) or bool(allowed)
    if restricted and sess.model and sess.model not in allowed:
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
    if re.match(r"^.+ \d{1,2}:\d{2}:\d{2}(\s*(AM|PM))?$", name, re.IGNORECASE):
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

        owner = getattr(sess, "owner", None)
        t_url, t_model, t_headers = resolve_task_endpoint(
            sess.endpoint_url, sess.model, sess.headers, owner=owner
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
    from src.endpoint_resolver import (
        build_chat_url,
        build_headers,
        build_models_url,
        normalize_base,
        resolve_endpoint_runtime,
    )
    from src.chatgpt_subscription import is_chatgpt_subscription_base

    current_url = sess.endpoint_url or ""
    owner = getattr(sess, "owner", None)
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(
            ModelEndpoint.is_enabled == True
        )
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.all()
    finally:
        db.close()

    for ep in endpoints:
        base = normalize_base(ep.base_url)
        # Skip current endpoint
        if current_url and base in current_url:
            continue
        try:
            base, api_key = resolve_endpoint_runtime(ep, owner=owner)
        except Exception:
            continue
        ping_url = build_models_url(base)
        headers = build_headers(api_key, base)
        try:
            if ping_url:
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
            else:
                models = json.loads(ep.cached_models or "[]")
            if not models:
                continue
            # Found a working endpoint — update session
            new_model = models[0]
            chat_url = build_chat_url(base)
            new_headers = build_headers(api_key, base)
            persisted_headers = {} if is_chatgpt_subscription_base(base) else new_headers

            sess.model = new_model
            sess.endpoint_url = chat_url
            sess.headers = new_headers

            # Persist
            _db = SessionLocal()
            try:
                _db.query(DBSession).filter(DBSession.id == session_id).update({
                    "model": new_model,
                    "endpoint_url": chat_url,
                    "headers": persisted_headers,
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
    allow_tool_preprocessing: bool = True,
) -> PreprocessedMessage:
    """Run chat_handler.preprocess_message and wrap the result."""
    enhanced, user_content, text_ctx, yt_transcripts, att_meta = (
        await chat_handler.preprocess_message(
            message,
            att_ids,
            sess,
            auto_opened_docs=auto_opened_docs,
            allow_tool_preprocessing=allow_tool_preprocessing,
        )
    )
    return PreprocessedMessage(
        enhanced_message=enhanced,
        user_content=user_content,
        text_for_context=text_ctx,
        youtube_transcripts=yt_transcripts,
        attachment_meta=att_meta,
    )


def build_uploaded_file_manifest(att_ids: list, upload_handler, owner: Optional[str]) -> list[dict]:
    """Resolve current-turn upload IDs into a small tool-facing manifest.

    The chat UI already sends attachment ids, and preprocessing inlines as much
    text as fits. Agent mode still needs a discoverable bridge for files whose
    content was truncated/omitted or when the model chooses file tools. Only
    owner-authorized uploads are included, and paths must remain inside the
    configured upload directory.
    """
    if not att_ids or not upload_handler or not hasattr(upload_handler, "resolve_upload"):
        return []

    def _read_file_can_open(path: str) -> bool:
        try:
            from src.tool_execution import _resolve_tool_path

            return _resolve_tool_path(path) == os.path.realpath(path)
        except Exception:
            return False

    manifest: list[dict] = []
    for att_id in att_ids:
        try:
            info = upload_handler.resolve_upload(str(att_id), owner=owner)
        except Exception:
            logger.debug("Failed to resolve upload %r for agent manifest", att_id, exc_info=True)
            continue
        if not isinstance(info, dict):
            continue

        path = info.get("path")
        if path:
            try:
                inside = True
                if hasattr(upload_handler, "_inside_upload_dir"):
                    inside = bool(upload_handler._inside_upload_dir(path))
                elif hasattr(upload_handler, "inside_base_dir"):
                    inside = bool(upload_handler.inside_base_dir(path))
                if not inside or not os.path.exists(path) or not _read_file_can_open(path):
                    path = None
            except Exception:
                path = None

        manifest.append({
            "id": info.get("id") or str(att_id),
            "name": info.get("name") or info.get("original_name") or str(att_id),
            "mime": info.get("mime", ""),
            "size": info.get("size", 0),
            "path": path,
        })
    return manifest


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
        webhook_manager.fire_and_forget("chat.message", {
            "session_id": session_id, "model": sess.model, "message": message[:2000],
        })
    from src.event_bus import fire_event
    user = effective_user(request)
    fire_event("message_sent", user)


def _session_url_matches_endpoint(session_url: str, endpoint_base: str) -> bool:
    if not session_url or not endpoint_base:
        return False
    try:
        from src.endpoint_resolver import build_chat_url, normalize_base

        sess_url = session_url.rstrip("/")
        base = normalize_base(endpoint_base).rstrip("/")
        return sess_url in {
            base,
            base + "/chat/completions",
            build_chat_url(base).rstrip("/"),
        }
    except Exception:
        return False


def _has_auth_keys(headers) -> bool:
    """True if a headers dict carries an Authorization/x-api-key entry."""
    return isinstance(headers, dict) and any(
        k.lower() in ('authorization', 'x-api-key') for k in headers
    )


def resolve_session_auth(sess, session_id: str, owner: Optional[str] = None):
    """Ensure session has auth headers — resolve from endpoint DB if missing."""
    try:
        from src.chatgpt_subscription import is_chatgpt_subscription_base
        is_chatgpt_subscription = is_chatgpt_subscription_base(getattr(sess, "endpoint_url", "") or "")
    except Exception:
        is_chatgpt_subscription = False
    has_auth = _has_auth_keys(sess.headers)
    if has_auth and not is_chatgpt_subscription:
        return

    try:
        from src.endpoint_resolver import build_headers, resolve_endpoint_runtime
        db = SessionLocal()
        try:
            target_url = getattr(sess, "endpoint_url", "") or ""
            if not target_url:
                return
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if owner:
                # Missing headers usually means "recover from the saved endpoint".
                # Scope that lookup to the session owner, otherwise two users
                # with similar endpoint URLs can borrow each other's API key.
                from src.auth_helpers import owner_filter
                q = owner_filter(q, ModelEndpoint, owner)
            for ep in q.all():
                if not _session_url_matches_endpoint(target_url, ep.base_url or ""):
                    continue
                try:
                    base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                except Exception as e:
                    logger.warning("Failed to resolve provider auth for session %s: %s", session_id, e)
                    return
                if not api_key:
                    # No usable key (e.g. ChatGPT Subscription needs re-auth).
                    return
                sess.headers = build_headers(api_key, base)
                if is_chatgpt_subscription:
                    # The bearer is short-lived and re-resolved per request, so it
                    # stays request-local and is never written to the plaintext
                    # sessions.headers column. Proactively strip any bearer an
                    # older code path may have persisted so it does not linger.
                    stale_q = db.query(DBSession).filter(DBSession.id == session_id)
                    if owner:
                        stale_q = stale_q.filter(DBSession.owner == owner)
                    stored = stale_q.first()
                    if stored is not None and _has_auth_keys(stored.headers):
                        stale_q.update({"headers": {}})
                        db.commit()
                        logger.info(f"Cleared persisted ChatGPT Subscription bearer from session {session_id}")
                    logger.debug(f"Resolved request-local ChatGPT Subscription auth for session {session_id}")
                    return
                update_q = db.query(DBSession).filter(DBSession.id == session_id)
                if owner:
                    update_q = update_q.filter(DBSession.owner == owner)
                update_q.update({"headers": sess.headers})
                db.commit()
                logger.info(f"Resolved and persisted auth headers for session {session_id} from endpoint {ep.name}")
                return
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Failed to resolve session headers: {e}")


def _match_cached_model_id(requested: str, models) -> Optional[str]:
    if not requested or not models:
        return None
    model_ids = [str(m) for m in models if m]
    if requested in model_ids:
        return requested

    req_base = os.path.basename(requested.rstrip("/"))
    for model_id in model_ids:
        if os.path.basename(model_id.rstrip("/")) == req_base:
            return model_id
    return None


def _normalize_model_id_from_cache(sess) -> Optional[str]:
    """Use stored endpoint model IDs before falling back to a live /models probe."""
    endpoint_url = getattr(sess, "endpoint_url", "") or ""
    requested = getattr(sess, "model", "") or ""
    if not endpoint_url or not requested:
        return None

    try:
        session_base = normalize_base(endpoint_url)
    except Exception:
        session_base = endpoint_url.rstrip("/")
    if not session_base:
        return None

    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        owner = getattr(sess, "owner", None)
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.all()
        for ep in endpoints:
            try:
                if normalize_base(getattr(ep, "base_url", "") or "") != session_base:
                    continue
            except Exception:
                continue

            raw_models = getattr(ep, "cached_models", None)
            if not raw_models:
                continue
            try:
                models = json.loads(raw_models) if isinstance(raw_models, str) else raw_models
            except Exception:
                continue

            matched = _match_cached_model_id(requested, models)
            if matched:
                return matched
    except Exception as e:
        logger.debug("Cached model normalization skipped: %s", e)
    finally:
        db.close()

    return None


def _session_is_research_spinoff(sess) -> bool:
    """True if this session was created via research "Discuss" spin-off.

    Detected by the primer system message the spin-off endpoint seeds into
    history (metadata ``research_spinoff_from``). Such sessions are grounded
    on the seeded report, so global memory + personal-doc RAG injection is
    suppressed for them (the report is the sole knowledge base). Handles both
    ChatMessage objects and plain dicts.
    """
    for m in getattr(sess, "history", []) or []:
        role = getattr(m, "role", None)
        if role is None and isinstance(m, dict):
            role = m.get("role")
        if role != "system":
            continue
        md = getattr(m, "metadata", None)
        if md is None and isinstance(m, dict):
            md = m.get("metadata")
        if (md or {}).get("research_spinoff_from"):
            return True
    return False


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
    allow_tool_preprocessing: bool = True,
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
        allow_tool_preprocessing=allow_tool_preprocessing,
    )

    # Add user message to history
    add_user_message(sess, chat_handler, preprocessed, incognito=incognito)

    # Fire events
    if not incognito:
        fire_message_event(request, webhook_manager, session_id, sess, message, compare_mode)

    # Resolve owner-scoped prefs/context. Browser requests keep the cookie user;
    # bearer-token chat requests use the token owner instead of the "api" sentinel.
    user = effective_user(request)
    uprefs = load_prefs_for_user(user)
    uploaded_files = build_uploaded_file_manifest(
        att_ids or [],
        getattr(chat_handler, "upload_handler", None),
        getattr(sess, "owner", None),
    )
    casual_low_signal = _is_casual_low_signal(message)

    # Memory enabled?
    mem_enabled = not incognito and not no_memory and uprefs.get("memory_enabled", True)
    # Skills injection respects its own enable toggle (mirrors memory_enabled).
    # When off, the "Available skills" index is not added to the prompt.
    skills_enabled = not incognito and uprefs.get("skills_enabled", True)
    if not allow_tool_preprocessing:
        mem_enabled = False
        skills_enabled = False
    if casual_low_signal:
        mem_enabled = False
        skills_enabled = False
    logger.debug(
        "Memory enabled=%s for user=%s (incognito=%s, no_memory=%s, pref=%s)",
        mem_enabled, user, incognito, no_memory, uprefs.get("memory_enabled", "NOT_SET"),
    )

    # Research-spinoff ("Discuss") sessions are grounded on the seeded report:
    # the primer system message IS the knowledge base. Injecting global memory
    # or personal-doc RAG on every turn pulls in keyword-matched but off-topic
    # facts ("wrong data") and competes with the report, so suppress both here.
    is_research_spinoff = _session_is_research_spinoff(sess)
    if is_research_spinoff:
        mem_enabled = False

    # Use RAG?
    use_rag_val = (str(use_rag).lower() != "false") if use_rag is not None else True
    if incognito or not allow_tool_preprocessing or is_research_spinoff or casual_low_signal:
        use_rag_val = False

    # If pre-fetched search context was provided (compare mode), skip live web search
    skip_web = bool(search_context) or not allow_tool_preprocessing or casual_low_signal

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
    if use_rag is not None or is_research_spinoff or casual_low_signal:
        _preface_kwargs["use_rag"] = use_rag_val
    preface, rag_sources, web_sources = chat_processor.build_context_preface(**_preface_kwargs)

    # Capture used memories immediately
    used_memories = getattr(chat_processor, '_last_used_memories', [])

    # Inject pre-fetched search context (compare mode)
    if search_context and allow_tool_preprocessing and not casual_low_signal:
        preface.append(untrusted_context_message("prefetched search context", search_context))

    # YouTube transcripts
    for transcript in preprocessed.youtube_transcripts:
        preface.append(untrusted_context_message("youtube transcript", transcript))

    # Normalize model ID. Prefer cached endpoint models so group chat does not
    # re-hit slow local /models endpoints on every participant turn.
    norm = _normalize_model_id_from_cache(sess) or normalize_model_id(
        sess.endpoint_url,
        sess.model,
        owner=getattr(sess, "owner", None),
    )
    if norm:
        sess.model = norm

    # Build messages
    messages = preface + sess.get_context_messages()

    # Current date/time — injected as a standalone *user*-role context message
    # placed immediately before the latest user turn, NOT folded into the
    # system prompt. Its text changes every minute, and local OpenAI-compatible
    # backends (llama.cpp / LM Studio) key their KV-cache prefix off the
    # system message byte-for-byte; mixing ever-changing timestamp text into
    # it would invalidate the cached prefix on every request (issue #2927).
    # Placing it at the tail also keeps it out of the stable
    # preface+history prefix, so that prefix stays byte-identical turn over
    # turn (modulo the genuinely new history entries) and the cache survives.
    if not agent_mode:
        try:
            from src.user_time import current_datetime_context_message
            _dt_msg = current_datetime_context_message()
            if messages and messages[-1].get("role") == "user":
                messages.insert(len(messages) - 1, _dt_msg)
            else:
                messages.append(_dt_msg)
        except Exception:
            logger.debug("Failed to add current date/time context", exc_info=True)

    # Auto-compact
    messages, context_length, was_compacted = await maybe_compact(
        sess, sess.endpoint_url, sess.model, messages, sess.headers, owner=user,
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
        uploaded_files=uploaded_files,
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
    from src.text_helpers import normalize_thinking_markup
    text = normalize_thinking_markup(text)
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
    from src.text_helpers import normalize_thinking_markup
    original_text = text
    text = normalize_thinking_markup(text)
    normalized_changed = text != original_text

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

    if normalized_changed and text.strip() and text.strip() != original_text.strip():
        return {"thinking": "", "reply": text.strip(), "time": think_time}

    return None


def clean_thinking_for_save(content: str, metadata: dict | None = None) -> tuple[str, dict]:
    """Extract thinking from content into metadata. Use for save paths that bypass save_assistant_response."""
    md = dict(metadata) if metadata else {}
    info = _extract_thinking_meta(content)
    if info:
        if info.get("thinking"):
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
    def _model_value(value) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.strip()

    requested_model = _model_value(md.get("requested_model") or md.get("selected_model") or getattr(sess, "model", ""))
    actual_model = _model_value(md.get("model") or md.get("actual_model") or requested_model)
    if requested_model:
        md["requested_model"] = requested_model
    if actual_model:
        md["model"] = actual_model
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
        if _think_info.get("thinking"):
            md["thinking"] = _think_info["thinking"]
        if _think_info.get("time"):
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


def _is_session_stream_active(session_id: str) -> bool:
    """Best-effort check for "is a chat completion currently streaming for
    this session?" — used to keep background extraction from overlapping a
    main completion and competing for the local backend's processing slots
    (issue #2927). Lazily imports the route module's live registry to avoid
    a circular import (chat_routes imports this module at load time)."""
    try:
        from routes import chat_routes as _cr
        return session_id in getattr(_cr, "_active_streams", {})
    except Exception:
        return False


async def _run_extraction_jobs_sequentially(session_id: str, jobs: list, max_wait_s: float = 120.0):
    """Run queued background-extraction coroutines one at a time, only once
    no chat completion is actively streaming for this session.

    As diagnosed in issue #2927, firing memory/skill extraction concurrently
    with the main chat completion (or with each other) makes them compete for
    the local backend's limited processing slots, evicting the main
    conversation's cached KV-cache checkpoint and forcing a full prompt
    re-evaluation on the next turn. Waiting for the stream to go idle and then
    running the jobs strictly in sequence keeps at most one "side" request in
    flight against the backend at any time, and never alongside the user's
    own conversation.
    """
    # Wait for the triggering turn's own stream to finish winding down (it
    # almost always already has by the time this task gets scheduled — this
    # is a small safety margin, not the primary mechanism).
    waited = 0.0
    poll = 0.25
    while _is_session_stream_active(session_id) and waited < max_wait_s:
        await asyncio.sleep(poll)
        waited += poll

    for name, job in jobs:
        # Re-check before each job: a fast follow-up message from the user
        # may have started a new stream for this session while we waited.
        waited = 0.0
        while _is_session_stream_active(session_id) and waited < max_wait_s:
            await asyncio.sleep(poll)
            waited += poll
        try:
            await job
        except Exception:
            logger.warning("[bg-extract] %s extraction job failed for session %s", name, session_id, exc_info=True)


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
    allow_background_extraction: bool = True,
):
    """Fire background tasks after a completed response: memory extraction, webhooks, auto-name, skill extraction.

    Memory/skill extraction are queued to run *sequentially*, after the main
    completion stream for this session has fully wound down — never
    concurrently with it or with each other. As diagnosed in issue #2927,
    firing these "side" LLM calls in parallel with the main chat completion
    makes them compete for the local backend's limited processing slots
    (llama.cpp defaults to 4), evicting the main conversation's cached
    checkpoint and forcing a full prompt re-evaluation on the next turn. By
    the time this function runs the main response is already saved, but the
    extraction calls themselves are still async — queuing them through
    ``_queue_background_extraction`` keeps them from overlapping the *next*
    turn's request too.
    """
    _extraction_jobs: list = []

    # Memory extraction — only every 4th message pair to avoid excess LLM calls
    _msg_count = len(sess.history) if hasattr(sess, 'history') else 0
    _should_extract = (_msg_count >= 4) and (_msg_count % 4 == 0)
    if allow_background_extraction and not incognito and not compare_mode and _should_extract and uprefs.get("auto_memory", True):
        from services.memory.memory_extractor import extract_and_store
        from src.task_endpoint import resolve_task_endpoint
        t_url, t_model, t_headers = resolve_task_endpoint(
            sess.endpoint_url, sess.model, sess.headers, owner=owner,
        )
        _extraction_jobs.append(("memory", extract_and_store(
            sess, memory_manager, memory_vector,
            t_url, t_model, t_headers,
        )))

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
        and allow_background_extraction
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
                sess.endpoint_url, sess.model, sess.headers, owner=owner,
            )
            logger.debug("[skill-extract] dispatching extractor (model=%s)", s_model)
            _extraction_jobs.append(("skill", maybe_extract_skill(
                sess, skills_manager,
                s_url, s_model, s_headers,
                agent_rounds, agent_tool_calls,
                owner=owner,
            )))

    if _extraction_jobs:
        _spawn_bg(_run_extraction_jobs_sequentially(session_id, _extraction_jobs))

    # Token accumulation
    if last_metrics:
        accumulate_token_usage(session_id, last_metrics)

    # Webhook
    if webhook_manager and not compare_mode:
        webhook_manager.fire_and_forget("chat.completed", {
            "session_id": session_id, "model": sess.model,
            "user_message": message, "response": full_response[:2000],
        })

    # Auto-name
    if needs_auto_name(sess.name):
        _spawn_bg(auto_name_session(session_manager, sess))
