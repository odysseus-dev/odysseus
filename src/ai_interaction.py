"""
ai_interaction.py

AI-to-AI interaction tools: pipeline and manage_memory, plus shared model
resolution (_resolve_model), the session-manager singleton, and dispatch_ai_tool.

As part of the tool -> registry migration (#3629), chat_with_model, ask_teacher
and list_models moved to src/agent_tools/model_interaction_tools.py, and
create_session, list_sessions, send_to_session and manage_session moved to
src/agent_tools/session_tools.py. Those modules reuse get_session_manager /
_resolve_model / AI_CHAT_TIMEOUT from here.

These are agent tools — the LLM writes fenced code blocks and they execute
through the standard agent_tools.py pipeline.
"""

import asyncio
import json
import logging
import re
import uuid
import time
from typing import Dict, Optional, Tuple
from urllib.parse import quote, urlparse

from src.constants import GENERATED_IMAGES_DIR

logger = logging.getLogger(__name__)

AI_CHAT_TIMEOUT = 120  # seconds for a single LLM call
MAX_DEBATE_ROUNDS = 5
MAX_PIPELINE_STEPS = 10

GEMINI_IMAGE_MODEL_ALIASES = {
    "gemini-image-pro": "gemini-3-pro-image",
    "models/gemini-image-pro": "gemini-3-pro-image",
    "nano-banana-pro": "gemini-3-pro-image",
    "gemini-pro-image": "gemini-3-pro-image",
}
GEMINI_IMAGE_FALLBACK_MODEL = "gemini-3-pro-image"

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


def set_rag_manager(rag_mgr, personal_docs_mgr=None):
    global _rag_manager, _personal_docs_manager
    _rag_manager = rag_mgr
    _personal_docs_manager = personal_docs_mgr


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, resolve_endpoint_runtime


