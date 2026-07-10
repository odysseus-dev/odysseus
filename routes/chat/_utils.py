"""Chat routes — /api/chat, /api/chat_stream, /api/inject_context, /api/search."""

import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, List, Optional

from fastapi import APIRouter, Request, HTTPException, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from core.models import ChatMessage
from src.request_models import ChatRequest
from src.llm_core import llm_call_async, stream_llm, stream_llm_with_fallback
from src.agent_loop import stream_agent_loop
from src import agent_runs
from src.model_context import estimate_tokens
from src.chat_helpers import coerce_message_and_session
from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url
from src.session_search import search_session_messages
from src.prompt_security import untrusted_context_message
from core.exceptions import SessionNotFoundError
from src.auth_helpers import effective_user, get_current_user
from routes.session_routes import _verify_session_owner
from routes.document_helpers import _owner_session_filter
from core.database import SessionLocal, get_session_mode, set_session_mode
from core.database import Session as DBSession, ChatMessage as DBChatMessage
from core.database import Document as DBDocument, ModelEndpoint
from core.log_safety import redact_url
from routes.research_routes import _resolve_research_endpoint
from routes.model import _visible_models
from routes.chat_helpers import (
    resolve_session_auth,
    build_chat_context,
    save_assistant_response,
    run_post_response_tasks,
    clean_thinking_for_save,
    _enforce_chat_privileges,
)
from src.action_intents import ToolIntent, classify_tool_intent as _classify_tool_intent
from src.tool_policy import build_effective_tool_policy

logger = logging.getLogger(__name__)

# Track active streams for partial-save safety net
_active_streams: Dict[str, dict] = {}
_IMAGE_MODEL_PREFIXES = (
    "gpt-image",
    "dall-e",
    "chatgpt-image",
    "flux",
    "qwen-image",
    "imagen",
    "models/imagen",
)
_VIDEO_MODEL_HINTS = (
    "text-to-video",
    "image-to-video",
    "video-generation",
    "t2v",
    "i2v",
    "veo-",
    "sora",
    "wan2",
    "wanx",
    "wan-ai",
    "kling",
    "runway",
    "luma",
    "vidu",
    "pixverse",
    "hailuo",
    "happyhorse",
)
_MEDIA_RESULT_KEYS = (
    "media_url", "media_id", "media_type", "media_prompt", "media_model",
    "media_size", "media_quality", "media_files",
    "image_url", "image_id", "image_prompt", "image_model", "image_size",
    "image_quality",
)


def _stream_set(session_id: str, **fields) -> None:
    """Update fields on the active-stream entry for `session_id`, or
    no-op if the entry has already been popped. Using .get() avoids a
    KeyError race between `if x in d` and `d[x]["k"] = v` if a sibling
    finally pops the key in between (which becomes possible the moment
    a coroutine cancellation reaches an inner cleanup before the
    outermost cleanup runs)."""
    rec = _active_streams.get(session_id)
    if rec is None:
        return
    rec.update(fields)


def _message_plain_text(content: Any) -> str:
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content or "")


