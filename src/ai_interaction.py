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
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

AI_CHAT_TIMEOUT = 120  # seconds for a single LLM call
MAX_DEBATE_ROUNDS = 5
MAX_PIPELINE_STEPS = 10

# ─ Timeouts (seconds) ──────────────────────────────────────────────────
HTTP_TIMEOUT_MODEL_LIST = 5        # Fetching model list from endpoint
HTTP_TIMEOUT_IMAGE_ENDPOINT = 3    # Checking image endpoint availability
HTTP_TIMEOUT_IMAGE_DOWNLOAD = 60   # Downloading DALL-E temp images

# ─ Response Truncation Limits (characters) ────────────────────────────
RESPONSE_TRUNCATE_LIMIT = 10000        # chat_with_model, send_to_session, second_opinion unified
RESPONSE_TRUNCATE_TEACHER = 8000       # ask_teacher, second_opinion review
RESPONSE_TRUNCATE_PIPELINE = 5000      # pipeline step output

# ─ Session & Message Management ───────────────────────────────────────
SESSION_CONTEXT_WINDOW = 15             # Recent messages to include in second_opinion
CONTEXT_MESSAGE_TEXT_LIMIT = 2000       # Max chars per message in context
SESSION_LIST_DISPLAY_LIMIT = 50         # Max sessions to show in list_sessions
SESSION_TRUNCATE_DEFAULT_KEEP = 10      # Default messages to keep when truncating

# ─ Reused Error / Event Strings ───────────────────────────────────────
ERR_NO_SESSION_MANAGER = "Session manager not available"
ERR_NO_ACTION = "No action specified"
ENDPOINT_OFFLINE = "(endpoint offline)"
EVENT_SESSION_CREATED = "session_created"

# ─ ID & Display Limits ────────────────────────────────────────────────
UUID_SHORT_ID_LENGTH = 8                # Length of short session/memory IDs
MEMORY_LIST_DISPLAY_LIMIT = 100         # Max memories to show in list
MEMORY_TEXT_PREVIEW_LIMIT = 150         # Max chars for memory text preview
MEMORY_SEARCH_RESULT_LIMIT = 20         # Max search results to return
IMAGE_PROMPT_LOG_LIMIT = 80             # Max chars of image prompt in logs
ERROR_TEXT_DISPLAY_LIMIT = 500          # Max chars of error details to show