def _model_ids_from_endpoint_fields(ep: object, *field_names: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for field_name in field_names:
        raw = getattr(ep, field_name, None)
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = raw
        if isinstance(parsed, str):
            values = [part.strip() for part in parsed.replace("\n", ",").split(",") if part.strip()]
        elif isinstance(parsed, (list, tuple, set)):
            values = [str(item).strip() for item in parsed if str(item or "").strip()]
        else:
            values = []
        for value in values:
            key = value.lower()
            if key not in seen:
                seen.add(key)
                out.append(value)
    return out


def _canonical_gemini_image_model(model_id: str) -> str:
    raw = (model_id or "").strip()
    lowered = raw.lower()
    return GEMINI_IMAGE_MODEL_ALIASES.get(lowered, raw)


def _is_gemini_endpoint(base_or_url: str, ep: object | None = None) -> bool:
    text = f"{base_or_url or ''} {getattr(ep, 'name', '') if ep else ''}".lower()
    try:
        host = (urlparse(base_or_url or "").hostname or "").lower()
    except Exception:
        host = ""
    return "generativelanguage.googleapis.com" in host or "gemini" in text or "google" in text


def _is_gemini_image_model(model_id: str) -> bool:
    model = (model_id or "").strip().lower()
    model = GEMINI_IMAGE_MODEL_ALIASES.get(model, model)
    return (
        "gemini" in model and "image" in model
    ) or model.startswith("imagen") or model.startswith("models/imagen")


def _gemini_image_fallback_for_endpoint(ep: object) -> str:
    models = _model_ids_from_endpoint_fields(ep, "cached_models", "pinned_models")
    for model in models:
        if _is_gemini_image_model(model):
            return _canonical_gemini_image_model(model)
    if _is_gemini_endpoint(getattr(ep, "base_url", "") or "", ep):
        return GEMINI_IMAGE_FALLBACK_MODEL
    return ""


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
                pinned_model_ids = _model_ids_from_endpoint_fields(ep, "pinned_models")
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
                        data = r.json()
                        items = data if isinstance(data, list) else (data.get("data") or [])
                        model_ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
                        if not model_ids:
                            model_ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                    else:
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    try:
                        model_ids = json.loads(ep.cached_models or "[]")
                    except Exception:
                        model_ids = []
                if not model_ids:
                    model_ids = _model_ids_from_endpoint_fields(ep, "cached_models")
                model_ids = [str(mid) for mid in model_ids if str(mid or "").strip()]
                for pinned in pinned_model_ids:
                    if pinned.lower() not in {mid.lower() for mid in model_ids}:
                        model_ids.append(pinned)

                # Exact match first
                for mid in model_ids:
                    if mid.lower() == model_name.lower():
                        return build_chat_url(base), mid, headers

                # Partial match — word-boundary aware to avoid false positives
                # e.g. "gpt-5" matches "gpt-5-mini" but NOT "qwen-gpt-5.1-instruct"
                _escaped = re.escape(model_name.lower())
                _boundary_re = re.compile(r'(?:^|[-_/: ])' + _escaped + r'(?:$|[-_/: ])')
                for mid in model_ids:
                    if _boundary_re.search(mid.lower()) or mid.lower() in model_name.lower():
                        return build_chat_url(base), mid, headers

                if _is_gemini_endpoint(base, ep) and _is_gemini_image_model(model_name):
                    return build_chat_url(base), _canonical_gemini_image_model(model_name), headers

        raise ValueError(f"Model '{spec}' not found on any configured endpoint")
    finally:
        db.close()


def _cached_models_for_hint(owner: Optional[str] = None) -> list[tuple[object, list[str]]]:
    from src.database import SessionLocal, ModelEndpoint
    from src.auth_helpers import owner_filter

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        rows: list[tuple[object, list[str]]] = []
        for ep in query.all():
            models = _model_ids_from_endpoint_fields(ep, "cached_models", "pinned_models")
            rows.append((ep, models))
        return rows
    finally:
        db.close()


def _best_cached_model(
    rows: list[tuple[object, list[str]]],
    *,
    endpoint_terms: tuple[str, ...] = (),
    include_terms: tuple[str, ...] = (),
    prefer_terms: tuple[str, ...] = (),
    avoid_terms: tuple[str, ...] = (),
) -> str:
    best: tuple[int, str] | None = None
    endpoint_terms_l = tuple(term.lower() for term in endpoint_terms)
    include_terms_l = tuple(term.lower() for term in include_terms)
    prefer_terms_l = tuple(term.lower() for term in prefer_terms)
    avoid_terms_l = tuple(term.lower() for term in avoid_terms)

    for ep, models in rows:
        endpoint_text = f"{getattr(ep, 'name', '')} {getattr(ep, 'base_url', '')}".lower()
        if endpoint_terms_l and not any(term in endpoint_text for term in endpoint_terms_l):
            continue
        for model in models:
            model_l = model.lower()
            if include_terms_l and not any(term in model_l for term in include_terms_l):
                continue
            score = 10
            score += sum(5 for term in prefer_terms_l if term in model_l)
            score -= sum(4 for term in avoid_terms_l if term in model_l)
            if "latest" in model_l:
                score += 2
            if "preview" in model_l:
                score -= 1
            candidate = (score, model)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best[1] if best else ""


def _image_model_hint_from_prompt(prompt: str, owner: Optional[str] = None) -> str:
    text = f" {prompt or ''} ".lower()
    rows = _cached_models_for_hint(owner=owner)

    if re.search(r"\b(?:gemini|google)\b", text):
        model = _best_cached_model(
            rows,
            endpoint_terms=("google", "gemini", "generativelanguage.googleapis.com"),
            include_terms=("image", "imagen"),
            prefer_terms=("gemini", "flash", "imagen"),
            avoid_terms=("audio", "embedding", "robotics", "live"),
        )
        if model:
            return model
        for ep, _models in rows:
            fallback = _gemini_image_fallback_for_endpoint(ep)
            if fallback:
                return fallback

    if re.search(r"\b(?:imagen)\b", text):
        model = _best_cached_model(
            rows,
            endpoint_terms=("google", "gemini", "generativelanguage.googleapis.com"),
            include_terms=("imagen",),
            prefer_terms=("generate", "ultra", "fast"),
            avoid_terms=("embedding",),
        )
        if model:
            return model

    if re.search(r"\b(?:chatgpt|openai|gpt)\b", text):
        model = _best_cached_model(
            rows,
            endpoint_terms=("openai", "api.openai.com"),
            include_terms=("gpt-image", "dall-e"),
            prefer_terms=("gpt-image",),
        )
        if model:
            return model

    if re.search(r"\b(?:flux|black\s*forest)\b", text):
        model = _best_cached_model(rows, include_terms=("flux",), prefer_terms=("dev", "schnell"))
        if model:
            return model

    if re.search(r"\b(?:qwen\s*image|qwen-image)\b", text):
        model = _best_cached_model(rows, include_terms=("qwen-image",), prefer_terms=("edit",))
        if model:
            return model

    return ""


def _looks_like_image_generation_model(model_id: str) -> bool:
    model = (model_id or "").strip().lower()
    if not model:
        return False
    image_terms = (
        "gpt-image",
        "dall-e",
        "chatgpt-image",
        "flux",
        "qwen-image",
        "imagen",
        "stable-diffusion",
        "stable_diffusion",
        "sdxl",
        "diffusion",
        "kontext",
    )
    if any(term in model for term in image_terms):
        return True
    return "image" in model and any(term in model for term in ("gemini", "qwen", "gpt"))


def _session_selected_image_model(session_id: Optional[str], owner: Optional[str] = None) -> str:
    """Return the current session's selected image model, if it is image-capable."""
    if not session_id:
        return ""
    session = None
    manager = get_session_manager()
    try:
        if manager is not None:
            session = getattr(manager, "sessions", {}).get(session_id)
            if session is None and hasattr(manager, "get_session"):
                session = manager.get_session(session_id)
    except Exception:
        session = None
    if session is None:
        return ""
    sess_owner = (getattr(session, "owner", None) or "").strip()
    if owner and sess_owner and sess_owner != owner:
        return ""
    model = (getattr(session, "model", "") or "").strip()
    return model if _looks_like_image_generation_model(model) else ""


def _is_ollama_image_endpoint_url(url: str) -> bool:
    try:
        from src.llm_core import _is_ollama_native_url, _is_ollama_openai_compat_url, _host_match
        return (
            _is_ollama_native_url(url)
            or _is_ollama_openai_compat_url(url)
            or _host_match(url, "ollama.com")
        )
    except Exception:
        try:
            parsed = urlparse(url or "")
            host = (parsed.hostname or "").lower()
            path = (parsed.path or "").rstrip("/")
            local_host = host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
            return local_host and (parsed.port == 11434 or path.startswith("/api") or path.startswith("/v1"))
        except Exception:
            return False


def _ollama_openai_image_base(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/v1"
    return (url or "").replace("/api/chat", "").rstrip("/") + "/v1"


def _extract_api_key_from_headers(headers: Dict[str, str]) -> str:
    auth = str((headers or {}).get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(
        (headers or {}).get("x-goog-api-key")
        or (headers or {}).get("X-Goog-Api-Key")
        or ""
    ).strip()


def _gemini_api_root_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "generativelanguage.googleapis.com"
    parts = [part for part in (parsed.path or "").split("/") if part]
    version = "v1beta"
    for part in parts:
        if re.fullmatch(r"v\d+(?:beta)?", part):
            version = part
            break
    return f"{scheme}://{netloc}/{version}"


def _gemini_generate_content_url(url: str, model_id: str) -> str:
    model = _canonical_gemini_image_model(model_id).strip()
    if model.startswith("models/"):
        model = model.split("/", 1)[1]
    return f"{_gemini_api_root_from_url(url)}/models/{quote(model, safe='')}:generateContent"


def _aspect_ratio_from_size(size: str) -> str:
    raw = str(size or "").strip().lower()
    if raw in {"auto", ""}:
        return "1:1"
    try:
        width_s, height_s = raw.split("x", 1)
        width = max(1, int(width_s))
        height = max(1, int(height_s))
    except Exception:
        return "1:1"
    if width == height:
        return "1:1"
    return "16:9" if width > height else "9:16"


def _gemini_image_payload(prompt: str, size: str) -> Dict:
    aspect_ratio = _aspect_ratio_from_size(size)
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
                "imageSize": "1K",
            },
        },
    }