def _last_user_plain_text(messages: List[Dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            return _message_plain_text(msg.get("content"))
    return ""


def _ensure_current_request_is_latest_user(messages: List[Dict[str, Any]], current_message: str) -> List[Dict[str, Any]]:
    """Defensively keep detached streams grounded on the request that created them."""
    current = str(current_message or "").strip()
    if not current:
        return messages
    latest = _last_user_plain_text(messages).strip()
    if latest == current or current in latest or latest in current:
        return messages
    logger.warning(
        "[chat_stream] latest user context mismatch; appending current request for model call. latest=%r current=%r",
        latest[:120],
        current[:120],
    )
    repaired = list(messages or [])
    repaired.append({"role": "user", "content": current})
    return repaired


_WEB_FOLLOWUP_RE = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:check|try\s+again|look(?:\s+now|\s+it\s+up)?|search(?:\s+now|\s+online|\s+it)?|"
    r"do\s+it|again)\??\s*$",
    re.I,
)
_RECENT_WEB_CONTEXT_RE = re.compile(
    r"\b(?:weather|forecast|rain|raining|hourly|news|headlines|rate|exchange|currency|"
    r"price|current|latest|search|look\s+up|online)\b",
    re.I,
)


def _recent_session_text(sess, limit: int = 8, max_chars: int = 2000) -> str:
    history = getattr(sess, "history", None) or getattr(sess, "_history", None) or []
    chunks: List[str] = []
    for msg in history[-limit:]:
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        text = _message_plain_text(content).strip()
        if text:
            chunks.append(text)
    return " ".join(chunks)[-max_chars:]


def _is_contextual_web_followup(message: str, sess) -> bool:
    """Treat short retry/check replies as web lookups when recent context was web."""
    if not message or not _WEB_FOLLOWUP_RE.search(message):
        return False
    return bool(_RECENT_WEB_CONTEXT_RE.search(_recent_session_text(sess)))


def _resolve_request_workspace(request, raw_value) -> tuple:
    """Resolve the posted workspace for this request: (workspace, rejected).

    Privilege is checked BEFORE the path ever touches the filesystem. Only
    admin/single-user callers can use the workspace-backed file/shell tools,
    so only they get vet_workspace() and the workspace_rejected signal. For
    any other caller the submitted value is dropped uniformly, with no vetting
    and no event: otherwise the presence/absence of workspace_rejected would
    let a non-admin chat caller probe which host paths exist.

    vet_workspace rejects non-directories, sensitive roots (.ssh, .gnupg,
    ...), and filesystem roots; on rejection there is no confinement and the
    default tool-path allowlist applies. The rejected value is surfaced so the
    stream can tell an admin client (which believes a workspace is active)
    that it was dropped.
    """
    requested = (raw_value or "").strip()
    if not requested:
        return "", ""
    from src.tool_security import owner_is_admin_or_single_user
    if not owner_is_admin_or_single_user(get_current_user(request)):
        return "", ""
    from src.tool_execution import vet_workspace
    workspace = vet_workspace(requested) or ""
    return workspace, (requested if not workspace else "")


_WORKSPACE_FILE_EXT = (
    r"(?:py|pyw|js|mjs|cjs|ts|tsx|jsx|java|kt|kts|xml|json|jsonc|toml|"
    r"ya?ml|txt|md|css|scss|html?|gradle|properties|lock|sh|bash|ps1|"
    r"bat|cmd|sql|rs|go|c|cc|cpp|h|hpp|cs|rb|php|lua)"
)
_WORKSPACE_LOCAL_PATH_RE = re.compile(
    rf"(?:[A-Za-z]:[\\/][^\s`'\"<>]+|(?:\.{{1,2}}[\\/]|~[\\/]|/)[^\s`'\"<>]+|"
    rf"(?:[\w.-]+[\\/])+[^\s`'\"<>]+|\b[\w.-]+\.{_WORKSPACE_FILE_EXT}\b)"
)
_WORKSPACE_FILE_ACTION_RE = re.compile(
    r"\b(?:open|read|show|list|browse|inspect|check|search|find|edit|modify|"
    r"change|write|save|create|delete|remove|rename|move|fix|update|patch|refactor|"
    r"build|run|test|look\s+(?:at|in|through)|work\s+on)\b",
    re.I,
)
_WORKSPACE_FILE_TARGET_RE = re.compile(
    r"\b(?:files?|folders?|director(?:y|ies)|repo|repository|project|workspace|"
    r"codebase|source|tree|path)\b",
    re.I,
)


def _message_mentions_workspace_files(message: Any) -> bool:
    text = str(message or "")
    if not text.strip():
        return False
    if _WORKSPACE_LOCAL_PATH_RE.search(text):
        return True
    return bool(
        _WORKSPACE_FILE_ACTION_RE.search(text)
        and _WORKSPACE_FILE_TARGET_RE.search(text)
    )


def _session_url_matches_endpoint(session_url: str, endpoint_base: str) -> bool:
    if not session_url or not endpoint_base:
        return False
    sess = session_url.rstrip("/")
    base = _normalize_base(endpoint_base).rstrip("/")
    variants = {
        base,
        base + "/chat/completions",
        build_chat_url(base).rstrip("/"),
    }
    return sess in variants or sess.startswith(base + "/")


def _clear_orphaned_session_endpoint(sess, owner: str | None = None) -> bool:
    """Clear a session model if its endpoint was deleted from ModelEndpoint."""
    if not getattr(sess, "endpoint_url", ""):
        return False
    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.all()
        for ep in endpoints:
            if _session_url_matches_endpoint(sess.endpoint_url or "", ep.base_url or ""):
                return False
        db_session = db.query(DBSession).filter(DBSession.id == sess.id).first()
        if db_session:
            db_session.endpoint_url = ""
            db_session.model = ""
            db_session.updated_at = datetime.utcnow()
            db.commit()
        sess.endpoint_url = ""
        sess.model = ""
        sess.headers = {}
        return True
    except Exception as e:
        logger.warning("Failed to clear orphaned session endpoint", exc_info=e)
        db.rollback()
        return False
    finally:
        db.close()


def _endpoint_cache_contains_model(endpoint, model: str) -> bool:
    """Return True when a populated endpoint model cache includes ``model``.

    Empty/malformed caches are treated as unknown rather than a negative match
    so older image endpoints without cached models still work.
    """
    raw = getattr(endpoint, "cached_models", None)
    if not raw:
        return True
    try:
        models = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning("Failed to parse cached models list, treating as containing model", exc_info=e)
        return True
    if not isinstance(models, list) or not models:
        return True
    wanted = (model or "").strip()
    return wanted in {str(item).strip() for item in models}


def _is_image_generation_session(sess, owner: str | None = None) -> bool:
    """Whether this chat session should bypass text chat and generate images.

    Model-name prefixes are explicit image models. Endpoint type is only used
    when the current session endpoint actually matches that image endpoint, and
    when a populated endpoint model cache includes the selected model. This
    prevents an image endpoint on the same host from misrouting ordinary text
    models into the image-generation path.
    """
    model = (getattr(sess, "model", "") or "").strip()
    model_l = model.lower()
    if any(model.lower().startswith(prefix) for prefix in _IMAGE_MODEL_PREFIXES):
        return True
    if "image" in model_l and any(term in model_l for term in ("gemini", "qwen", "flux", "imagen")):
        return True

    endpoint_url = (getattr(sess, "endpoint_url", "") or "").strip()
    if not endpoint_url:
        return False

    db = SessionLocal()
    try:
        q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            from src.auth_helpers import owner_filter
            q = owner_filter(q, ModelEndpoint, owner)
        endpoints = q.all()
        for endpoint in endpoints:
            if (getattr(endpoint, "model_type", None) or "llm") != "image":
                continue
            if not _session_url_matches_endpoint(endpoint_url, getattr(endpoint, "base_url", "") or ""):
                continue
            if _endpoint_cache_contains_model(endpoint, model):
                return True
    except Exception:
        return False
    finally:
        db.close()
    return False


def _is_video_generation_session(sess) -> bool:
    """Whether the selected session model is explicitly a video model."""
    model = (getattr(sess, "model", "") or "").strip().lower()
    if model.startswith("models/"):
        model = model.split("/", 1)[1]
    if not model or "embedding" in model:
        return False
    if "wan2.7-image" in model or "wan2-7-image" in model:
        return False
    if "minimax" in model and "video" in model:
        return True
    return any(hint in model for hint in _VIDEO_MODEL_HINTS) or (
        "video" in model and not model.startswith("gpt-image")
    )


def _looks_like_existing_media_question(message: str) -> bool:
    text = " ".join((message or "").lower().split())
    if not text:
        return False
    media = r"(?:image|picture|pic|photo|photograph|screenshot|screen\s*shot|gallery|photos)"
    reference = r"(?:above|attached|uploaded|shown|visible|this|that|the|previous|last|my)"
    observe = r"(?:see|view|look\s+at|access|inspect|describe|analy[sz]e|read|check|recognize|tell\s+me)"
    if re.search(rf"\b(?:can|could|do|did)\s+you\b.{{0,80}}\b{observe}\b.{{0,120}}\b{media}\b", text):
        return True
    if re.search(rf"\b{observe}\b.{{0,80}}\b{reference}\s+{media}\b", text):
        return True
    if re.search(rf"\b{media}\s+{reference}\b", text) and re.search(r"\b(?:can|could|do|did|what|who|where|why|how|is|are)\b", text):
        return True
    if re.search(rf"\b(?:what(?:'s| is)|who|where|why|how)\b.{{0,120}}\b(?:in|on|inside|shown|visible)\b.{{0,80}}\b{media}\b", text):
        return True
    return False


def _content_part_is_image(part: Any) -> bool:
    if not isinstance(part, dict):
        return False
    ptype = str(part.get("type") or "").lower()
    if ptype in {"image", "image_url", "input_image"}:
        return True
    if "image" in ptype:
        return True
    return any(key in part for key in ("image", "image_url", "input_image"))


def _messages_have_image_content(messages: list | None) -> bool:
    """True when the actual LLM payload contains image content blocks."""
    for item in messages or []:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if isinstance(content, list) and any(_content_part_is_image(part) for part in content):
            return True
    return False


def _resolve_configured_vision_primary_candidate(owner: str | None = None) -> list:
    try:
        from src.settings import get_user_setting, load_settings
        from src.endpoint_resolver import resolve_endpoint_by_id

        settings = load_settings()
        owner_key = owner or ""
        endpoint_id = (get_user_setting("vision_endpoint_id", owner_key, settings.get("vision_endpoint_id", "")) or "").strip()
        model = (get_user_setting("vision_model", owner_key, settings.get("vision_model", "")) or "").strip()
        if not endpoint_id:
            return []
        resolved = resolve_endpoint_by_id(endpoint_id, model, owner=owner)
        return [resolved] if resolved else []
    except Exception as e:
        logger.debug("Could not resolve configured vision primary candidate: %s", e)
        return []


def _resolve_auto_vision_fallback_candidates(owner: str | None = None, limit: int = 3) -> list:
    """Find enabled vision-capable chat endpoints when no explicit chain exists."""
    try:
        from src.endpoint_resolver import resolve_auto_vision_candidates
        return resolve_auto_vision_candidates(owner=owner, limit=limit)
    except Exception as e:
        logger.debug("Could not resolve auto vision fallback candidates: %s", e)
        return []


def _resolve_chat_stream_fallback_candidates(messages: list | None, owner: str | None = None) -> list:
    """Resolve fallbacks for this stream.

    Image-attached turns should try configured vision fallbacks before generic
    chat fallbacks. Otherwise an incompatible regional vision endpoint can 400
    with no usable recovery path even though the user has a working vision
    model configured.
    """
    candidates = []
    if _messages_have_image_content(messages):
        candidates.extend(_resolve_configured_vision_primary_candidate(owner=owner))
        try:
            from src.endpoint_resolver import resolve_vision_fallback_candidates
            candidates.extend(resolve_vision_fallback_candidates(owner=owner))
        except Exception as e:
            logger.debug("Could not resolve vision fallback candidates: %s", e)
        candidates.extend(_resolve_auto_vision_fallback_candidates(owner=owner))
    try:
        from src.endpoint_resolver import resolve_chat_fallback_candidates
        candidates.extend(resolve_chat_fallback_candidates(owner=owner))
    except Exception as e:
        logger.debug("Could not resolve chat fallback candidates: %s", e)
    return candidates


def _should_prefer_vision_fallback(messages: list | None, sess, fallback_candidates: list | None) -> bool:
    """Selected vision model first; otherwise route image turns to vision fallback."""
    if not fallback_candidates or not _messages_have_image_content(messages):
        return False
    try:
        from src.chat_helpers import model_supports_vision
        return not model_supports_vision(
            getattr(sess, "model", "") or "",
            getattr(sess, "endpoint_url", "") or "",
        )
    except Exception:
        return True


def _direct_media_request_for_session(
    sess,
    message: str,
    messages: list | None,
    owner: str | None = None,
) -> tuple[str, str] | None:
    if _looks_like_existing_media_question(message):
        return None
    if _is_video_generation_session(sess):
        return "video", message or ""
    if _is_image_generation_session(sess, owner=owner):
        return "image", message or ""
    return None


async def _generate_direct_media(kind: str, prompt: str, session_id: str, owner: str | None) -> dict:
    if kind == "image":
        from src.ai_interaction import do_generate_image
        from src.runcomfy_media import generate_runcomfy_media, wants_runcomfy_media

        if wants_runcomfy_media(prompt):
            return await generate_runcomfy_media("image", prompt, owner=owner, session_id=session_id)
        return await do_generate_image(prompt, session_id, owner=owner)

    from src.runcomfy_media import generate_runcomfy_media
    media_kind = "music" if kind == "audio" else kind

    return await generate_runcomfy_media(
        media_kind,
        prompt,
        owner=owner,
        session_id=session_id,
    )


def _recover_empty_session_model(sess, session_id: str, owner: str | None = None) -> bool:
    """Re-populate sess.model from the matching endpoint's cached models.

    Covers the window between endpoint setup and the first chat send: the
    picker showed a model in the dropdown but the session record never got
    written (Issue #587 — UI uses the cached endpoint list, not s.model).
    For Codex Subscription, also repairs stale OpenAI API model names such as
    ``gpt-5`` that are not accepted by the Codex-backed ChatGPT account route.
    """
    current_model = (getattr(sess, "model", "") or "").strip()
    endpoint_url = (getattr(sess, "endpoint_url", "") or "").strip()
    is_chatgpt_subscription = False
    if current_model:
        try:
            from src.chatgpt_subscription import is_chatgpt_subscription_base
            is_chatgpt_subscription = is_chatgpt_subscription_base(endpoint_url)
            if not is_chatgpt_subscription:
                return False
        except Exception:
            return False
    db = SessionLocal()
    try:
        # Prefer the endpoint whose base URL matches the session — we know the
        # user already pointed this session at that endpoint, so its first
        # cached model is the most defensible default.
        ep = None
        if getattr(sess, "endpoint_url", ""):
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if owner:
                from src.auth_helpers import owner_filter
                q = owner_filter(q, ModelEndpoint, owner)
            endpoints = q.all()
            for cand in endpoints:
                if _session_url_matches_endpoint(sess.endpoint_url or "", cand.base_url or ""):
                    ep = cand
                    break
        if not ep:
            return False
        if not is_chatgpt_subscription:
            try:
                from src.chatgpt_subscription import is_chatgpt_subscription_base
                is_chatgpt_subscription = is_chatgpt_subscription_base(getattr(ep, "base_url", "") or endpoint_url)
            except Exception:
                is_chatgpt_subscription = False
        try:
            cached = json.loads(ep.cached_models) if isinstance(ep.cached_models, str) else (ep.cached_models or [])
        except Exception as e:
            logger.warning("Failed to parse cached_models for endpoint %r", getattr(ep, "id", "?"), exc_info=e)
            cached = []
        if not cached:
            visible = []
        else:
            try:
                visible = _visible_models(cached, getattr(ep, "hidden_models", None))
            except Exception:
                visible = cached
        if current_model and current_model in {str(item).strip() for item in visible}:
            return False
        if is_chatgpt_subscription:
            live_models = []
            if getattr(ep, "provider_auth_id", None):
                try:
                    from src.chatgpt_subscription import fetch_available_models
                    from src.endpoint_resolver import resolve_endpoint_runtime
                    _base, api_key = resolve_endpoint_runtime(ep, owner=owner)
                    if api_key:
                        live_models = fetch_available_models(api_key)
                        if live_models:
                            ep.cached_models = json.dumps(live_models)
                            db.commit()
                except Exception:
                    live_models = []
            # Codex Subscription recovery must use the live Codex catalog.
            # Cached rows are only trusted above to avoid revalidating a model
            # that is already present in the visible picker list.
            cached = live_models
            if not cached:
                return False
            try:
                visible = _visible_models(cached, getattr(ep, "hidden_models", None))
            except Exception:
                visible = cached
            if current_model and current_model in {str(item).strip() for item in visible}:
                return False
        if not visible:
            return False
        model = visible[0]
        if not isinstance(model, str) or not model.strip():
            return False
        model = model.strip()
        # Persist so the next request, websocket reconnect, or page reload
        # picks up the same model (we'd otherwise re-pick on every send
        # and silently switch on the user if the cached order shifts).
        db_session_q = db.query(DBSession).filter(DBSession.id == session_id)
        if owner:
            db_session_q = db_session_q.filter(DBSession.owner == owner)
        db_session = db_session_q.first()
        if db_session:
            db_session.model = model
            db_session.updated_at = datetime.utcnow()
            db.commit()
        sess.model = model
        logger.info(
            "Recovered session model for %s — picked %r from endpoint %s",
            session_id, model, ep.id,
        )
        return True
    except Exception as e:
        db.rollback()
        logger.warning("Failed to recover empty session model for %s: %s", session_id, e)
        return False
    finally:
        db.close()


def _set_user_time_from_request(request: Request) -> None:
    """Copy browser timezone headers into the per-request context.

    This is intentionally ephemeral: it is used only while building prompts
    and running tools for this request. It is not persisted or logged.
    """
    try:
        tz_offset = request.headers.get("x-tz-offset")
        tz_name = request.headers.get("x-tz-name")
        from src.user_time import clear_user_time_context, set_user_tz_name, set_user_tz_offset

        clear_user_time_context()
        if tz_offset is not None:
            set_user_tz_offset(tz_offset)
        if tz_name:
            set_user_tz_name(tz_name)
    except Exception:
        pass