# ─ UI Panel Aliases ───────────────────────────────────────────────────
PANEL_ALIASES = {
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

CANONICAL_PANELS = set(PANEL_ALIASES.values())

# ─ UI Toggle Aliases ──────────────────────────────────────────────────
TOGGLE_ALIASES = {
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

CANONICAL_TOGGLES = {"web", "bash", "research", "incognito", "document_editor"}

# ─ Theme Presets ──────────────────────────────────────────────────────
THEME_PRESETS = {
    "dark", "light", "midnight", "paper", "cyberpunk", "retrowave",
    "forest", "ocean", "ume", "copper", "terminal", "organs",
    "lavender", "gpt", "claude", "cute",
}

# ─ Background Pattern Options ─────────────────────────────────────────
BACKGROUND_PATTERNS = {
    "none", "dots", "synapse", "rain", "constellations",
    "perlin-flow", "petals", "sparkles", "embers"
}

# ─ Color Keys for Theme Customization ─────────────────────────────────
ADVANCED_COLOR_KEYS = {
    "userBubbleBg", "aiBubbleBg", "bubbleBorder", "sidebarBg",
    "sectionAccent", "brandColor", "inputBg", "inputBorder",
    "sendBtnBg", "sendBtnHover", "codeBg", "codeFg",
    "toggleBg", "toggleActive", "accentPrimary", "accentError",
}

# ─ System Prompts ─────────────────────────────────────────────────────
TEACHER_SYSTEM_PROMPT = (
    "You are a senior AI mentor. A less capable model is stuck on a problem and asking for help. "
    "Provide clear, actionable guidance:\n"
    "1. Brief analysis of the problem\n"
    "2. Recommended approach (step by step)\n"
    "3. Key things to watch out for\n\n"
    "Be concise and practical. No preamble."
)

SECOND_OPINION_REVIEWER_SYSTEM = (
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

SECOND_OPINION_UNIFIER_SYSTEM = (
    "Another AI model just reviewed the conversation you've been having with the user. "
    "Read their feedback carefully, then respond with:\n\n"
    "1. **What you agree with** — acknowledge valid points honestly.\n"
    "2. **What you disagree with** — explain why, briefly.\n"
    "3. **Unified version** — produce an updated/refined version of whatever was being discussed, "
    "incorporating the feedback you found valid. Don't accept every note blindly — "
    "use your judgment on what actually improves things vs what's unnecessary.\n\n"
    "Be concise and practical. The user wants a better result, not a meta-discussion."
)

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
# Helper functions
# ---------------------------------------------------------------------------

def truncate_response(text: str, limit: int = RESPONSE_TRUNCATE_LIMIT) -> str:
    """Truncate text to limit, append '... (truncated)' if needed.

    Args:
        text: The text to potentially truncate
        limit: Character limit (default: RESPONSE_TRUNCATE_LIMIT)

    Returns:
        Truncated text with notice, or original if under limit
    """
    if len(text) > limit:
        return text[:limit] + "\n... (truncated)"
    return text


def truncate_for_teacher(text: str) -> str:
    """Truncate response to teacher-specific limit (8000 chars)."""
    return truncate_response(text, RESPONSE_TRUNCATE_TEACHER)


def truncate_for_pipeline(text: str) -> str:
    """Truncate response to pipeline-specific limit (5000 chars)."""
    return truncate_response(text, RESPONSE_TRUNCATE_PIPELINE)


def get_first_line(content: str, max_len: Optional[int] = None) -> str:
    """Extract first line from content, optionally limit length.

    Used for extracting model specs, action names, etc. from multi-line content.

    Args:
        content: The content to parse
        max_len: Optional character limit for the result

    Returns:
        First line of content, stripped, optionally truncated
    """
    line = content.strip().split("\n")[0].strip() if content.strip() else ""
    return line[:max_len] if max_len else line


def get_action_from_content(content: str) -> str:
    """Extract action name (first line) from content."""
    return get_first_line(content, 40)


def get_model_spec_from_content(content: str) -> str:
    """Extract model spec (first line) from content."""
    return get_first_line(content, 60)


def resolve_panel(name: Optional[str]) -> Optional[str]:
    """Resolve panel alias to canonical name.

    Args:
        name: Panel name or alias (case-insensitive)

    Returns:
        Canonical panel name or None if not found
    """
    if not name:
        return None
    name_lower = name.lower()
    # Check if it's an alias first
    if name_lower in PANEL_ALIASES:
        return PANEL_ALIASES[name_lower]
    # Check if it's already canonical
    if name_lower in CANONICAL_PANELS:
        return name_lower
    return None


def resolve_toggle(name: Optional[str]) -> Optional[str]:
    """Resolve toggle alias to canonical name.

    Args:
        name: Toggle name or alias (case-insensitive)

    Returns:
        Canonical toggle name or None if not found
    """
    if not name:
        return None
    name_lower = name.lower()
    # Check if it's an alias first
    if name_lower in TOGGLE_ALIASES:
        return TOGGLE_ALIASES[name_lower]
    # Check if it's already canonical
    if name_lower in CANONICAL_TOGGLES:
        return name_lower
    return None


def is_valid_hex_color(value: str) -> bool:
    """Validate hex color #RRGGBB format.

    Args:
        value: Color string to validate

    Returns:
        True if valid hex color, False otherwise
    """
    import re
    return bool(re.match(r'^#[0-9a-fA-F]{6}$', value))


async def resolve_model_safe(model_spec: str) -> Tuple[Optional[str], Optional[str], Optional[Dict], Optional[Dict]]:
    """Resolve model spec or return error dict.

    Wraps _resolve_model with error handling.

    Args:
        model_spec: Model name or "name@endpoint"

    Returns:
        Tuple of (url, model_id, headers, error_dict)
        On success: (url, model, headers, None)
        On error: (None, None, None, {"error": "message"})
    """
    try:
        url, model, headers = _resolve_model(model_spec)
        return url, model, headers, None
    except ValueError as e:
        return None, None, None, {"error": str(e)}


from contextlib import contextmanager


@contextmanager
def get_db_session():
    """Context manager for database session.

    Usage:
        with get_db_session() as db:
            # use db
            pass
    """
    from src.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Image generation helpers
# ---------------------------------------------------------------------------

def _image_classify_model(model_id: str) -> Dict:
    """Classify a model as gpt-image, dall-e, or local diffusion.

    Returns:
        Dict with boolean flags: is_gpt_image, is_dalle, is_local_diffusion
    """
    is_gpt_image = "gpt-image" in model_id.lower()
    is_dalle = "dall-e" in model_id.lower()
    return {
        "is_gpt_image": is_gpt_image,
        "is_dalle": is_dalle,
        "is_local_diffusion": not is_gpt_image and not is_dalle,
    }


_VALID_GPT_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
_VALID_DALLE3_SIZES = {"1024x1024", "1024x1792", "1792x1024"}
_DEFAULT_IMAGE_SIZE = "1024x1024"


def _image_clamp_size(size: str, *, is_gpt_image: bool, is_dalle: bool) -> str:
    """Clamp size to the allowed values for the given model family.

    Local diffusion models accept any WxH string.
    """
    if is_gpt_image and size not in _VALID_GPT_SIZES:
        return _DEFAULT_IMAGE_SIZE
    if is_dalle and size not in _VALID_DALLE3_SIZES:
        return _DEFAULT_IMAGE_SIZE
    return size


def _image_build_payload(
    model_id: str,
    prompt: str,
    size: str,
    quality: str,
    *,
    is_gpt_image: bool,
    is_dalle: bool,
    is_local_diffusion: bool,
) -> Dict:
    """Build the images/generations API request payload."""
    payload: Dict = {
        "model": model_id,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    # DALL-E 3 does not accept a quality field; others do
    if is_gpt_image or is_local_diffusion:
        payload["quality"] = quality if quality in ("low", "medium", "high", "auto") else "medium"
    return payload


def _image_derive_generations_url(chat_url: str) -> str:
    """Derive the images/generations endpoint URL from the chat completions URL."""
    base = chat_url.replace("/chat/completions", "").replace("/v1/messages", "").rstrip("/")
    return base + "/images/generations"


def _image_auto_detect_model_spec() -> str:
    """Probe configured endpoints and return the first usable image model spec.

    Returns empty string if nothing is found.
    """
    for candidate in ("gpt-image-1.5", "gpt-image-1", "dall-e-3"):
        try:
            _resolve_model(candidate)
            return candidate
        except ValueError:
            continue

    # Fallback: scan image-type endpoints registered in the database
    try:
        import httpx as _req
        from src.database import SessionLocal as _ISL, ModelEndpoint as _IME
        _idb = _ISL()
        try:
            eps = _idb.query(_IME).filter(
                _IME.is_enabled.is_(True),
                _IME.model_type == "image",
            ).all()
            for ep in eps:
                base = ep.base_url.rstrip("/")
                if not base.endswith("/v1"):
                    base += "/v1"
                try:
                    r = _req.get(base + "/models", timeout=HTTP_TIMEOUT_IMAGE_ENDPOINT)
                    r.raise_for_status()
                    model_ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
                    if model_ids:
                        return model_ids[0]
                except Exception:
                    continue
        finally:
            _idb.close()
    except Exception:
        pass

    return ""


def _image_save_to_gallery(
    filename: str, prompt: str, model_id: str, size: str, quality: str,
    session_id: Optional[str], owner: Optional[str],
) -> str:
    """Insert a GalleryImage row and return the new UUID (or '' on failure)."""
    try:
        from src.database import GalleryImage
        new_id = str(uuid.uuid4())
        with get_db_session() as db:
            db.add(GalleryImage(
                id=new_id,
                filename=filename,
                prompt=prompt,
                model=model_id,
                size=size,
                quality=quality,
                session_id=session_id,
                owner=owner,
            ))
            db.commit()
        return new_id
    except Exception as exc:
        logger.warning(f"Failed to save gallery record: {exc}")
        return ""


async def _image_save_b64(
    b64_data: str, prompt: str, model_id: str, size: str, quality: str,
    session_id: Optional[str], owner: Optional[str],
) -> tuple:
    """Decode a base64 image, save to disk, persist gallery record.

    Returns:
        (image_url, image_id)
    """
    import base64
    from pathlib import Path

    img_dir = Path("data/generated_images")
    img_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:12]}.png"
    (img_dir / filename).write_bytes(base64.b64decode(b64_data))
    image_url = f"/api/generated-image/{filename}"
    image_id = _image_save_to_gallery(filename, prompt, model_id, size, quality, session_id, owner)
    return image_url, image_id


async def _image_download_and_save(
    remote_url: str, prompt: str, model_id: str, size: str, quality: str,
    session_id: Optional[str], owner: Optional[str],
) -> tuple:
    """Download an external image URL, save locally, persist gallery record.

    Returns:
        (image_url, image_id) — falls back to remote_url if download fails.
    """
    import httpx
    from pathlib import Path

    try:
        dl = httpx.get(remote_url, timeout=HTTP_TIMEOUT_IMAGE_DOWNLOAD)
        if dl.status_code == 200:
            img_dir = Path("data/generated_images")
            img_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex[:12]}.png"
            (img_dir / filename).write_bytes(dl.content)
            image_url = f"/api/generated-image/{filename}"
            image_id = _image_save_to_gallery(filename, prompt, model_id, size, quality, session_id, owner)
            return image_url, image_id
    except Exception as exc:
        logger.warning(f"Failed to download image: {exc}")

    return remote_url, ""  # Fallback to external URL


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def _pipeline_parse_steps(content: str) -> Tuple[Optional[List[Dict]], Optional[Dict]]:
    """Parse pipeline steps from JSON or pipe-delimited line format.

    Returns:
        (steps, error_dict) — exactly one of them is None.
    """
    stripped = content.strip()
    if not stripped:
        return None, {"error": "No pipeline steps provided"}

    # Try JSON first
    steps = None
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
            steps = data.get("steps") if isinstance(data, dict) else data
        except (ValueError, TypeError):
            pass

    # Fall back to line-based format: model | instruction
    if steps is None:
        steps = []
        for line in stripped.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "|" not in line:
                return None, {"error": "Each line must be: model | instruction (or use JSON format)"}
            model_part, instruction_part = line.split("|", 1)
            steps.append({"model": model_part.strip(), "instruction": instruction_part.strip()})

    if not steps:
        return None, {"error": "No pipeline steps provided"}

    return steps, None


# ---------------------------------------------------------------------------
# Second-opinion helpers
# ---------------------------------------------------------------------------

def _second_opinion_build_context(messages: list) -> str:
    """Build a readable context string from the most recent session messages.

    Returns:
        Formatted string with [ROLE]: text lines, or '' if nothing to show.
    """
    recent = messages[-SESSION_CONTEXT_WINDOW:] if len(messages) > SESSION_CONTEXT_WINDOW else messages
    parts = []
    for m in recent:
        role = m.get("role", "unknown").upper()
        text = m.get("content", "")
        if isinstance(text, list):
            text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
        if text:
            parts.append(f"[{role}]: {text[:CONTEXT_MESSAGE_TEXT_LIMIT]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url, build_headers, build_models_url
from src.llm_core import llm_call_async


def _resolve_model(spec: str) -> Tuple[str, str, Dict]:
    """Resolve a model specifier to (endpoint_url, model_id, headers).

    Accepts:
      "model_name"              — searches all configured endpoints
      "model_name@endpoint_name" — looks up specific endpoint by display name

    Raises ValueError if model not found.
    """
    from src.database import ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS

    spec = spec.strip()
    target_endpoint_name = None

    if "@" in spec:
        model_name, target_endpoint_name = spec.rsplit("@", 1)
        model_name = model_name.strip()
        target_endpoint_name = target_endpoint_name.strip()
    else:
        model_name = spec

    with get_db_session() as db:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled.is_(True))
        if target_endpoint_name:
            query = query.filter(ModelEndpoint.name.ilike(f"%{target_endpoint_name}%"))
        endpoints = query.all()

    if not endpoints:
        raise ValueError(
            "No enabled endpoints found"
            + (f" matching '{target_endpoint_name}'" if target_endpoint_name else "")
        )

    for ep in endpoints:
        base = _normalize_base(ep.base_url)
        provider = _detect_provider(base)
        headers = build_headers(ep.api_key, base)

        model_ids = _fetch_endpoint_model_ids(base, headers, provider, ANTHROPIC_MODELS)
        model_ids = [m for m in model_ids if m != ENDPOINT_OFFLINE]

        for mid in model_ids:
            if mid.lower() == model_name.lower():
                return build_chat_url(base), mid, headers
        for mid in model_ids:
            if model_name.lower() in mid.lower() or mid.lower() in model_name.lower():
                return build_chat_url(base), mid, headers

    raise ValueError(f"Model '{spec}' not found on any configured endpoint")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def do_chat_with_model(content: str, session_id: Optional[str] = None) -> Dict:
    """Send a message to a specific model and return its response.

    Content format:
      Line 1: model_name (or model_name@endpoint_name)
      Line 2+: the message to send
    """

    lines = content.strip().split("\n", 1)
    if not lines or not lines[0].strip():
        return {"error": "First line must be the model name"}

    model_spec = lines[0].strip()
    message = lines[1].strip() if len(lines) > 1 else ""
    if not message:
        return {"error": "No message provided (line 2+ is the message)"}

    try:
        url, model, headers = _resolve_model(model_spec)
    except ValueError as e:
        return {"error": str(e)}

    try:
        response = await llm_call_async(
            url, model,
            [{"role": "user", "content": message}],
            headers=headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        return {"model": model, "response": truncate_response(response)}
    except Exception as e:
        logger.error(f"chat_with_model failed: {e}")
        return {"error": f"Failed to get response from {model_spec}: {e}"}


# Backward-compat alias for modules that import _TEACHER_SYSTEM_PROMPT directly
_TEACHER_SYSTEM_PROMPT = TEACHER_SYSTEM_PROMPT


async def do_ask_teacher(content: str, session_id: Optional[str] = None) -> Dict:
    """Ask a more capable model for help.

    Content format:
      Line 1: model_name (or 'auto')
      Line 2+: the problem description
    """
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
        url, model, headers = _resolve_model(model_spec)
    except ValueError as e:
        return {"error": str(e)}

    try:
        response = await llm_call_async(
            url, model,
            [
                {"role": "system", "content": TEACHER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Problem:\n{problem}"},
            ],
            headers=headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        return {"model": model, "response": truncate_for_teacher(response), "teacher": True}
    except Exception as e:
        logger.error(f"ask_teacher failed: {e}")
        return {"error": f"Teacher call failed ({model_spec}): {e}"}


async def do_second_opinion(content: str, session_id: Optional[str] = None) -> Dict:
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

    lines = content.strip().split("\n", 1)
    if not lines or not lines[0].strip():
        return {"error": "First line must be the model name"}

    model_spec = lines[0].strip()
    focus = lines[1].strip() if len(lines) > 1 else ""

    try:
        reviewer_url, reviewer_model, reviewer_headers = _resolve_model(model_spec)
    except ValueError as e:
        return {"error": str(e)}

    # Pull recent conversation context from the active session
    context_text = ""
    sess = None
    if session_id and _session_manager:
        sess = _session_manager.get_session(session_id)
        if sess:
            context_text = _second_opinion_build_context(sess.get_context_messages())

    if not context_text:
        return {"error": "No conversation context found to review"}

    # ── Step 1: Get the reviewer's feedback ──
    reviewer_message = f"Here's the conversation so far:\n\n{context_text}"
    if focus:
        reviewer_message += f"\n\n---\nSpecifically, I want your take on: {focus}"
    else:
        reviewer_message += "\n\n---\nGive me your honest second opinion on what's being discussed."

    try:
        review = await llm_call_async(
            reviewer_url, reviewer_model,
            [
                {"role": "system", "content": SECOND_OPINION_REVIEWER_SYSTEM},
                {"role": "user", "content": reviewer_message},
            ],
            headers=reviewer_headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        review = truncate_for_teacher(review)
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
                    {"role": "system", "content": SECOND_OPINION_UNIFIER_SYSTEM},
                    {"role": "user", "content": unify_message},
                ],
                headers=original_headers,
                timeout=AI_CHAT_TIMEOUT,
            )
            unified = truncate_response(unified)
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
        return {"error": ERR_NO_SESSION_MANAGER}

    lines = content.strip().split("\n")
    if len(lines) < 2:
        return {"error": "Need 2 lines: session name, then model spec"}

    name = lines[0].strip()
    model_spec = lines[1].strip()

    if not name:
        return {"error": "Session name cannot be empty"}

    try:
        url, model, headers = _resolve_model(model_spec)
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
            fire_event(EVENT_SESSION_CREATED, owner)
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

    Content = optional filter keyword (matches session name).
    """
    if not _session_manager:
        return {"error": ERR_NO_SESSION_MANAGER}

    keyword = content.strip().lower() if content.strip() else None

    try:
        from core.database import Session as DbSession
        from datetime import datetime

        with get_db_session() as db:
            db_rows = {r.id: r for r in db.query(DbSession).all()}

        # SECURITY: scope to the caller's sessions only
        sessions = _session_manager.get_sessions_for_user(owner)
        rows = []
        for sid, sess in sessions.items():
            if keyword and keyword not in (sess.name or "").lower():
                continue
            db_row = db_rows.get(sid)
            ts = _session_best_timestamp(db_row)
            rows.append((ts, sid, sess))

        rows.sort(key=lambda r: r[0] or datetime.min, reverse=True)

        lines = []
        for i, (ts, sid, sess) in enumerate(rows):
            if i >= SESSION_LIST_DISPLAY_LIMIT:
                lines.append(
                    f"... and {len(rows) - SESSION_LIST_DISPLAY_LIMIT} more "
                    f"(showing first {SESSION_LIST_DISPLAY_LIMIT})"
                )
                break
            safe_name = (sess.name or "Untitled").replace("[", "\\[").replace("]", "\\]")
            msg_count = getattr(sess, "message_count", 0) or 0
            model = getattr(sess, "model", "unknown")
            marker = " ← most recent" if i == 0 else ""
            lines.append(
                f"- **[{safe_name}](#session-{sid})** "
                f"(id: `{sid}`, model: {model}, {msg_count} msgs, "
                f"last active {_session_relative_time(ts)}){marker}"
            )

        if not lines:
            suffix = f" matching '{keyword}'" if keyword else ""
            return {"results": f"No sessions found{suffix}."}

        return {
            "results": (
                f"Found {len(rows)} session(s), sorted most-recent first:\n"
                + "\n".join(lines)
                + "\n\nAssistant: when replying to the user, preserve the chat-title markdown links "
                "exactly as shown, e.g. `[Chat](#session-id)`. Do not rewrite as a plain table."
            )
        }
    except Exception as e:
        logger.error(f"list_sessions failed: {e}")
        return {"error": str(e)}


def _session_best_timestamp(db_row) -> Optional[object]:
    """Return the most informative timestamp from a DB session row, or None."""
    if not db_row:
        return None
    return (
        getattr(db_row, "last_accessed", None)
        or getattr(db_row, "updated_at", None)
        or getattr(db_row, "created_at", None)
    )


def _session_relative_time(ts) -> str:
    """Convert a datetime to a human-readable relative string (e.g. '5m ago').

    Args:
        ts: datetime object (naive or tz-aware), or None

    Returns:
        Relative string like 'just now', '5m ago', '2h ago', '3d ago',
        a date string for older entries, or 'never' if ts is None.
    """
    from datetime import datetime, timezone

    if ts is None:
        return "never"

    try:
        # Always use tz-aware comparison; make naive datetimes UTC-aware
        if getattr(ts, "tzinfo", None) is not None:
            now = datetime.now(timezone.utc)
        else:
            ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
        diff = (now - ts).total_seconds()
    except Exception:
        return "unknown"

    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    if diff < 86400 * 7:
        return f"{int(diff / 86400)}d ago"
    return ts.strftime("%Y-%m-%d")


async def do_send_to_session(content: str, session_id: Optional[str] = None) -> Dict:
    """Send a message to an existing session and get a response.

    Content format:
      Line 1: session_id
      Line 2+: message
    """
    from core.models import ChatMessage

    if not _session_manager:
        return {"error": ERR_NO_SESSION_MANAGER}

    lines = content.strip().split("\n", 1)
    if len(lines) < 2:
        return {"error": "Need 2 lines: session_id, then message"}

    target_sid = lines[0].strip()
    message = lines[1].strip()

    sess = _session_manager.get_session(target_sid)
    if not sess:
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
        return {
            "session_id": target_sid,
            "session_name": sess.name,
            "response": truncate_response(response),
        }
    except Exception as e:
        logger.error(f"send_to_session failed: {e}")
        return {"error": f"Failed to send to session: {e}"}


async def stream_ai_tool(tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None):
    """Dispatcher for streaming AI tools. Yields events as async generator."""
    # Fallback: run non-streaming and yield final result
    desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    yield {"_final": True, "desc": desc, "result": result}


async def do_pipeline(content: str, session_id: Optional[str] = None) -> Dict:
    """Execute a multi-step pipeline where each model's output feeds the next.

    Content format (JSON):
      {"steps": [
        {"model": "model_a", "instruction": "Draft an essay about X"},
        {"model": "model_b", "instruction": "Critique the following draft"},
        {"model": "model_a", "instruction": "Revise based on this critique"}
      ]}

    Or pipe-delimited line format:
      model_a | Draft an essay about X
      model_b | Critique the following draft
    """
    steps, parse_error = _pipeline_parse_steps(content)
    if parse_error:
        return parse_error

    if len(steps) > MAX_PIPELINE_STEPS:
        return {"error": f"Maximum {MAX_PIPELINE_STEPS} steps allowed"}

    # Resolve all models first so we fail fast before executing anything
    resolved = []
    for i, step in enumerate(steps):
        model_spec = step.get("model", "").strip()
        instruction = step.get("instruction", "").strip()
        if not model_spec or not instruction:
            return {"error": f"Step {i + 1}: both 'model' and 'instruction' are required"}
        try:
            url, model, headers = _resolve_model(model_spec)
            resolved.append((url, model, headers, instruction))
        except ValueError as e:
            return {"error": f"Step {i + 1}: {e}"}

    # Execute the pipeline, chaining each step's output into the next
    step_outputs = []
    previous_output = None

    try:
        for i, (url, model, headers, instruction) in enumerate(resolved):
            user_content = (
                f"Previous step's output:\n\n{previous_output}\n\nYour task: {instruction}"
                if previous_output else instruction
            )
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
                "output": truncate_for_pipeline(response),
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
    """Manage sessions: list, switch, rename, archive, unarchive, delete, important, truncate, fork.

    Accepts both JSON format ({action, session_id, value}) and line format:
      Line 1: action
      Line 2: target session_id  (or "current" for the active session)
      Line 3+: action-specific value (new name for rename, keep_count for truncate)
    """
    if not _session_manager:
        return {"error": ERR_NO_SESSION_MANAGER}

    action, target_sid, value, list_filter = _parse_manage_session_input(content)

    if not action:
        return {"error": "Missing action (rename|archive|delete|important|truncate|fork|list|switch)"}

    if action == "list":
        return await do_list_sessions(list_filter, session_id, owner=owner)

    if not target_sid:
        return {"error": "Need a session_id (or 'current' for the active chat)"}

    if target_sid.lower() == "current" and session_id:
        target_sid = session_id

    try:
        return await _dispatch_session_action(action, target_sid, value, session_id, owner)
    except Exception as e:
        logger.error(f"manage_session failed: {e}")
        return {"error": str(e)}


def _parse_manage_session_input(content: str) -> Tuple[str, str, Optional[str], str]:
    """Parse do_manage_session content from JSON or line format.

    Returns:
        Tuple of (action, target_sid, value, list_filter) — all str or None.
    """
    raw = (content or "").strip()

    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            action = str(parsed.get("action") or "").strip().lower()
            target_sid = str(
                parsed.get("session_id") or parsed.get("session") or parsed.get("id") or ""
            ).strip()
            v = parsed.get("value")
            if v is None:
                v = (parsed.get("name") or parsed.get("new_name")
                     or parsed.get("title") or parsed.get("keep_count"))
            value = None if v is None else str(v).strip()
            list_filter = str(parsed.get("filter") or "").strip()
            return action, target_sid, value, list_filter

    lines = raw.split("\n")
    if not lines or not lines[0].strip():
        return "", "", None, ""

    action = lines[0].strip().lower()
    target_sid = lines[1].strip() if len(lines) >= 2 else ""
    value = lines[2].strip() if len(lines) >= 3 else None
    list_filter = "\n".join(lines[1:]).strip()
    return action, target_sid, value, list_filter


async def _dispatch_session_action(
    action: str,
    target_sid: str,
    value: Optional[str],
    current_session_id: Optional[str],
    owner: Optional[str],
) -> Dict:
    """Route a validated manage_session action to its handler.

    Raises on unexpected errors; callers should catch Exception.
    """
    def _query(db):
        from src.database import Session as DbSession
        q = db.query(DbSession).filter(DbSession.id == target_sid)
        if owner is not None:
            q = q.filter(DbSession.owner == owner)
        return q

    VIEW_ACTIONS = ("switch", "open", "select", "view")
    if action in VIEW_ACTIONS:
        return _session_action_view(target_sid, _query, action)

    _VALID = "list, switch, rename, archive, unarchive, delete, important, unimportant, truncate, fork"

    with get_db_session() as db:
        db_sess = _query(db).first()

        if action == "rename":
            return _session_action_rename(db, db_sess, target_sid, value)

        if action in ("archive", "unarchive"):
            return _session_action_set_archived(db, db_sess, target_sid, action == "archive")

        if action == "delete":
            return _session_action_delete(db_sess, target_sid, current_session_id)

        if action in ("important", "unimportant"):
            return _session_action_set_important(db, db_sess, target_sid, action == "important")

        if action == "truncate":
            return _session_action_truncate(db_sess, target_sid, value)

        if action == "fork":
            return await _session_action_fork(db_sess, target_sid, value, owner)

        return {"error": f"Unknown action '{action}'. Use: {_VALID}"}


def _session_not_found_error(sid: str) -> Dict:
    """Standard error dict for an unknown session id."""
    return {"error": f"Session '{sid}' not found. Use list_sessions and pass the exact id it returned."}


def _memory_not_found_error(mid: str) -> Dict:
    """Standard error dict for an unknown memory id."""
    return {"error": f"Memory '{mid}' not found"}


def _session_action_view(target_sid: str, query_fn, action: str) -> Dict:
    """Return a clickable link so the user can navigate to the session."""
    with get_db_session() as db:
        db_sess = query_fn(db).first()
        if not db_sess:
            return _session_not_found_error(target_sid)
        name = db_sess.name or target_sid
    return {
        "action": action,
        "session_id": target_sid,
        "name": name,
        "results": f"[{name}](#session-{target_sid}) — click to open.",
    }


def _session_action_rename(db, db_sess, target_sid: str, value: Optional[str]) -> Dict:
    """Rename a session and update the in-memory manager."""
    if not value:
        return {"error": "rename needs a new name (the `value` arg, or line 3 in the legacy format)"}
    if not db_sess:
        return _session_not_found_error(target_sid)
    db_sess.name = value
    db.commit()
    _session_manager.update_session_name(target_sid, value)
    return {"action": "rename", "session_id": target_sid, "name": value,
            "results": f"Session renamed to '{value}'"}


def _session_action_set_archived(db, db_sess, target_sid: str, archived: bool) -> Dict:
    """Archive or unarchive a session."""
    action_word = "archive" if archived else "unarchive"
    if not db_sess:
        return _session_not_found_error(target_sid)
    db_sess.archived = archived
    db.commit()
    past = "archived" if archived else "unarchived"
    return {"action": action_word, "session_id": target_sid,
            "results": f"Session '{db_sess.name}' {past}"}


def _session_action_delete(db_sess, target_sid: str, current_session_id: Optional[str]) -> Dict:
    """Delete a session with safety guards."""
    if target_sid == current_session_id:
        return {"error": "Cannot delete the current session while chatting in it. Delete other sessions first."}
    if not db_sess:
        return {"error": f"Session '{target_sid}' not found. Refusing to delete an unknown chat id; use the exact id from list_sessions."}
    if db_sess.is_important:
        return {"error": f"Session '{db_sess.name}' is starred/favorited. Unstar it first before deleting."}
    try:
        ok = _session_manager.delete_session(target_sid)
        if not ok:
            return {"error": f"Session '{target_sid}' was not deleted because it no longer exists."}
        return {"action": "delete", "session_id": target_sid,
                "results": f"Session '{db_sess.name or target_sid}' deleted"}
    except Exception as e:
        return {"error": f"Failed to delete session: {e}"}


def _session_action_set_important(db, db_sess, target_sid: str, is_important: bool) -> Dict:
    """Star or unstar a session."""
    if not db_sess:
        return _session_not_found_error(target_sid)
    if not is_important and db_sess.is_important:
        return {"error": f"Session '{db_sess.name}' is starred by the user. Only the user can unstar sessions manually."}
    db_sess.is_important = is_important
    db.commit()
    action_word = "important" if is_important else "unimportant"
    status = "marked as important" if is_important else "unmarked as important"
    return {"action": action_word, "session_id": target_sid,
            "results": f"Session '{db_sess.name}' {status}"}


def _session_action_truncate(db_sess, target_sid: str, value: Optional[str]) -> Dict:
    """Truncate a session to the last N messages."""
    if not db_sess:
        return _session_not_found_error(target_sid)
    keep_count = SESSION_TRUNCATE_DEFAULT_KEEP
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


async def _session_action_fork(db_sess, target_sid: str, value: Optional[str], owner: Optional[str]) -> Dict:
    """Fork a session, copying messages into a new session."""
    if not db_sess:
        return _session_not_found_error(target_sid)
    keep_count = 0
    if value:
        try:
            keep_count = int(value)
        except ValueError:
            pass

    source = _session_manager.get_session(target_sid)
    if not source:
        return {"error": f"Session '{target_sid}' not found"}

    new_sid = str(uuid.uuid4())[:UUID_SHORT_ID_LENGTH]
    _session_manager.create_session(
        session_id=new_sid,
        name=f"Fork: {source.name}",
        endpoint_url=source.endpoint_url,
        model=source.model,
        rag=False,
        owner=owner,
    )
    history = source.get_context_messages()
    if keep_count > 0:
        history = history[:keep_count]

    from core.models import ChatMessage as InMemoryMsg
    new_sess = _session_manager.get_session(new_sid)
    for msg in history:
        new_sess.add_message(InMemoryMsg(msg["role"], msg["content"]))

    try:
        from src.event_bus import fire_event
        fire_event(EVENT_SESSION_CREATED, owner)
    except Exception:
        logger.debug("session_created event dispatch failed", exc_info=True)

    return {
        "action": "fork",
        "session_id": new_sid,
        "source_session": target_sid,
        "messages_copied": len(history),
        "results": f"Forked session '{source.name}' -> new session {new_sid} ({len(history)} messages)",
    }


# ---------------------------------------------------------------------------
# Memory management tool
# ---------------------------------------------------------------------------

async def do_manage_memory(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Manage memories: list, add, edit, delete, search.

    Content format:
      Line 1: action (list|add|edit|delete|search)
      Line 2+: action-specific params

    Actions:
      list   [category]      — list memories, optionally filtered by category
      add    <text> [cat]    — add a memory; cat defaults to 'fact'
      edit   <id> <new_text> — update memory text
      delete <id>            — remove a memory
      search <query>         — full-text or vector search
    """
    if not _memory_manager:
        return {"error": "Memory manager not available"}

    lines = content.strip().split("\n") if content.strip() else []
    if not lines:
        return {"error": "Need at least 1 line: action"}

    action = lines[0].strip().lower()

    _HANDLERS = {
        "list": _memory_action_list,
        "add": _memory_action_add,
        "edit": _memory_action_edit,
        "delete": _memory_action_delete,
        "search": _memory_action_search,
    }

    handler = _HANDLERS.get(action)
    if not handler:
        valid = ", ".join(sorted(_HANDLERS))
        return {"error": f"Unknown action '{action}'. Use: {valid}"}

    return handler(lines, owner)


def _memory_action_list(lines: list, owner: Optional[str]) -> Dict:
    """List memories, optionally filtered by category."""
    category_filter = lines[1].strip().lower() if len(lines) > 1 and lines[1].strip() else None
    memories = _memory_manager.load(owner=owner)
    if category_filter:
        memories = [m for m in memories if m.get("category", "").lower() == category_filter]
    if not memories:
        suffix = f" in category '{category_filter}'" if category_filter else ""
        return {"results": f"No memories found{suffix}."}

    result_lines = [f"Found {len(memories)} memory entries:\n"]
    for m in memories[:MEMORY_LIST_DISPLAY_LIMIT]:
        cat = m.get("category", "fact")
        mid = m.get("id", "?")[:UUID_SHORT_ID_LENGTH]
        text = m.get("text", "")
        if len(text) > MEMORY_TEXT_PREVIEW_LIMIT:
            text = text[:MEMORY_TEXT_PREVIEW_LIMIT] + "..."
        result_lines.append(f"- [{cat}] `{mid}` — {text}")
    if len(memories) > MEMORY_LIST_DISPLAY_LIMIT:
        result_lines.append(f"... and {len(memories) - MEMORY_LIST_DISPLAY_LIMIT} more")
    return {"results": "\n".join(result_lines)}


def _memory_action_add(lines: list, owner: Optional[str]) -> Dict:
    """Add a new memory entry."""
    if len(lines) < 2 or not lines[1].strip():
        return {"error": "Add needs line 2: memory text"}
    text = lines[1].strip()
    category = lines[2].strip().lower() if len(lines) > 2 and lines[2].strip() else "fact"

    entry = _memory_manager.add_entry(text, source="ai_agent", category=category, owner=owner)
    memories = _memory_manager.load_all()
    memories.append(entry)
    _memory_manager.save(memories)

    _memory_vector_add(entry["id"], text)
    _fire_memory_event("memory_added", owner)

    return {"action": "add", "memory_id": entry["id"],
            "results": f"Memory added: [{category}] {text}"}


def _memory_action_edit(lines: list, owner: Optional[str]) -> Dict:
    """Edit an existing memory by (partial) ID."""
    if len(lines) < 3 or not lines[2].strip():
        return {"error": "Edit needs line 2: memory_id, line 3: new text"}
    memory_id = lines[1].strip()
    new_text = lines[2].strip()

    memories = _memory_manager.load_all()
    full_id = None
    for m in memories:
        if m.get("id", "").startswith(memory_id):
            if owner and m.get("owner") != owner:
                return _memory_not_found_error(memory_id)
            m["text"] = new_text
            m["timestamp"] = int(time.time())
            full_id = m["id"]
            break

    if not full_id:
        return _memory_not_found_error(memory_id)

    _memory_manager.save(memories)
    _memory_vector_add(full_id, new_text)
    return {"action": "edit", "memory_id": memory_id,
            "results": f"Memory updated: {new_text}"}


def _memory_action_delete(lines: list, owner: Optional[str]) -> Dict:
    """Delete a memory by (partial) ID."""
    if len(lines) < 2 or not lines[1].strip():
        return {"error": "Delete needs line 2: memory_id"}
    memory_id = lines[1].strip()

    memories = _memory_manager.load_all()
    original_len = len(memories)
    full_id = None

    for m in memories:
        if m.get("id", "").startswith(memory_id):
            if owner and m.get("owner") != owner:
                return _memory_not_found_error(memory_id)
            full_id = m["id"]
            break

    memories = [m for m in memories if m.get("id") != full_id]
    if len(memories) == original_len:
        return _memory_not_found_error(memory_id)

    _memory_manager.save(memories)
    _memory_vector_remove(full_id)
    return {"action": "delete", "memory_id": memory_id,
            "results": f"Memory '{memory_id}' deleted"}


def _memory_action_search(lines: list, owner: Optional[str]) -> Dict:
    """Search memories by text or vector similarity."""
    if len(lines) < 2 or not lines[1].strip():
        return {"error": "Search needs line 2: query"}
    query = lines[1].strip()
    memories = _memory_manager.load(owner=owner)

    if hasattr(_memory_manager, "get_relevant_memories"):
        results = _memory_manager.get_relevant_memories(
            query, memories, threshold=0.05, max_items=MEMORY_SEARCH_RESULT_LIMIT
        )
    else:
        query_lower = query.lower()
        results = [m for m in memories if query_lower in m.get("text", "").lower()][:MEMORY_SEARCH_RESULT_LIMIT]

    if not results:
        return {"results": f"No memories found matching '{query}'."}

    result_lines = [f"Found {len(results)} matching memories:\n"]
    for m in results:
        cat = m.get("category", "fact")
        mid = m.get("id", "?")[:UUID_SHORT_ID_LENGTH]
        text = m.get("text", "")
        result_lines.append(f"- [{cat}] `{mid}` — {text}")
    return {"results": "\n".join(result_lines)}


def _memory_vector_add(memory_id: str, text: str) -> None:
    """Add or update an entry in the memory vector index (best-effort)."""
    if _memory_vector and hasattr(_memory_vector, "healthy") and _memory_vector.healthy:
        try:
            _memory_vector.add(memory_id, text)
        except Exception:
            pass


def _memory_vector_remove(memory_id: Optional[str]) -> None:
    """Remove an entry from the memory vector index (best-effort)."""
    if memory_id and _memory_vector and hasattr(_memory_vector, "healthy") and _memory_vector.healthy:
        try:
            _memory_vector.remove(memory_id)
        except Exception:
            pass


def _fire_memory_event(event: str, owner: Optional[str]) -> None:
    """Emit an event on the event bus (best-effort, non-blocking)."""
    try:
        from src.event_bus import fire_event
        fire_event(event, owner)
    except Exception:
        logger.debug("memory event dispatch failed", exc_info=True)


# ---------------------------------------------------------------------------
# List models tool
# ---------------------------------------------------------------------------

async def do_list_models(content: str, session_id: Optional[str] = None) -> Dict:
    """List all available models across configured endpoints.

    Content = optional filter keyword.
    """
    from src.database import ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS

    keyword = content.strip().lower() if content.strip() else None

    try:
        with get_db_session() as db:
            endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled.is_(True)).all()

        if not endpoints:
            return {"results": "No enabled model endpoints configured."}

        result_lines = []
        total_models = 0

        for ep in endpoints:
            base = _normalize_base(ep.base_url)
            provider = _detect_provider(base)
            headers = build_headers(ep.api_key, base)

            model_ids = _fetch_endpoint_model_ids(base, headers, provider, ANTHROPIC_MODELS)

            if keyword:
                model_ids = [
                    m for m in model_ids
                    if keyword in m.lower() or keyword in (ep.name or "").lower()
                ]

            if model_ids:
                result_lines.append(f"\n**{ep.name or base}** ({provider}):")
                for mid in model_ids:
                    result_lines.append(f"  - `{mid}`")
                    total_models += 1

        if not result_lines:
            return {"results": "No models found" + (f" matching '{keyword}'" if keyword else "") + "."}

        return {"results": f"Available models ({total_models} total):" + "\n".join(result_lines)}

    except Exception as e:
        logger.error(f"list_models failed: {e}")
        return {"error": str(e)}


def _fetch_endpoint_model_ids(base: str, headers: Dict, provider: str, anthropic_models) -> list:
    """Probe an endpoint and return its model IDs.

    Returns a list of model ID strings, or ["(endpoint offline)"] on failure.
    """
    import httpx

    if provider == "anthropic":
        return list(anthropic_models)

    try:
        r = httpx.get(build_models_url(base), headers=headers, timeout=HTTP_TIMEOUT_MODEL_LIST)
        r.raise_for_status()
        data = r.json()
        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if not model_ids:
            model_ids = [
                m.get("name") or m.get("model")
                for m in (data.get("models") or [])
                if m.get("name") or m.get("model")
            ]
        return model_ids or [ENDPOINT_OFFLINE]
    except Exception:
        return [ENDPOINT_OFFLINE]


# ---------------------------------------------------------------------------
# RAG management tool
# ---------------------------------------------------------------------------

async def do_manage_rag(content: str, session_id: Optional[str] = None) -> Dict:
    """Manage RAG indexed documents: list, add_directory, remove_directory.

    Content format:
      Line 1: action (list|add_directory|remove_directory)
      Line 2: directory path (for add/remove)
    """
    lines = content.strip().split("\n") if content.strip() else []
    if not lines:
        return {"error": ERR_NO_ACTION}

    action = lines[0].strip().lower()

    _HANDLERS = {
        "list": _rag_action_list,
        "add_directory": _rag_action_add_directory,
        "remove_directory": _rag_action_remove_directory,
    }

    handler = _HANDLERS.get(action)
    if not handler:
        valid = ", ".join(sorted(_HANDLERS))
        return {"error": f"Unknown action '{action}'. Use: {valid}"}

    return handler(lines)


def _rag_action_list(lines: list) -> Dict:
    """List all indexed RAG files and directories."""
    if not _personal_docs_manager:
        return {"results": "Personal docs manager not available. RAG may not be configured."}
    try:
        files = getattr(_personal_docs_manager, "index", None) or []
        dirs = []
        if hasattr(_personal_docs_manager, "get_indexed_directories"):
            dirs = _personal_docs_manager.get_indexed_directories()

        result_lines = []
        if dirs:
            result_lines.append(f"**Indexed directories ({len(dirs)}):**")
            result_lines.extend(f"  - `{d}`" for d in dirs)
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


def _rag_action_add_directory(lines: list) -> Dict:
    """Index a directory into RAG."""
    import os

    if len(lines) < 2 or not lines[1].strip():
        return {"error": "add_directory needs line 2: directory path"}
    directory = os.path.expanduser(lines[1].strip())

    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory}"}
    if not _rag_manager:
        return {"error": "RAG manager not available"}

    try:
        result = _rag_manager.index_personal_documents(directory)
        indexed = result.get("indexed", 0) if isinstance(result, dict) else 0
        return {
            "action": "add_directory",
            "directory": directory,
            "results": f"Directory '{directory}' added to RAG index ({indexed} files indexed)",
        }
    except Exception as e:
        return {"error": f"Failed to index directory: {e}"}


def _rag_action_remove_directory(lines: list) -> Dict:
    """Remove a directory from the RAG index and rebuild."""
    if len(lines) < 2 or not lines[1].strip():
        return {"error": "remove_directory needs line 2: directory path"}
    if not _personal_docs_manager:
        return {"error": "Personal docs manager not available"}

    directory = lines[1].strip()
    try:
        if hasattr(_personal_docs_manager, "remove_directory"):
            _personal_docs_manager.remove_directory(directory)
        if _rag_manager and hasattr(_rag_manager, "rebuild_index"):
            _rag_manager.rebuild_index()
        return {
            "action": "remove_directory",
            "directory": directory,
            "results": f"Directory '{directory}' removed from RAG index",
        }
    except Exception as e:
        return {"error": f"Failed to remove directory: {e}"}


# ---------------------------------------------------------------------------
# UI control tool (returns events for frontend to apply)
# ---------------------------------------------------------------------------

async def do_ui_control(content: str, session_id: Optional[str] = None) -> Dict:
    """Control frontend UI: toggle settings, switch model, change theme.

    Content format:
      Line 1: action
      Line 2+: action-specific params

    Actions:
      toggle <name> <on|off>  — Toggle a setting (web, bash, research, incognito, document_editor)
      set_mode <agent|chat>   — Switch between agent and chat mode
      switch_model <model>    — Change the model for the current session
      set_theme <preset>      — Apply a theme preset (dark, light, paper, cyberpunk, etc.)
      create_theme <name> <bg> <fg> <panel> <border> <accent> [key=val ...]
      open_panel <name>       — Open a panel (documents, gallery, email, sessions, notes, etc.)
      open_email_reply <uid> [folder] [reply|reply-all|ai-reply]
      highlight <selector> [label] — Highlight a UI element
      clear_highlight         — Clear all highlights
      get_toggles             — Return info about available toggles
    """
    stripped = content.strip()
    if not stripped:
        return {"error": ERR_NO_ACTION}

    lines = stripped.split("\n")
    parts = lines[0].strip().split(None, 2)
    if not parts:
        return {"error": ERR_NO_ACTION}

    action = parts[0].lower()

    _HANDLERS = {
        "toggle": _ui_handle_toggle,
        "set_mode": _ui_handle_set_mode,
        "set_theme": _ui_handle_set_theme,
        "create_theme": _ui_handle_create_theme,
        "highlight": _ui_handle_highlight,
        "clear_highlight": _ui_handle_clear_highlight,
        "open_panel": _ui_handle_open_panel,
        "open_email_reply": _ui_handle_open_email_reply,
        "get_toggles": _ui_handle_get_toggles,
    }

    if action == "switch_model":
        return await _ui_handle_switch_model(parts, lines, session_id)

    handler = _HANDLERS.get(action)
    if handler:
        return handler(parts, lines)

    valid = sorted(_HANDLERS.keys()) + ["switch_model"]
    return {"error": f"Unknown action '{action}'. Use: {', '.join(valid)}"}


def _ui_handle_toggle(parts: list, lines: list) -> Dict:
    """Handle the toggle action — enable/disable a UI feature."""
    if len(parts) < 3:
        return {"error": "toggle needs: toggle <name> <on|off>"}

    raw_name = parts[1].lower()
    toggle_name = resolve_toggle(raw_name)
    if not toggle_name or toggle_name not in CANONICAL_TOGGLES:
        return {"error": f"Unknown toggle '{raw_name}'. Valid: {', '.join(sorted(CANONICAL_TOGGLES))}"}

    state = parts[2].lower() in ("on", "true", "1", "yes", "enable", "enabled")
    return {
        "ui_event": "toggle",
        "toggle_name": toggle_name,
        "state": state,
        "results": f"Toggle '{toggle_name}' set to {'on' if state else 'off'}",
    }


def _ui_handle_set_mode(parts: list, lines: list) -> Dict:
    """Handle the set_mode action — switch between agent and chat mode."""
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


async def _ui_handle_switch_model(parts: list, lines: list, session_id: Optional[str]) -> Dict:
    """Handle switch_model — validate and apply a model change to the current session."""
    model_spec = " ".join(parts[1:]) if len(parts) > 1 else ""
    if not model_spec:
        model_spec = lines[1].strip() if len(lines) > 1 else ""
    if not model_spec:
        return {"error": "switch_model needs a model name"}

    try:
        url, model_id, headers = _resolve_model(model_spec)
    except ValueError as e:
        return {"error": str(e)}

    if session_id and _session_manager:
        from src.database import Session as _DbSess
        with get_db_session() as db:
            db_s = db.query(_DbSess).filter(_DbSess.id == session_id).first()
            if db_s:
                db_s.endpoint_url = url
                db_s.model = model_id
                db.commit()

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


def _ui_handle_set_theme(parts: list, lines: list) -> Dict:
    """Handle set_theme — apply a named theme preset or custom theme."""
    theme_name = parts[1].lower() if len(parts) > 1 else ""
    if not theme_name:
        return {"error": "set_theme needs a theme name"}

    custom_themes = {}
    try:
        from routes.prefs_routes import _load as _load_prefs
        custom_themes = _load_prefs().get("custom-themes", {}) or {}
    except Exception:
        pass

    all_known = THEME_PRESETS | set(custom_themes.keys())
    if theme_name not in all_known:
        custom_label = f" | Custom: {', '.join(sorted(custom_themes.keys()))}" if custom_themes else ""
        return {"error": f"Unknown theme '{theme_name}'. Available: {', '.join(sorted(THEME_PRESETS))}{custom_label}"}

    return {
        "ui_event": "set_theme",
        "theme_name": theme_name,
        "results": f"Theme changed to '{theme_name}'",
    }


def _ui_handle_create_theme(parts: list, lines: list) -> Dict:
    """Handle create_theme — create a custom named theme with colors and optional effects."""
    all_parts = lines[0].strip().split()
    if len(all_parts) < 7:
        return {"error": (
            "create_theme needs: create_theme <name> <bg> <fg> <panel> <border> <accent> "
            "(all hex colors). Optional advanced color key=value pairs and background EFFECTS: "
            f"bgPattern=<{'|'.join(sorted(BACKGROUND_PATTERNS))}>, "
            "bgEffectColor=#RRGGBB, bgEffectIntensity=<num>, bgEffectSize=<num>, frosted=true|false"
        )}

    name = all_parts[1].lower().replace(" ", "-")
    base_color_keys = ("bg", "fg", "panel", "border", "red")
    colors = dict(zip(base_color_keys, all_parts[2:7]))

    for key, val in colors.items():
        if not is_valid_hex_color(val):
            return {"error": f"Invalid hex color for {key}: '{val}'. Use format #RRGGBB"}

    advanced, bg = {}, {}
    for token in all_parts[7:]:
        if "=" not in token:
            continue
        key, val = token.split("=", 1)
        err = _ui_parse_theme_token(key, val, advanced, bg)
        if err:
            return err

    if advanced:
        colors["advanced"] = advanced

    effect_label = bg.get("pattern", "frosted" if bg.get("frosted") else "custom") if bg else None
    return {
        "ui_event": "create_theme",
        "theme_name": name,
        "colors": colors,
        "bg": bg or None,
        "results": (
            f"Custom theme '{name}' created and applied"
            + (f" with {len(advanced)} advanced overrides" if advanced else "")
            + (f" + background effect ({effect_label})" if bg else "")
        ),
    }


def _ui_parse_theme_token(key: str, val: str, advanced: dict, bg: dict) -> Optional[Dict]:
    """Parse one key=value token from create_theme. Mutates advanced/bg dicts.

    Returns an error dict if invalid, or None if the token was consumed correctly.
    """
    if key in ADVANCED_COLOR_KEYS:
        if not is_valid_hex_color(val):
            return {"error": f"Invalid hex color for advanced key {key}: '{val}'. Use format #RRGGBB"}
        advanced[key] = val

    elif key == "bgPattern":
        if val not in BACKGROUND_PATTERNS:
            return {"error": f"Invalid bgPattern '{val}'. Use one of: {', '.join(sorted(BACKGROUND_PATTERNS))}"}
        bg["pattern"] = val

    elif key == "bgEffectColor":
        if not is_valid_hex_color(val):
            return {"error": f"Invalid hex color for bgEffectColor: '{val}'. Use format #RRGGBB"}
        bg["effectColor"] = val

    elif key in ("bgEffectIntensity", "bgEffectSize"):
        try:
            dest = "effectIntensity" if key == "bgEffectIntensity" else "effectSize"
            bg[dest] = float(val)
        except ValueError:
            return {"error": f"Invalid number for {key}: '{val}'"}

    elif key == "frosted":
        bg["frosted"] = val.lower() in ("true", "1", "yes", "on")

    return None


def _ui_handle_highlight(parts: list, lines: list) -> Dict:
    """Handle highlight — mark a CSS selector for visual emphasis."""
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


def _ui_handle_clear_highlight(parts: list, lines: list) -> Dict:
    """Handle clear_highlight — remove all visual highlights."""
    return {
        "ui_event": "clear_highlight",
        "results": "Highlights cleared",
    }


def _ui_handle_open_panel(parts: list, lines: list) -> Dict:
    """Handle open_panel — navigate to a top-level panel in the UI."""
    raw = parts[1].lower() if len(parts) > 1 else ""
    target = resolve_panel(raw)
    if not target:
        return {"error": f"Unknown panel '{raw}'. Valid: {', '.join(sorted(CANONICAL_PANELS))}."}
    return {
        "ui_event": "open_panel",
        "panel": target,
        "results": f"Opening {target} panel",
    }


def _ui_handle_open_email_reply(parts: list, lines: list) -> Dict:
    """Handle open_email_reply — open a pre-populated reply draft for an email."""
    tokens = lines[0].strip().split()
    uid = tokens[1].strip() if len(tokens) > 1 else ""
    folder = tokens[2].strip() if len(tokens) > 2 else "INBOX"
    mode = tokens[3].strip().lower() if len(tokens) > 3 else "reply"

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


def _ui_handle_get_toggles(parts: list, lines: list) -> Dict:
    """Handle get_toggles — return info about available UI toggles."""
    return {
        "results": (
            "Toggle states are managed client-side in localStorage. "
            f"Available toggles: {', '.join(sorted(CANONICAL_TOGGLES))}. "
            "Use 'toggle <name> <on|off>' to change them."
        )
    }


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

async def do_generate_image(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Generate an image using an image-capable model (e.g. gpt-image-1).

    Content format:
      Line 1: prompt describing the image
      Line 2: model name (optional, auto-detects if omitted)
      Line 3: size (optional, defaults to 1024x1024)
      Line 4: quality (optional: low, medium, high, auto — defaults to medium)
    """
    import httpx

    lines = content.strip().split(chr(10)) if content.strip() else []
    prompt = lines[0].strip() if lines else ""
    if not prompt:
        return {"error": "Image prompt is required (line 1)"}

    model_spec = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
    size = lines[2].strip() if len(lines) > 2 and lines[2].strip() else _DEFAULT_IMAGE_SIZE
    quality = lines[3].strip() if len(lines) > 3 and lines[3].strip() else "medium"

    # Apply admin-configured defaults when caller did not specify
    try:
        from src.settings import load_settings
        settings = load_settings()
    except Exception:
        settings = {}

    if not model_spec:
        model_spec = settings.get("image_model", "")
    if quality == "medium" and settings.get("image_quality"):
        quality = settings["image_quality"]

    # Auto-detect the best available image model
    if not model_spec:
        model_spec = _image_auto_detect_model_spec()
    if not model_spec:
        return {"error": "No image model found. Configure one in Admin → Image Generation."}

    try:
        url, model_id, headers = _resolve_model(model_spec)
    except ValueError:
        return {"error": f"No endpoint found with image model '{model_spec}'. "
                "Configure an OpenAI-compatible endpoint with image generation support."}

    model_kind = _image_classify_model(model_id)
    size = _image_clamp_size(size, **{k: model_kind[k] for k in ("is_gpt_image", "is_dalle")})
    payload = _image_build_payload(model_id, prompt, size, quality, **model_kind)
    images_url = _image_derive_generations_url(url)
    effective_quality = payload.get("quality", "medium")

    logger.info(
        f"Image generation: model={model_id}, size={size}, quality={quality}, "
        f"prompt={prompt[:IMAGE_PROMPT_LOG_LIMIT]}"
    )

    try:
        # GPT image models can take 30-120s+
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        ) as client:
            resp = await client.post(images_url, json=payload, headers=headers)

        if resp.status_code != 200:
            error_text = resp.text[:ERROR_TEXT_DISPLAY_LIMIT]
            try:
                err_obj = resp.json().get("error", error_text)
                error_text = err_obj.get("message", error_text) if isinstance(err_obj, dict) else str(err_obj)
            except Exception:
                pass
            return {"error": f"Image generation failed ({resp.status_code}): {error_text}"}

        images = resp.json().get("data", [])
        if not images:
            return {"error": "No images returned from API"}

        img = images[0]

        if img.get("b64_json"):
            image_url, image_id = await _image_save_b64(
                img["b64_json"], prompt, model_id, size, effective_quality, session_id, owner
            )
        elif img.get("url"):
            image_url, image_id = await _image_download_and_save(
                img["url"], prompt, model_id, size, effective_quality, session_id, owner
            )
        else:
            return {"error": "Image API returned unexpected format (no b64_json or url)"}

        return {
            "results": f"Generated image for: {prompt[:100]}",
            "image_url": image_url,
            "image_id": image_id,
            "image_prompt": prompt,
            "image_model": model_id,
            "image_size": size,
            "image_quality": effective_quality,
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
    """Dispatch an AI interaction tool. Returns (description, result_dict).

    Each entry in _TOOL_REGISTRY is:
        tool_name -> (handler_coro, desc_fn)
    where desc_fn(content) produces the human-readable description string.
    """
    # Registry: tool name → (coroutine_factory, description_factory)
    # desc_fn receives raw content and returns a short label for the UI.
    _TOOL_REGISTRY = {
        "chat_with_model": (
            lambda c: do_chat_with_model(c, session_id),
            lambda c: f"chat_with_model: {get_first_line(c, 60)}",
        ),
        "ask_teacher": (
            lambda c: do_ask_teacher(c, session_id),
            lambda c: f"ask_teacher: {get_first_line(c, 60)}",
        ),
        "second_opinion": (
            lambda c: do_second_opinion(c, session_id),
            lambda c: f"second_opinion: {get_first_line(c, 60)}",
        ),
        "create_session": (
            lambda c: do_create_session(c, session_id, owner=owner),
            lambda c: f"create_session: {get_first_line(c, 60)}",
        ),
        "list_sessions": (
            lambda c: do_list_sessions(c, session_id, owner=owner),
            lambda c: f"list_sessions{': ' + c.strip()[:40] if c.strip() else ''}",
        ),
        "send_to_session": (
            lambda c: do_send_to_session(c, session_id),
            lambda c: f"send_to_session: {get_first_line(c, 20)}",
        ),
        "manage_session": (
            lambda c: do_manage_session(c, session_id, owner=owner),
            lambda c: f"manage_session: {get_action_from_content(c)}",
        ),
        "pipeline": (
            lambda c: do_pipeline(c, session_id),
            lambda c: "pipeline: running steps",
        ),
        "manage_memory": (
            lambda c: do_manage_memory(c, session_id, owner=owner),
            lambda c: f"manage_memory: {get_action_from_content(c)}",
        ),
        "list_models": (
            lambda c: do_list_models(c, session_id),
            lambda c: f"list_models{': ' + c.strip()[:40] if c.strip() else ''}",
        ),
        "ui_control": (
            lambda c: do_ui_control(c, session_id),
            lambda c: f"ui_control: {get_first_line(c, 60)}",
        ),
        "generate_image": (
            lambda c: do_generate_image(c, session_id, owner=owner),
            lambda c: f"generate_image: {get_first_line(c, 60)}",
        ),
    }

    if tool not in _TOOL_REGISTRY:
        return f"unknown ai tool: {tool}", {"error": f"Unknown AI interaction tool: {tool}"}

    handler_factory, desc_factory = _TOOL_REGISTRY[tool]
    desc = desc_factory(content)
    result = await handler_factory(content)
    return desc, result