def _extract_gemini_image_b64(payload) -> str:
    if isinstance(payload, dict):
        inline = payload.get("inlineData") or payload.get("inline_data")
        if isinstance(inline, dict):
            data = inline.get("data") or inline.get("bytesBase64Encoded") or inline.get("bytes_base64_encoded")
            if isinstance(data, str) and data.strip():
                return data.strip()
        for value in payload.values():
            found = _extract_gemini_image_b64(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_gemini_image_b64(item)
            if found:
                return found
    return ""


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------



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
            url, model, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
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
        query_lower = query.lower()
        exact_results = [m for m in memories if query_lower in (m.get("text", "").lower())]

        if hasattr(_memory_manager, 'get_relevant_memories'):
            vector_results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
        else:
            vector_results = []
        seen = set()
        results = []
        for m in [*exact_results, *vector_results]:
            mid = m.get("id")
            if mid in seen:
                continue
            seen.add(mid)
            results.append(m)
            if len(results) >= 20:
                break

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
      open_panel <name>       — Open a panel (documents, gallery, email, sessions, notes, calendar, tasks, research, workspace, memories, skills, settings, cookbook)
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
            url, model_id, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
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
        # email, sessions, notes, calendar, tasks, research, workspace,
        # memories, skills, settings, cookbook.
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
            "calendar": "calendar",
            "calendars": "calendar",
            "calander": "calendar",
            "calanders": "calendar",
            "calender": "calendar",
            "calenders": "calendar",
            "agenda": "calendar",
            "events": "calendar",
            "tasks": "tasks",
            "task": "tasks",
            "automations": "tasks",
            "automation": "tasks",
            "scheduled": "tasks",
            "research": "research",
            "deep": "research",
            "deepresearch": "research",
            "deep-research": "research",
            "reports": "research",
            "report": "research",
            "workspace": "workspace",
            "workspaces": "workspace",
            "filetree": "workspace",
            "file-tree": "workspace",
            "foldertree": "workspace",
            "folder-tree": "workspace",
            "files": "workspace",
            "folders": "workspace",
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
            return {"error": f"Unknown panel '{panel}'. Valid: documents, gallery, email, sessions, notes, calendar, tasks, research, workspace, memories, skills, settings, cookbook."}
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

    raw_content = (content or "").strip()
    image_args = {}
    if raw_content.startswith("{"):
        try:
            parsed = json.loads(raw_content)
            image_args = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            image_args = {}
    if image_args:
        prompt = str(image_args.get("prompt") or image_args.get("description") or "").strip()
        style = str(image_args.get("style") or "").strip()
        if style and style.lower() not in prompt.lower():
            prompt = (prompt.rstrip(" .") + ". " + style).strip()
        model_spec = str(image_args.get("model") or image_args.get("model_id") or "").strip()
        size = str(image_args.get("size") or "1024x1024").replace("_", "x")
        quality = str(image_args.get("quality") or "").strip().lower()
    else:
        lines = raw_content.split("\n")
        prompt = lines[0].strip() if lines else ""
        model_spec = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
        size = lines[2].strip() if len(lines) > 2 and lines[2].strip() else "1024x1024"
        quality = lines[3].strip() if len(lines) > 3 and lines[3].strip() else ""

    if quality in {"professional", "premium", "hero", "commercial"}:
        quality = "high"
    elif quality in {"draft", "fast", "cheap"}:
        quality = "low"
    elif not quality:
        quality = "high"

    if not prompt:
        return {"error": "Image prompt is required (line 1)"}

    # Load admin settings for defaults
    try:
        from src.settings import load_settings
        _settings = load_settings()
    except Exception:
        _settings = {}

    # Use the explicit/current/mentioned/configured image model if not specified by the tool call.
    # The session picker is authoritative for ordinary prompts; prompt hints only
    # apply when the chat is not already on an image-capable model.
    if not model_spec:
        model_spec = (
            _session_selected_image_model(session_id, owner=owner)
            or _image_model_hint_from_prompt(prompt, owner=owner)
            or _settings.get("image_model", "")
        )
    if (not image_args and len(raw_content.split("\n")) <= 3 or image_args and not image_args.get("quality")) and _settings.get("image_quality"):
        quality = _settings["image_quality"]

    # Auto-detect best available image model if still not set
    if not model_spec:
        for candidate in ("gpt-image-1.5", "gpt-image-1", "dall-e-3"):
            try:
                await asyncio.to_thread(_resolve_model, candidate, owner=owner)
                model_spec = candidate
                break
            except ValueError:
                continue
        if not model_spec:
            for candidate in (
                "gemini-3-pro-image",
                "gemini-3.1-flash-image",
                "gemini-2.5-flash-image",
                "gemini-image-pro",
            ):
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
                            _data = _r.json()
                            _ditems = _data if isinstance(_data, list) else (_data.get("data") or [])
                            _mids = [m.get("id") for m in _ditems if isinstance(m, dict) and m.get("id")]
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
        url, model_id, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
    except ValueError:
        return {"error": f"No endpoint found with image model '{model_spec}'. "
                "Configure an OpenAI-compatible endpoint with image generation support."}

    # Detect if this is a GPT image model vs DALL-E vs local diffusion
    is_gpt_image = "gpt-image" in model_id.lower()
    is_dalle = "dall-e" in model_id.lower()
    is_gemini_image = _is_gemini_endpoint(url) and _is_gemini_image_model(model_id)
    is_ollama_image = _is_ollama_image_endpoint_url(url)
    is_local_diffusion = not is_gpt_image and not is_dalle

    # Build the images endpoint URL from the chat completions URL
    if is_ollama_image:
        base_url = _ollama_openai_image_base(url)
    else:
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
    if is_ollama_image:
        payload["response_format"] = "b64_json"

    # GPT image models and local diffusion endpoints support quality; DALL-E and Ollama do not.
    if is_gpt_image or (is_local_diffusion and not is_ollama_image):
        if quality in ("low", "medium", "high", "auto"):
            payload["quality"] = quality
        else:
            payload["quality"] = "medium"

    logger.info(f"Image generation: model={model_id}, size={size}, quality={quality}, prompt={prompt[:80]}")

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
                quality=payload.get("quality", quality),
                session_id=session_id,
                owner=owner,
            ))
            _gdb.commit()
            _gdb.close()
            return new_id
        except Exception as _ge:
            logger.warning(f"Failed to save gallery record: {_ge}")
            return ""

    def _save_image_bytes(raw: bytes) -> tuple[str, str]:
        img_dir = Path(GENERATED_IMAGES_DIR)
        img_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.png"
        img_path = img_dir / filename
        img_path.write_bytes(raw)
        return f"/api/generated-image/{filename}", _save_to_gallery(filename)

    try:
        # GPT image models can take 30-120s+ depending on quality
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)) as client:
            if is_gemini_image:
                gemini_url = _gemini_generate_content_url(url, model_id)
                gemini_headers = {"Content-Type": "application/json"}
                api_key = _extract_api_key_from_headers(headers)
                if api_key:
                    gemini_headers["x-goog-api-key"] = api_key
                else:
                    gemini_headers.update(headers)
                resp = await client.post(
                    gemini_url,
                    json=_gemini_image_payload(prompt, size),
                    headers=gemini_headers,
                )
            else:
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
            if is_gemini_image:
                image_b64 = _extract_gemini_image_b64(data)
                if not image_b64:
                    return {"error": "Gemini image API returned no inline image data."}
                image_url, image_id = _save_image_bytes(base64.b64decode(image_b64))
                return {
                    "results": f"Generated image for: {prompt[:100]}",
                    "image_url": image_url,
                    "image_id": image_id,
                    "image_prompt": prompt,
                    "image_model": _canonical_gemini_image_model(model_id),
                    "image_size": size,
                    "image_quality": payload.get("quality", quality),
                }

            images = data.get("data", [])
            if not images:
                return {"error": "No images returned from API"}

            img = images[0]
            image_url = None
            image_id = None

            # GPT image models always return b64_json; DALL-E may return url
            if img.get("b64_json"):
                image_url, image_id = _save_image_bytes(base64.b64decode(img.get("b64_json")))

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

    if tool == "pipeline":
        desc = "pipeline: running steps"
        result = await do_pipeline(content, session_id, owner=owner)

    elif tool == "manage_memory":
        action = content.split("\n")[0].strip()[:40]
        desc = f"manage_memory: {action}"
        result = await do_manage_memory(content, session_id, owner=owner)

    elif tool == "ui_control":
        action = content.split("\n")[0].strip()[:60]
        desc = f"ui_control: {action}"
        result = await do_ui_control(content, session_id, owner=owner)

    else:
        desc = f"unknown ai tool: {tool}"
        result = {"error": f"Unknown AI interaction tool: {tool}"}

    return desc, result
